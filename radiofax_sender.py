#!/usr/bin/env python3
"""Transmit images using the extensible Radio Image Framing Protocol (RIFP).

The default radio profile uses packetised continuous-phase binary FSK and is
suitable for permitted short-range-device allocations such as 433.92 MHz.
RIFP itself is independent of frequency and modulation.

A TX-capable SoapySDR device is required for RF transmission.  The --iq-output
mode writes interleaved complex64 IQ samples for loopback and conformance tests.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import math
import os
import random
import secrets
import sys
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageOps

from rifp_protocol import (
    ENCODING_CCITT_GROUP3,
    ENCODING_CCITT_GROUP4,
    ENCODING_JPEG,
    ENCODING_PNG,
    ENCODING_RAW,
    ENCODING_RLE,
    ENCODING_ZLIB,
    END_PAYLOAD,
    FLAG_RETRANSMISSION,
    FRAME_DATA,
    FRAME_END,
    FRAME_MANIFEST,
    FRAME_OBJECT_DESCRIPTOR,
    HeaderExtension,
    ObjectDescriptor,
    PIXEL_GRAY1,
    PIXEL_GRAY2,
    PIXEL_GRAY4,
    PIXEL_GRAY8,
    RADIO_PROFILE_CPFSK_4800,
    TLV_CONTENT_HINT,
    TLV_RADIO_PROFILE,
    TLV_SENDER_ID,
    VERSION_MAJOR,
    VERSION_MINOR,
    build_air_frame,
    utf8_extension,
)

CODECS = ("auto", "group4", "group3", "png", "jpeg", "raw", "rle", "zlib")
IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
PRESET_WIDTHS = {"tiny": 96, "small": 160, "medium": 320, "large": 640, "full": None}


@dataclass(frozen=True)
class EncodedImage:
    source: Path
    codec: str
    payload: bytes
    width: int
    height: int
    bits_per_pixel: int
    filename: str
    raw_size: int


@dataclass(frozen=True)
class TransmissionFrame:
    frame_type: int
    sequence: int
    total: int
    payload: bytes
    flags: int = 0


def parse_number(value: str) -> float:
    """Parse values such as 433.92M, 25k, or 0.5."""
    value = value.strip().lower().replace("hz", "")
    multipliers = {"k": 1e3, "m": 1e6, "g": 1e9}
    if value and value[-1] in multipliers:
        return float(value[:-1]) * multipliers[value[-1]]
    return float(value)


def parse_device_args(text: str) -> dict[str, str]:
    if not text:
        return {}
    text = text.strip()
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("JSON device arguments must be an object")
        return {str(k): str(v) for k, v in parsed.items()}
    result: dict[str, str] = {}
    for item in text.split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise ValueError(f"invalid device argument {item!r}; expected key=value")
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def parse_manifest_extensions(values: Sequence[str]) -> dict[str, object]:
    extensions: dict[str, object] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(
                f"invalid manifest extension {item!r}; expected namespace=JSON"
            )
        name, value = item.split("=", 1)
        name = name.strip()
        if not name or name in extensions:
            raise ValueError("manifest extension names must be non-empty and unique")
        if "." not in name and ":" not in name:
            raise ValueError(
                "manifest extension names must use a reverse-domain or URI namespace"
            )
        try:
            extensions[name] = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON for manifest extension {name!r}: {exc}") from exc
    return extensions


def expand_inputs(inputs: Sequence[str], recursive: bool) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        expanded = os.path.expanduser(item)
        matches = glob.glob(expanded)
        candidates = matches if matches else [expanded]
        for candidate in candidates:
            path = Path(candidate)
            if path.is_dir():
                iterator = path.rglob("*") if recursive else path.glob("*")
                paths.extend(p for p in iterator if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
            elif path.is_file():
                paths.append(path)
    # Stable de-duplication while keeping user order.
    return list(dict.fromkeys(p.resolve() for p in paths))


def resize_grayscale(path: Path, max_width: int | None, max_height: int | None) -> Image.Image:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("L")
    if max_width is None and max_height is None:
        return image
    width_limit = max_width or image.width
    height_limit = max_height or image.height
    ratio = min(width_limit / image.width, height_limit / image.height, 1.0)
    if ratio >= 1.0:
        return image
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    return image.resize(size, Image.Resampling.LANCZOS)


def quantize_grayscale(image: Image.Image, bits_per_pixel: int, dither: bool) -> Image.Image:
    if bits_per_pixel == 1:
        method = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
        return image.convert("1", dither=method)
    if bits_per_pixel not in (2, 4, 8):
        raise ValueError("bits per pixel must be 1, 2, 4, or 8")
    if bits_per_pixel == 8:
        return image.convert("L")
    levels = (1 << bits_per_pixel) - 1
    array = np.asarray(image, dtype=np.uint16)
    quantized = np.rint(array * levels / 255.0).astype(np.uint8)
    expanded = np.rint(quantized * 255.0 / levels).astype(np.uint8)
    return Image.fromarray(expanded, mode="L")


def pack_raster(image: Image.Image, bits_per_pixel: int) -> bytes:
    if bits_per_pixel == 1:
        values = (np.asarray(image.convert("L"), dtype=np.uint8) >= 128).astype(np.uint8)
        return np.packbits(values.reshape(-1), bitorder="big").tobytes()

    values = np.asarray(image.convert("L"), dtype=np.uint16)
    levels = (1 << bits_per_pixel) - 1
    quantized = np.rint(values * levels / 255.0).astype(np.uint8).reshape(-1)
    if bits_per_pixel == 8:
        return quantized.tobytes()
    pixels_per_byte = 8 // bits_per_pixel
    padding = (-len(quantized)) % pixels_per_byte
    if padding:
        quantized = np.pad(quantized, (0, padding), constant_values=0)
    grouped = quantized.reshape(-1, pixels_per_byte)
    packed = np.zeros(len(grouped), dtype=np.uint8)
    for index in range(pixels_per_byte):
        shift = 8 - bits_per_pixel * (index + 1)
        packed |= grouped[:, index] << shift
    return packed.tobytes()


def rle_encode(data: bytes) -> bytes:
    """Simple byte-oriented (count, value) RLE."""
    if not data:
        return b""
    output = bytearray()
    start = 0
    while start < len(data):
        value = data[start]
        end = start + 1
        while end < len(data) and data[end] == value and end - start < 255:
            end += 1
        output.extend((end - start, value))
        start = end
    return bytes(output)


def encode_candidate(
    image: Image.Image,
    codec: str,
    bits_per_pixel: int,
    jpeg_quality: int,
) -> tuple[bytes, int]:
    buffer = io.BytesIO()
    if codec in ("group3", "group4"):
        bilevel = image.convert("1")
        bilevel.save(buffer, format="TIFF", compression=codec)
        return buffer.getvalue(), 1
    if codec == "png":
        image.save(buffer, format="PNG", optimize=True, compress_level=9)
        return buffer.getvalue(), bits_per_pixel
    if codec == "jpeg":
        image.convert("L").save(
            buffer,
            format="JPEG",
            quality=jpeg_quality,
            optimize=True,
            progressive=True,
        )
        return buffer.getvalue(), 8

    raster = pack_raster(image, bits_per_pixel)
    if codec == "raw":
        return raster, bits_per_pixel
    if codec == "rle":
        return rle_encode(raster), bits_per_pixel
    if codec == "zlib":
        return zlib.compress(raster, level=9), bits_per_pixel
    raise ValueError(f"unsupported codec: {codec}")


def encode_image(
    path: Path,
    codec: str,
    bits_per_pixel: int,
    max_width: int | None,
    max_height: int | None,
    dither: bool,
    jpeg_quality: int,
    allow_lossy_auto: bool,
) -> EncodedImage:
    grayscale = resize_grayscale(path, max_width=max_width, max_height=max_height)
    quantized = quantize_grayscale(grayscale, bits_per_pixel, dither=dither)
    raw_size = len(pack_raster(quantized, bits_per_pixel))

    if codec == "auto":
        candidates = ["png", "zlib", "rle"]
        if bits_per_pixel == 1:
            candidates = ["group4", "group3", *candidates]
        if allow_lossy_auto:
            candidates.append("jpeg")
        encoded: list[tuple[int, str, bytes, int]] = []
        errors: list[str] = []
        for candidate in candidates:
            try:
                payload, effective_bpp = encode_candidate(
                    quantized, candidate, bits_per_pixel, jpeg_quality
                )
                encoded.append((len(payload), candidate, payload, effective_bpp))
            except Exception as exc:  # Pillow builds vary in TIFF codec support.
                errors.append(f"{candidate}: {exc}")
        if not encoded:
            raise RuntimeError("no image codec succeeded: " + "; ".join(errors))
        _, codec, payload, effective_bpp = min(encoded, key=lambda item: item[0])
    else:
        payload, effective_bpp = encode_candidate(
            quantized, codec, bits_per_pixel, jpeg_quality
        )

    return EncodedImage(
        source=path,
        codec=codec,
        payload=payload,
        width=quantized.width,
        height=quantized.height,
        bits_per_pixel=effective_bpp,
        filename=path.name,
        raw_size=raw_size,
    )


def make_transmission_frames(
    encoded: EncodedImage,
    session_id: int,
    chunk_size: int,
    manifest_repeats: int,
    manifest_every: int,
    packet_repeats: int,
    profile: str,
    manifest_extensions: dict[str, object],
    extended_manifest: bool = False,
) -> tuple[list[TransmissionFrame], dict[str, object]]:
    chunks = [encoded.payload[i : i + chunk_size] for i in range(0, len(encoded.payload), chunk_size)]
    if len(chunks) > 0xFFFFFFFF:
        raise ValueError("encoded image requires more than 4,294,967,295 chunks")

    media_types = {
        "group3": ("image/tiff", "ccitt-group3"),
        "group4": ("image/tiff", "ccitt-group4"),
        "png": ("image/png", "identity"),
        "jpeg": ("image/jpeg", "identity"),
        "raw": ("image/rifp-raster", "raw"),
        "rle": ("image/rifp-raster", "rle8"),
        "zlib": ("image/rifp-raster", "zlib"),
    }
    media_type, content_encoding = media_types[encoded.codec]
    payload_crc32 = zlib.crc32(encoded.payload) & 0xFFFFFFFF
    payload_sha256 = hashlib.sha256(encoded.payload).hexdigest()
    manifest = {
        "protocol": "rifp",
        "protocol_version": f"{VERSION_MAJOR}.{VERSION_MINOR}",
        "session_id": f"{session_id:016x}",
        "filename": encoded.filename,
        "media_type": media_type,
        "content_encoding": content_encoding,
        "width": encoded.width,
        "height": encoded.height,
        "bits_per_pixel": encoded.bits_per_pixel,
        "encoded_size": len(encoded.payload),
        "raw_size": encoded.raw_size,
        "payload_crc32": payload_crc32,
        "payload_sha256": payload_sha256,
        "chunk_size": chunk_size,
        "chunk_count": len(chunks),
        "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "radio_profile": profile,
        "extensions": manifest_extensions,
    }
    manifest_payload = json.dumps(
        manifest,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")
    encoding_id = {
        "group3": ENCODING_CCITT_GROUP3,
        "group4": ENCODING_CCITT_GROUP4,
        "png": ENCODING_PNG,
        "jpeg": ENCODING_JPEG,
        "raw": ENCODING_RAW,
        "rle": ENCODING_RLE,
        "zlib": ENCODING_ZLIB,
    }[encoded.codec]
    pixel_format = {
        1: PIXEL_GRAY1,
        2: PIXEL_GRAY2,
        4: PIXEL_GRAY4,
        8: PIXEL_GRAY8,
    }[encoded.bits_per_pixel]
    descriptor_payload = ObjectDescriptor(
        encoding_id=encoding_id,
        pixel_format=pixel_format,
        width=encoded.width,
        height=encoded.height,
        chunk_size=chunk_size,
        encoded_size=len(encoded.payload),
        payload_crc32=payload_crc32,
        payload_sha256=bytes.fromhex(payload_sha256),
    ).encode()
    frames: list[TransmissionFrame] = []
    for repeat in range(max(1, manifest_repeats)):
        flags = FLAG_RETRANSMISSION if repeat else 0
        frames.append(TransmissionFrame(FRAME_OBJECT_DESCRIPTOR, 0, len(chunks), descriptor_payload, flags))
        if extended_manifest:
            frames.append(TransmissionFrame(FRAME_MANIFEST, 0, len(chunks), manifest_payload, flags))

    for sequence, chunk in enumerate(chunks):
        if manifest_every > 0 and sequence > 0 and sequence % manifest_every == 0:
            frames.append(
                TransmissionFrame(
                    FRAME_OBJECT_DESCRIPTOR,
                    0,
                    len(chunks),
                    descriptor_payload,
                    FLAG_RETRANSMISSION,
                )
            )
            if extended_manifest:
                frames.append(
                    TransmissionFrame(FRAME_MANIFEST, 0, len(chunks), manifest_payload, FLAG_RETRANSMISSION)
                )
        for repeat in range(max(1, packet_repeats)):
            flags = FLAG_RETRANSMISSION if repeat else 0
            frames.append(TransmissionFrame(FRAME_DATA, sequence, len(chunks), chunk, flags))

    end_payload = END_PAYLOAD.pack(
        len(encoded.payload),
        payload_crc32,
        bytes.fromhex(payload_sha256),
    )
    frames.append(TransmissionFrame(FRAME_END, len(chunks), len(chunks), end_payload))
    return frames, manifest

def cpfsk_modulate(
    data: bytes,
    sample_rate: float,
    symbol_rate: float,
    deviation: float,
    amplitude: float,
    ramp_ms: float,
    lead_ms: float,
) -> np.ndarray:
    samples_per_symbol = sample_rate / symbol_rate
    rounded = round(samples_per_symbol)
    if not math.isclose(samples_per_symbol, rounded, rel_tol=0, abs_tol=1e-9):
        raise ValueError("sample rate must be an integer multiple of symbol rate")
    sps = int(rounded)
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="big")
    frequencies = np.where(bits != 0, deviation, -deviation).astype(np.float64)
    per_sample_frequency = np.repeat(frequencies, sps)
    phase = 2.0 * np.pi * np.cumsum(per_sample_frequency) / sample_rate
    signal = (amplitude * np.exp(1j * phase)).astype(np.complex64)

    ramp_samples = min(len(signal) // 2, max(0, round(sample_rate * ramp_ms / 1000.0)))
    if ramp_samples:
        ramp = np.sin(np.linspace(0.0, np.pi / 2.0, ramp_samples, endpoint=True)) ** 2
        signal[:ramp_samples] *= ramp.astype(np.float32)
        signal[-ramp_samples:] *= ramp[::-1].astype(np.float32)
    lead_samples = max(0, round(sample_rate * lead_ms / 1000.0))
    if lead_samples:
        silence = np.zeros(lead_samples, dtype=np.complex64)
        signal = np.concatenate((silence, signal, silence))
    return signal


class IQFileTransmitter:
    def __init__(self, path: Path, sample_rate: float, gap_seconds: float):
        self.path = path
        self.sample_rate = sample_rate
        self.gap_seconds = max(0.0, gap_seconds)
        self.handle = path.open("wb")

    def send(self, samples: np.ndarray) -> None:
        samples.astype(np.complex64, copy=False).tofile(self.handle)
        if self.gap_seconds:
            np.zeros(round(self.sample_rate * self.gap_seconds), dtype=np.complex64).tofile(self.handle)

    def close(self) -> None:
        self.handle.close()


class SoapyTransmitter:
    def __init__(
        self,
        device_args: dict[str, str],
        channel: int,
        frequency: float,
        sample_rate: float,
        bandwidth: float,
        gain: float | None,
        antenna: str | None,
    ):
        try:
            import SoapySDR  # type: ignore
            from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_TX  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "SoapySDR Python bindings are required for RF transmission; "
                "use --iq-output for an offline test"
            ) from exc
        self.SoapySDR = SoapySDR
        self.direction = SOAPY_SDR_TX
        self.channel = channel
        self.device = SoapySDR.Device(device_args)
        tx_channels = int(self.device.getNumChannels(self.direction))
        if tx_channels == 0:
            raise RuntimeError(
                "selected SoapySDR device has no transmit channels; "
                "RTL-SDR devices are receive-only, so select TX-capable hardware "
                "with --device or use --iq-output"
            )
        if channel < 0 or channel >= tx_channels:
            raise ValueError(
                f"TX channel {channel} is unavailable; selected device has "
                f"{tx_channels} transmit channel(s)"
            )
        self.device.setSampleRate(self.direction, channel, sample_rate)
        self.device.setFrequency(self.direction, channel, frequency)
        if bandwidth > 0:
            self.device.setBandwidth(self.direction, channel, bandwidth)
        if antenna:
            self.device.setAntenna(self.direction, channel, antenna)
        if gain is not None:
            self.device.setGain(self.direction, channel, gain)
        self.stream = self.device.setupStream(self.direction, SOAPY_SDR_CF32, [channel])
        self.mtu = max(1024, int(self.device.getStreamMTU(self.stream)))

    def send(self, samples: np.ndarray) -> None:
        flags_end_burst = getattr(self.SoapySDR, "SOAPY_SDR_END_BURST", 1 << 1)
        self.device.activateStream(self.stream)
        try:
            offset = 0
            while offset < len(samples):
                count = min(self.mtu, len(samples) - offset)
                flags = flags_end_burst if offset + count == len(samples) else 0
                result = self.device.writeStream(
                    self.stream,
                    [samples[offset : offset + count]],
                    count,
                    flags=flags,
                    timeoutUs=1_000_000,
                )
                written = getattr(result, "ret", result)
                if written is None or int(written) <= 0:
                    raise RuntimeError(f"SoapySDR writeStream failed: {result}")
                offset += int(written)
        finally:
            self.device.deactivateStream(self.stream)

    def close(self) -> None:
        self.device.closeStream(self.stream)


def transmit_image(
    transmitter: IQFileTransmitter | SoapyTransmitter | None,
    encoded: EncodedImage,
    args: argparse.Namespace,
) -> float:
    session_id = secrets.randbits(64)
    frames, _manifest = make_transmission_frames(
        encoded,
        session_id=session_id,
        chunk_size=args.chunk_size,
        manifest_repeats=args.manifest_repeats,
        manifest_every=args.manifest_every,
        packet_repeats=args.packet_repeats,
        profile=args.profile,
        manifest_extensions=args.manifest_extensions_parsed,
        extended_manifest=args.extended_manifest or bool(args.manifest_extensions_parsed),
    )

    samples_per_symbol = int(round(args.sample_rate / args.symbol_rate))
    lead_samples = max(0, round(args.sample_rate * args.lead_ms / 1000.0))
    extensions: list[HeaderExtension] = [
        utf8_extension(TLV_RADIO_PROFILE, args.profile),
    ]
    if args.sender_id:
        extensions.append(utf8_extension(TLV_SENDER_ID, args.sender_id))
    if args.content_hint:
        extensions.append(utf8_extension(TLV_CONTENT_HINT, args.content_hint))

    air_frames: list[bytes] = []
    total_airtime = 0.0
    for frame in frames:
        air = build_air_frame(
            frame.frame_type,
            session_id,
            frame.sequence,
            frame.total,
            frame.payload,
            flags=frame.flags,
            extensions=extensions,
        )
        air_frames.append(air)
        sample_count = len(air) * 8 * samples_per_symbol + 2 * lead_samples
        total_airtime += sample_count / args.sample_rate

    print(
        f"{encoded.source}: {encoded.width}x{encoded.height}, {encoded.bits_per_pixel}-bit, "
        f"codec={encoded.codec}, {len(encoded.payload)} bytes, {len(frames)} frames, "
        f"airtime={total_airtime:.2f}s, session={session_id:016x}"
    )
    if args.dry_run:
        return total_airtime * args.image_repeats
    assert transmitter is not None

    for repeat_index in range(args.image_repeats):
        for air in air_frames:
            iq = cpfsk_modulate(
                air,
                sample_rate=args.sample_rate,
                symbol_rate=args.symbol_rate,
                deviation=args.deviation,
                amplitude=args.amplitude,
                ramp_ms=args.ramp_ms,
                lead_ms=args.lead_ms,
            )
            transmitter.send(iq)
            airtime = len(iq) / args.sample_rate
            required_off = airtime * (1.0 / args.duty_cycle - 1.0)
            pause = max(args.inter_frame_gap, required_off)
            if pause > 0 and not isinstance(transmitter, IQFileTransmitter):
                time.sleep(pause)
        if repeat_index + 1 < args.image_repeats and args.repeat_gap > 0:
            if isinstance(transmitter, IQFileTransmitter):
                transmitter.send(np.zeros(round(args.sample_rate * args.repeat_gap), dtype=np.complex64))
            else:
                time.sleep(args.repeat_gap)
    return total_airtime * args.image_repeats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send images using RIFP over the CPFSK radio profile.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("images", nargs="+", help="image files, glob patterns, or directories")
    parser.add_argument("--recursive", action="store_true", help="recurse into input directories")
    parser.add_argument("--shuffle", action="store_true", help="shuffle image order for every cycle")
    parser.add_argument("--cycles", type=int, default=1, help="number of full image-set cycles; 0 means forever")
    parser.add_argument("--interval", type=float, default=0.0, help="minimum seconds between image starts")
    parser.add_argument("--image-repeats", type=int, default=1, help="repeat each complete encoded image")
    parser.add_argument("--repeat-gap", type=float, default=1.0, help="seconds between complete-image repeats")

    encoding = parser.add_argument_group("image encoding")
    encoding.add_argument("--preset", choices=PRESET_WIDTHS, default="small", help="scaling preset")
    encoding.add_argument("--max-width", type=int, help="override preset maximum width")
    encoding.add_argument("--max-height", type=int, help="optional maximum height")
    encoding.add_argument("--codec", choices=CODECS, default="auto")
    encoding.add_argument("--bits", type=int, choices=(1, 2, 4, 8), default=1, help="grayscale bits per pixel")
    encoding.add_argument("--dither", action=argparse.BooleanOptionalAction, default=True)
    encoding.add_argument("--jpeg-quality", type=int, default=45)
    encoding.add_argument(
        "--allow-lossy-auto",
        action="store_true",
        help="allow auto mode to select JPEG when it is smaller",
    )

    protocol = parser.add_argument_group("protocol")
    protocol.add_argument("--profile", default=RADIO_PROFILE_CPFSK_4800, help="RIFP radio-profile identifier")
    protocol.add_argument("--sender-id", help="optional UTF-8 sender identifier carried as a header TLV")
    protocol.add_argument("--content-hint", help="optional short UTF-8 description carried as a header TLV")
    protocol.add_argument(
        "--manifest-extension",
        action="append",
        default=[],
        metavar="NAMESPACE=JSON",
        help="add a namespaced JSON member under manifest.extensions",
    )
    protocol.add_argument("--chunk-size", type=int, default=192, help="payload bytes per data frame")
    protocol.add_argument("--packet-repeats", type=int, default=2, help="repeat every data frame")
    protocol.add_argument("--manifest-repeats", type=int, default=3)
    protocol.add_argument("--manifest-every", type=int, default=8, help="resend manifest every N data chunks; 0 disables")
    protocol.add_argument(
        "--extended-manifest",
        action="store_true",
        help="also transmit the optional extended JSON manifest",
    )

    radio = parser.add_argument_group("radio")
    radio.add_argument("--frequency", type=parse_number, default=433.92e6)
    radio.add_argument("--symbol-rate", type=parse_number, default=4800.0)
    radio.add_argument("--sample-rate", type=parse_number, default=96_000.0)
    radio.add_argument("--deviation", type=parse_number, default=4_000.0)
    radio.add_argument("--bandwidth", type=parse_number, default=25_000.0)
    radio.add_argument("--amplitude", type=float, default=0.45)
    radio.add_argument("--gain", type=float, help="hardware-specific TX gain")
    radio.add_argument("--antenna", help="SoapySDR TX antenna name")
    radio.add_argument("--channel", type=int, default=0)
    radio.add_argument("--device", default="", help="SoapySDR arguments, e.g. driver=hackrf")
    radio.add_argument("--ramp-ms", type=float, default=2.0)
    radio.add_argument("--lead-ms", type=float, default=8.0, help="zero samples before and after each burst")
    radio.add_argument("--inter-frame-gap", type=float, default=0.05)
    radio.add_argument(
        "--duty-cycle",
        type=float,
        default=0.10,
        help="maximum duty-cycle fraction used for pacing RF bursts",
    )

    offline = parser.add_argument_group("offline/testing")
    offline.add_argument("--iq-output", type=Path, help="write raw complex64 IQ instead of transmitting")
    offline.add_argument("--iq-gap", type=float, default=0.05, help="silence appended after each IQ-file burst")
    offline.add_argument("--dry-run", action="store_true", help="encode and estimate airtime without transmitting")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.cycles < 0:
        raise ValueError("--cycles must be 0 or greater")
    if args.image_repeats < 1 or args.packet_repeats < 1 or args.manifest_repeats < 1:
        raise ValueError("repeat counts must be at least 1")
    if not 16 <= args.chunk_size <= 4096:
        raise ValueError("--chunk-size must be between 16 and 4096")
    if not 0.0 < args.duty_cycle <= 1.0:
        raise ValueError("--duty-cycle must be greater than 0 and at most 1")
    if not 0.0 < args.amplitude <= 1.0:
        raise ValueError("--amplitude must be in the range (0, 1]")
    if args.deviation <= 0 or args.symbol_rate <= 0 or args.sample_rate <= 0:
        raise ValueError("sample rate, symbol rate and deviation must be positive")
    ratio = args.sample_rate / args.symbol_rate
    if not math.isclose(ratio, round(ratio), rel_tol=0, abs_tol=1e-9):
        raise ValueError("--sample-rate must be an integer multiple of --symbol-rate")
    if args.jpeg_quality < 1 or args.jpeg_quality > 95:
        raise ValueError("--jpeg-quality must be between 1 and 95")
    if not args.profile or len(args.profile.encode("utf-8")) > 96:
        raise ValueError("--profile must be between 1 and 96 UTF-8 bytes")
    if args.sender_id is not None and len(args.sender_id.encode("utf-8")) > 96:
        raise ValueError("--sender-id must not exceed 96 UTF-8 bytes")
    if args.content_hint is not None and len(args.content_hint.encode("utf-8")) > 96:
        raise ValueError("--content-hint must not exceed 96 UTF-8 bytes")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
        args.manifest_extensions_parsed = parse_manifest_extensions(args.manifest_extension)
        preset_width = PRESET_WIDTHS[args.preset]
        max_width = args.max_width if args.max_width is not None else preset_width
        if max_width is not None and max_width < 1:
            raise ValueError("maximum width must be positive")
        if args.max_height is not None and args.max_height < 1:
            raise ValueError("maximum height must be positive")

        transmitter: IQFileTransmitter | SoapyTransmitter | None
        if args.dry_run:
            transmitter = None
        elif args.iq_output:
            transmitter = IQFileTransmitter(args.iq_output, args.sample_rate, args.iq_gap)
        else:
            transmitter = SoapyTransmitter(
                parse_device_args(args.device),
                channel=args.channel,
                frequency=args.frequency,
                sample_rate=args.sample_rate,
                bandwidth=args.bandwidth,
                gain=args.gain,
                antenna=args.antenna,
            )

        cycle = 0
        try:
            while args.cycles == 0 or cycle < args.cycles:
                images = expand_inputs(args.images, recursive=args.recursive)
                if not images:
                    raise FileNotFoundError("no supported image files matched the input paths")
                if args.shuffle:
                    random.shuffle(images)
                for path in images:
                    started = time.monotonic()
                    try:
                        encoded = encode_image(
                            path,
                            codec=args.codec,
                            bits_per_pixel=args.bits,
                            max_width=max_width,
                            max_height=args.max_height,
                            dither=args.dither,
                            jpeg_quality=args.jpeg_quality,
                            allow_lossy_auto=args.allow_lossy_auto,
                        )
                        transmit_image(transmitter, encoded, args)
                    except Exception as exc:
                        print(f"error processing {path}: {exc}", file=sys.stderr)
                    remaining = args.interval - (time.monotonic() - started)
                    if remaining > 0 and not args.dry_run and not isinstance(transmitter, IQFileTransmitter):
                        time.sleep(remaining)
                cycle += 1
        except KeyboardInterrupt:
            print("stopped", file=sys.stderr)
        finally:
            if transmitter is not None:
                transmitter.close()
        return 0
    except Exception as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
