#!/usr/bin/env python3
"""Discover and receive images carried by the Radio Image Framing Protocol.

The receiver accepts the RIFP-CPFSK-4800 profile from a SoapySDR device or a
raw complex64 IQ file.  It validates extensible frame headers, rejects unknown
critical extensions, reassembles chunks in any order, verifies whole-object
CRC-32 and SHA-256 values, and safely decodes the advertised image format.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import sys
import time
import warnings
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

from rifp_protocol import (
    CRC32,
    END_PAYLOAD,
    FRAME_CANCEL,
    FRAME_DATA,
    FRAME_END,
    FRAME_MANIFEST,
    HeaderExtension,
    KNOWN_FRAME_TYPES,
    MAX_WIRE_PAYLOAD,
    MIN_HEADER_LENGTH,
    PROTOCOL_NAME,
    ProtocolError,
    SYNC_WORD,
    TLV_CONTENT_HINT,
    TLV_RADIO_PROFILE,
    TLV_SENDER_ID,
    VERSION_MAJOR,
    extension_text,
    parse_base_header,
    parse_complete_frame,
)

SYNC_BITS = np.unpackbits(np.frombuffer(SYNC_WORD, dtype=np.uint8), bitorder="big")
MAX_MANIFEST_BYTES = 32_768


@dataclass(frozen=True)
class DecodedFrame:
    frame_type: int
    session_id: int
    sequence: int
    total: int
    payload: bytes
    flags: int = 0
    extensions: tuple[HeaderExtension, ...] = ()


@dataclass
class ReceiveSession:
    session_id: int
    total_chunks: int | None = None
    manifest: dict[str, object] | None = None
    chunks: dict[int, bytes] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.monotonic)
    completed: bool = False
    received_bytes: int = 0

    def progress(self) -> str:
        total = self.total_chunks or 0
        return f"{len(self.chunks)}/{total}" if total else str(len(self.chunks))


def parse_number(value: str) -> float:
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


def bits_to_bytes(bits: np.ndarray) -> bytes:
    usable = len(bits) - (len(bits) % 8)
    if usable <= 0:
        return b""
    return np.packbits(bits[:usable], bitorder="big").tobytes()


def find_sync_positions(bits: np.ndarray, max_sync_errors: int) -> np.ndarray:
    if len(bits) < len(SYNC_BITS):
        return np.empty(0, dtype=np.int64)
    windows = np.lib.stride_tricks.sliding_window_view(bits, len(SYNC_BITS))
    errors = np.count_nonzero(windows != SYNC_BITS, axis=1)
    return np.flatnonzero(errors <= max_sync_errors)


def decode_frames_from_samples(
    samples: np.ndarray,
    sample_rate: float,
    symbol_rate: float,
    max_payload: int,
    max_sync_errors: int,
) -> list[DecodedFrame]:
    if len(samples) < 2:
        return []
    ratio = sample_rate / symbol_rate
    sps = round(ratio)
    if not math.isclose(ratio, sps, rel_tol=0, abs_tol=1e-9):
        raise ValueError("sample rate must be an integer multiple of symbol rate")
    sps = int(sps)

    products = samples[1:] * np.conjugate(samples[:-1])
    discriminator = np.angle(products).astype(np.float32)
    amplitude = (np.abs(samples[1:]) * np.abs(samples[:-1])).astype(np.float32)
    frames: dict[tuple[int, int, int, int], DecodedFrame] = {}

    for phase in range(sps):
        symbol_count = (len(discriminator) - phase) // sps
        if symbol_count < 64:
            continue
        disc = discriminator[phase : phase + symbol_count * sps].reshape(symbol_count, sps).mean(axis=1)
        power = amplitude[phase : phase + symbol_count * sps].reshape(symbol_count, sps).mean(axis=1)

        peak_power = float(np.max(power)) if len(power) else 0.0
        if peak_power <= 0:
            continue
        active = power > max(peak_power * 0.04, float(np.median(power)) * 4.0)
        if np.count_nonzero(active) < 32:
            active = power > peak_power * 0.01
        if np.any(active):
            active_values = disc[active].astype(np.float64)
            low = float(np.percentile(active_values, 20.0))
            high = float(np.percentile(active_values, 80.0))
            for _ in range(8):
                low_members = active_values[np.abs(active_values - low) <= np.abs(active_values - high)]
                high_members = active_values[np.abs(active_values - low) > np.abs(active_values - high)]
                if len(low_members):
                    low = float(np.mean(low_members))
                if len(high_members):
                    high = float(np.mean(high_members))
            threshold = (low + high) / 2.0
        else:
            threshold = 0.0
        bits = (disc > threshold).astype(np.uint8)

        for sync_position in find_sync_positions(bits, max_sync_errors=max_sync_errors):
            body_start = int(sync_position) + len(SYNC_BITS)
            if body_start + MIN_HEADER_LENGTH * 8 > len(bits):
                continue
            base_bytes = bits_to_bytes(bits[body_start : body_start + MIN_HEADER_LENGTH * 8])
            try:
                base = parse_base_header(base_bytes)
            except ProtocolError:
                continue
            if base.major != VERSION_MAJOR or base.frame_type not in KNOWN_FRAME_TYPES:
                continue
            if base.payload_length > max_payload:
                continue
            frame_byte_length = base.header_length + base.payload_length + CRC32.size
            frame_bit_length = frame_byte_length * 8
            if body_start + frame_bit_length > len(bits):
                continue
            frame_bytes = bits_to_bytes(bits[body_start : body_start + frame_bit_length])
            try:
                parsed = parse_complete_frame(frame_bytes)
            except ProtocolError:
                continue
            header = parsed.header
            frame = DecodedFrame(
                header.frame_type,
                header.session_id,
                header.sequence,
                header.total,
                parsed.payload,
                header.flags,
                header.extensions,
            )
            key = (header.session_id, header.frame_type, header.sequence, parsed.crc32)
            frames[key] = frame
    return list(frames.values())

def rle_decode(data: bytes, expected_max: int) -> bytes:
    if len(data) % 2:
        raise ValueError("invalid RLE payload length")
    output = bytearray()
    for index in range(0, len(data), 2):
        count, value = data[index], data[index + 1]
        if count == 0:
            raise ValueError("invalid zero-length RLE run")
        if len(output) + count > expected_max:
            raise ValueError("RLE payload exceeds expected image size")
        output.extend([value] * count)
    return bytes(output)


def unpack_raster(data: bytes, width: int, height: int, bits_per_pixel: int) -> Image.Image:
    pixel_count = width * height
    if bits_per_pixel == 1:
        values = np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="big")[:pixel_count]
        if len(values) < pixel_count:
            raise ValueError("truncated 1-bit raster")
        array = (values.reshape(height, width) * 255).astype(np.uint8)
    elif bits_per_pixel in (2, 4):
        raw = np.frombuffer(data, dtype=np.uint8)
        pixels_per_byte = 8 // bits_per_pixel
        values = np.empty(len(raw) * pixels_per_byte, dtype=np.uint8)
        mask = (1 << bits_per_pixel) - 1
        for index in range(pixels_per_byte):
            shift = 8 - bits_per_pixel * (index + 1)
            values[index::pixels_per_byte] = (raw >> shift) & mask
        values = values[:pixel_count]
        if len(values) < pixel_count:
            raise ValueError("truncated packed raster")
        array = np.rint(values.reshape(height, width) * 255.0 / mask).astype(np.uint8)
    elif bits_per_pixel == 8:
        values = np.frombuffer(data, dtype=np.uint8)[:pixel_count]
        if len(values) < pixel_count:
            raise ValueError("truncated 8-bit raster")
        array = values.reshape(height, width)
    else:
        raise ValueError(f"unsupported bits_per_pixel: {bits_per_pixel}")
    return Image.fromarray(array, mode="L")


def sanitize_filename(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "image"


def decode_image_payload(payload: bytes, manifest: dict[str, object], max_pixels: int) -> Image.Image:
    media_type = str(manifest.get("media_type", ""))
    content_encoding = str(manifest.get("content_encoding", ""))
    codec_map = {
        ("image/tiff", "ccitt-group3"): "group3",
        ("image/tiff", "ccitt-group4"): "group4",
        ("image/png", "identity"): "png",
        ("image/jpeg", "identity"): "jpeg",
        ("image/rifp-raster", "raw"): "raw",
        ("image/rifp-raster", "rle8"): "rle",
        ("image/rifp-raster", "zlib"): "zlib",
    }
    codec = codec_map.get((media_type, content_encoding))
    if codec is None and "codec" in manifest:
        codec = str(manifest["codec"])
    if codec is None:
        raise ValueError(f"unsupported media type/encoding: {media_type!r}/{content_encoding!r}")
    width = int(manifest["width"])
    height = int(manifest["height"])
    bits_per_pixel = int(manifest["bits_per_pixel"])
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ValueError("image dimensions exceed configured safety limit")

    if codec in ("group3", "group4", "png", "jpeg"):
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as opened:
                opened.load()
                image = opened.convert("L")
        if image.width != width or image.height != height:
            raise ValueError("decoded image dimensions do not match manifest")
        return image

    bytes_per_image = math.ceil(width * height * bits_per_pixel / 8)
    if codec == "raw":
        raster = payload
    elif codec == "rle":
        raster = rle_decode(payload, expected_max=bytes_per_image)
    elif codec == "zlib":
        decompressor = zlib.decompressobj()
        raster = decompressor.decompress(payload, bytes_per_image + 1)
        if len(raster) > bytes_per_image or decompressor.unconsumed_tail:
            raise ValueError("zlib payload exceeds expected raster size")
        if not decompressor.eof:
            raise ValueError("incomplete zlib stream")
        if decompressor.unused_data:
            raise ValueError("unexpected trailing data after zlib stream")
    else:
        raise ValueError(f"unsupported image codec: {codec}")
    if len(raster) != bytes_per_image:
        raise ValueError(f"raster size mismatch: expected {bytes_per_image}, received {len(raster)}")
    return unpack_raster(raster, width, height, bits_per_pixel)


class SessionManager:
    def __init__(
        self,
        output_dir: Path,
        save_encoded: bool,
        max_image_bytes: int,
        max_pixels: int,
        max_sessions: int,
    ):
        self.output_dir = output_dir
        self.save_encoded = save_encoded
        self.max_image_bytes = max_image_bytes
        self.max_pixels = max_pixels
        self.max_sessions = max_sessions
        self.sessions: dict[int, ReceiveSession] = {}
        output_dir.mkdir(parents=True, exist_ok=True)

    def process(self, frame: DecodedFrame) -> None:
        if frame.session_id not in self.sessions and len(self.sessions) >= self.max_sessions:
            oldest_id = min(self.sessions, key=lambda key: self.sessions[key].last_seen)
            del self.sessions[oldest_id]
        session = self.sessions.setdefault(frame.session_id, ReceiveSession(frame.session_id))
        session.last_seen = time.monotonic()
        if frame.total > 0:
            if session.total_chunks is not None and session.total_chunks != frame.total:
                print(
                    f"session {frame.session_id:016x}: conflicting chunk total; resetting",
                    file=sys.stderr,
                )
                session.chunks.clear()
                session.received_bytes = 0
            session.total_chunks = frame.total

        if frame.frame_type == FRAME_MANIFEST:
            if len(frame.payload) > MAX_MANIFEST_BYTES:
                return
            try:
                manifest = json.loads(frame.payload.decode("utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("manifest is not an object")
                if manifest.get("protocol") != PROTOCOL_NAME:
                    raise ValueError("manifest protocol is not rifp")
                version = str(manifest.get("protocol_version", ""))
                if version.split(".", 1)[0] != str(VERSION_MAJOR):
                    raise ValueError(f"unsupported manifest protocol version {version!r}")
                if str(manifest.get("session_id", "")).lower() != f"{frame.session_id:016x}":
                    raise ValueError("manifest session ID does not match frame header")
                encoded_size = int(manifest.get("encoded_size", -1))
                if encoded_size < 0 or encoded_size > self.max_image_bytes:
                    raise ValueError("encoded image exceeds configured size limit")
                payload_crc32 = int(manifest.get("payload_crc32", -1))
                if not 0 <= payload_crc32 <= 0xFFFFFFFF:
                    raise ValueError("manifest payload_crc32 is missing or invalid")
                expected_sha = str(manifest.get("payload_sha256", "")).lower()
                if len(expected_sha) != 64:
                    raise ValueError("manifest payload_sha256 is missing or invalid")
                try:
                    bytes.fromhex(expected_sha)
                except ValueError as exc:
                    raise ValueError("manifest payload_sha256 is not hexadecimal") from exc
                chunk_count = int(manifest.get("chunk_count", frame.total))
                chunk_size = int(manifest.get("chunk_size", 0))
                if chunk_count < 1 or chunk_count != frame.total:
                    raise ValueError("manifest chunk count does not match frame header")
                if chunk_size < 1 or chunk_size > MAX_WIRE_PAYLOAD:
                    raise ValueError("manifest chunk size is invalid")
                extensions_object = manifest.get("extensions", {})
                if not isinstance(extensions_object, dict):
                    raise ValueError("manifest extensions member is not an object")
                profile = extension_text(frame.extensions, TLV_RADIO_PROFILE) or manifest.get("radio_profile", "unknown")
                sender = extension_text(frame.extensions, TLV_SENDER_ID)
                hint = extension_text(frame.extensions, TLV_CONTENT_HINT)
                session.manifest = manifest
                session.total_chunks = chunk_count
                sender_note = f" sender={sender}" if sender else ""
                hint_note = f" hint={hint!r}" if hint else ""
                print(
                    f"session {frame.session_id:016x}: manifest "
                    f"{manifest.get('filename', 'image')} {manifest.get('width')}x{manifest.get('height')} "
                    f"encoding={manifest.get('content_encoding')} chunks={session.total_chunks} "
                    f"profile={profile}{sender_note}{hint_note}"
                )
            except Exception as exc:
                print(f"session {frame.session_id:016x}: invalid manifest: {exc}", file=sys.stderr)
                return
        elif frame.frame_type == FRAME_DATA:
            if session.total_chunks is not None and frame.sequence >= session.total_chunks:
                return
            existing = session.chunks.get(frame.sequence)
            if existing is not None and existing != frame.payload:
                print(
                    f"session {frame.session_id:016x}: conflicting payload for chunk {frame.sequence}; abandoning session",
                    file=sys.stderr,
                )
                del self.sessions[frame.session_id]
                return
            if existing is None:
                if session.received_bytes + len(frame.payload) > self.max_image_bytes:
                    print(
                        f"session {frame.session_id:016x}: received data exceeds configured size limit",
                        file=sys.stderr,
                    )
                    return
                session.chunks[frame.sequence] = frame.payload
                session.received_bytes += len(frame.payload)
                print(f"session {frame.session_id:016x}: received chunk {frame.sequence + 1}, progress {session.progress()}")
        elif frame.frame_type == FRAME_END:
            if len(frame.payload) == END_PAYLOAD.size:
                encoded_size, payload_crc32, payload_sha256 = END_PAYLOAD.unpack(frame.payload)
                if session.manifest is not None:
                    if encoded_size != int(session.manifest.get("encoded_size", -1)):
                        print(f"session {frame.session_id:016x}: END size differs from manifest", file=sys.stderr)
                    if payload_crc32 != int(session.manifest.get("payload_crc32", -1)):
                        print(f"session {frame.session_id:016x}: END CRC differs from manifest", file=sys.stderr)
                    if payload_sha256.hex() != str(session.manifest.get("payload_sha256", "")).lower():
                        print(f"session {frame.session_id:016x}: END SHA-256 differs from manifest", file=sys.stderr)
            print(f"session {frame.session_id:016x}: end marker, progress {session.progress()}")
        elif frame.frame_type == FRAME_CANCEL:
            print(f"session {frame.session_id:016x}: cancelled by sender")
            del self.sessions[frame.session_id]
            return

        self._try_complete(session)

    def _try_complete(self, session: ReceiveSession) -> None:
        if session.completed or session.manifest is None or session.total_chunks is None:
            return
        if len(session.chunks) < session.total_chunks:
            return
        missing = [index for index in range(session.total_chunks) if index not in session.chunks]
        if missing:
            return
        payload = b"".join(session.chunks[index] for index in range(session.total_chunks))
        expected_size = int(session.manifest["encoded_size"])
        if len(payload) != expected_size:
            print(
                f"session {session.session_id:016x}: encoded size mismatch "
                f"({len(payload)} != {expected_size})",
                file=sys.stderr,
            )
            return
        expected_crc = int(session.manifest["payload_crc32"])
        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            print(f"session {session.session_id:016x}: complete-image CRC mismatch", file=sys.stderr)
            return
        expected_sha256 = str(session.manifest["payload_sha256"]).lower()
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            print(f"session {session.session_id:016x}: complete-image SHA-256 mismatch", file=sys.stderr)
            return
        try:
            image = decode_image_payload(payload, session.manifest, self.max_pixels)
            stem = Path(sanitize_filename(str(session.manifest.get("filename", "image")))).stem
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            output = self.output_dir / f"{timestamp}_{session.session_id:016x}_{stem}.png"
            image.save(output, format="PNG")
            if self.save_encoded:
                encoded_output = self.output_dir / f"{timestamp}_{session.session_id:016x}_{stem}.encoded"
                encoded_output.write_bytes(payload)
            session.completed = True
            print(f"session {session.session_id:016x}: saved {output}")
        except Exception as exc:
            print(f"session {session.session_id:016x}: image decode failed: {exc}", file=sys.stderr)

    def expire(self, timeout: float) -> None:
        now = time.monotonic()
        for session_id, session in list(self.sessions.items()):
            if now - session.last_seen > timeout:
                if not session.completed:
                    print(f"session {session_id:016x}: expired at progress {session.progress()}")
                del self.sessions[session_id]


class SoapyReceiver:
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
            from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "SoapySDR Python bindings are required for live reception; "
                "use --iq-input for an offline test"
            ) from exc
        self.SoapySDR = SoapySDR
        self.direction = SOAPY_SDR_RX
        self.channel = channel
        self.device = SoapySDR.Device(device_args)
        self.configure(frequency, sample_rate, bandwidth, gain, antenna)
        self.stream = self.device.setupStream(self.direction, SOAPY_SDR_CF32, [channel])
        self.mtu = max(1024, int(self.device.getStreamMTU(self.stream)))
        self.buffer = np.empty(self.mtu, dtype=np.complex64)
        self.active = False

    def configure(
        self,
        frequency: float,
        sample_rate: float,
        bandwidth: float,
        gain: float | None,
        antenna: str | None,
    ) -> None:
        self.device.setSampleRate(self.direction, self.channel, sample_rate)
        self.device.setFrequency(self.direction, self.channel, frequency)
        if bandwidth > 0:
            self.device.setBandwidth(self.direction, self.channel, bandwidth)
        if antenna:
            self.device.setAntenna(self.direction, self.channel, antenna)
        if gain is not None:
            self.device.setGain(self.direction, self.channel, gain)

    def activate(self) -> None:
        if not self.active:
            self.device.activateStream(self.stream)
            self.active = True

    def deactivate(self) -> None:
        if self.active:
            self.device.deactivateStream(self.stream)
            self.active = False

    def read(self, requested: int | None = None, timeout_us: int = 1_000_000) -> np.ndarray:
        self.activate()
        count = min(requested or self.mtu, self.mtu)
        result = self.device.readStream(self.stream, [self.buffer], count, timeoutUs=timeout_us)
        received = int(getattr(result, "ret", result))
        if received < 0:
            # Timeouts are expected while waiting for sparse bursts.
            return np.empty(0, dtype=np.complex64)
        return self.buffer[:received].copy()

    def close(self) -> None:
        self.deactivate()
        self.device.closeStream(self.stream)


def discover_frequency(
    receiver: SoapyReceiver,
    center_frequency: float,
    span: float,
    scan_sample_rate: float,
    scan_seconds: float,
    detect_db: float,
    timeout: float,
) -> float:
    receiver.activate()
    started = time.monotonic()
    fft_size = 65_536
    window = np.hanning(fft_size).astype(np.float32)
    bin_width = scan_sample_rate / fft_size
    smooth_bins = max(3, round(20_000.0 / bin_width))
    kernel = np.ones(smooth_bins, dtype=np.float64) / smooth_bins

    while timeout <= 0 or time.monotonic() - started < timeout:
        target_samples = round(scan_sample_rate * scan_seconds)
        collected: list[np.ndarray] = []
        count = 0
        while count < target_samples:
            block = receiver.read()
            if len(block):
                collected.append(block)
                count += len(block)
        samples = np.concatenate(collected) if collected else np.empty(0, dtype=np.complex64)
        if len(samples) < fft_size:
            continue

        psd_accumulator = np.zeros(fft_size, dtype=np.float64)
        windows = 0
        for offset in range(0, len(samples) - fft_size + 1, fft_size):
            spectrum = np.fft.fftshift(np.fft.fft(samples[offset : offset + fft_size] * window))
            psd_accumulator += np.abs(spectrum) ** 2
            windows += 1
        psd = psd_accumulator / max(1, windows)
        smoothed = np.convolve(psd, kernel, mode="same")
        frequencies = np.fft.fftshift(np.fft.fftfreq(fft_size, d=1.0 / scan_sample_rate))
        valid = np.abs(frequencies) <= span / 2.0
        # Exclude common direct-conversion DC spike.
        valid &= np.abs(frequencies) >= 8_000.0
        candidate_indices = np.flatnonzero(valid)
        if not len(candidate_indices):
            raise RuntimeError("discovery span is incompatible with scan sample rate")
        valid_power = smoothed[candidate_indices]
        peak_local = int(np.argmax(valid_power))
        peak_index = int(candidate_indices[peak_local])
        noise = float(np.median(valid_power)) + 1e-30
        peak = float(valid_power[peak_local]) + 1e-30
        excess_db = 10.0 * math.log10(peak / noise)
        candidate = center_frequency + float(frequencies[peak_index])
        print(f"discovery: strongest candidate {candidate / 1e6:.6f} MHz, {excess_db:.1f} dB above median")
        if excess_db >= detect_db:
            return candidate
    raise TimeoutError("no candidate signal crossed the discovery threshold")


def process_iq_file(args: argparse.Namespace, manager: SessionManager) -> None:
    samples = np.fromfile(args.iq_input, dtype=np.complex64)
    print(f"loaded {len(samples)} IQ samples ({len(samples) / args.sample_rate:.2f}s)")
    frames = decode_frames_from_samples(
        samples,
        sample_rate=args.sample_rate,
        symbol_rate=args.symbol_rate,
        max_payload=args.max_payload,
        max_sync_errors=args.max_sync_errors,
    )
    print(f"decoded {len(frames)} unique valid frames")
    for frame in frames:
        manager.process(frame)


def process_live(args: argparse.Namespace, manager: SessionManager) -> None:
    device_args = parse_device_args(args.device)
    frequency = args.frequency
    if args.discover:
        scanner = SoapyReceiver(
            device_args,
            channel=args.channel,
            frequency=args.scan_center,
            sample_rate=args.scan_sample_rate,
            bandwidth=min(args.scan_span, args.scan_sample_rate),
            gain=args.gain,
            antenna=args.antenna,
        )
        try:
            frequency = discover_frequency(
                scanner,
                center_frequency=args.scan_center,
                span=args.scan_span,
                scan_sample_rate=args.scan_sample_rate,
                scan_seconds=args.scan_seconds,
                detect_db=args.detect_db,
                timeout=args.discover_timeout,
            )
        finally:
            scanner.close()
        print(f"locked to {frequency / 1e6:.6f} MHz")

    receiver = SoapyReceiver(
        device_args,
        channel=args.channel,
        frequency=frequency,
        sample_rate=args.sample_rate,
        bandwidth=args.bandwidth,
        gain=args.gain,
        antenna=args.antenna,
    )
    try:
        max_samples = max(
            round(args.sample_rate * args.decode_window),
            round(args.sample_rate * 2.0),
        )
        step_samples = max(1, round(args.sample_rate * args.decode_step))
        buffer = np.empty(0, dtype=np.complex64)
        samples_since_decode = 0
        seen_frames: set[tuple[int, int, int, int]] = set()
        started = time.monotonic()

        while args.run_seconds <= 0 or time.monotonic() - started < args.run_seconds:
            block = receiver.read()
            if not len(block):
                continue
            buffer = np.concatenate((buffer, block))
            if len(buffer) > max_samples:
                buffer = buffer[-max_samples:]
            samples_since_decode += len(block)
            if samples_since_decode < step_samples:
                continue
            samples_since_decode = 0
            frames = decode_frames_from_samples(
                buffer,
                sample_rate=args.sample_rate,
                symbol_rate=args.symbol_rate,
                max_payload=args.max_payload,
                max_sync_errors=args.max_sync_errors,
            )
            for frame in frames:
                fingerprint = (
                    frame.session_id,
                    frame.frame_type,
                    frame.sequence,
                    zlib.crc32(frame.payload) & 0xFFFFFFFF,
                )
                if fingerprint in seen_frames:
                    continue
                seen_frames.add(fingerprint)
                manager.process(frame)
            manager.expire(args.session_timeout)
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    finally:
        receiver.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover and decode RIFP images using the CPFSK radio profile.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output-dir", type=Path, default=Path("received-radiofax"))
    parser.add_argument("--save-encoded", action="store_true")
    parser.add_argument("--max-image-bytes", type=int, default=20_000_000)
    parser.add_argument("--max-pixels", type=int, default=20_000_000)
    parser.add_argument("--session-timeout", type=float, default=300.0)
    parser.add_argument("--max-sessions", type=int, default=32)
    parser.add_argument("--run-seconds", type=float, default=0.0, help="0 means run until interrupted")

    protocol = parser.add_argument_group("protocol/demodulation")
    protocol.add_argument("--symbol-rate", type=parse_number, default=4800.0)
    protocol.add_argument("--sample-rate", type=parse_number, default=96_000.0)
    protocol.add_argument("--max-payload", type=int, default=32_768)
    protocol.add_argument("--max-sync-errors", type=int, default=0, choices=range(0, 5))
    protocol.add_argument("--decode-window", type=float, default=4.0, help="seconds retained for frame search")
    protocol.add_argument("--decode-step", type=float, default=0.75, help="seconds between decode passes")

    radio = parser.add_argument_group("radio")
    radio.add_argument("--frequency", type=parse_number, default=433.92e6)
    radio.add_argument("--bandwidth", type=parse_number, default=25_000.0)
    radio.add_argument("--gain", type=float, help="hardware-specific RX gain")
    radio.add_argument("--antenna", help="SoapySDR RX antenna name")
    radio.add_argument("--channel", type=int, default=0)
    radio.add_argument("--device", default="", help="SoapySDR arguments, e.g. driver=rtlsdr")

    discovery = parser.add_argument_group("wideband discovery")
    discovery.add_argument("--discover", action="store_true", help="find the strongest narrowband burst before decoding")
    discovery.add_argument("--scan-center", type=parse_number, default=434.0e6)
    discovery.add_argument("--scan-span", type=parse_number, default=1.9e6)
    discovery.add_argument("--scan-sample-rate", type=parse_number, default=2.4e6)
    discovery.add_argument("--scan-seconds", type=float, default=0.5)
    discovery.add_argument("--detect-db", type=float, default=10.0)
    discovery.add_argument("--discover-timeout", type=float, default=0.0, help="0 means scan until interrupted")

    offline = parser.add_argument_group("offline/testing")
    offline.add_argument("--iq-input", type=Path, help="raw complex64 IQ file instead of live SDR")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.sample_rate <= 0 or args.symbol_rate <= 0:
        raise ValueError("sample rate and symbol rate must be positive")
    ratio = args.sample_rate / args.symbol_rate
    if not math.isclose(ratio, round(ratio), rel_tol=0, abs_tol=1e-9):
        raise ValueError("--sample-rate must be an integer multiple of --symbol-rate")
    if args.decode_window <= 0 or args.decode_step <= 0:
        raise ValueError("decode window and step must be positive")
    if not 1 <= args.max_payload <= MAX_WIRE_PAYLOAD:
        raise ValueError(f"--max-payload must be between 1 and {MAX_WIRE_PAYLOAD}")
    if args.discover and args.iq_input:
        raise ValueError("--discover cannot be used with --iq-input")
    if args.discover and args.scan_sample_rate < args.scan_span:
        raise ValueError("--scan-sample-rate must be at least as large as --scan-span")
    if args.max_sessions < 1:
        raise ValueError("--max-sessions must be at least 1")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
        Image.MAX_IMAGE_PIXELS = args.max_pixels
        manager = SessionManager(
            output_dir=args.output_dir,
            save_encoded=args.save_encoded,
            max_image_bytes=args.max_image_bytes,
            max_pixels=args.max_pixels,
            max_sessions=args.max_sessions,
        )
        if args.iq_input:
            process_iq_file(args, manager)
        else:
            process_live(args, manager)
        return 0
    except Exception as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
