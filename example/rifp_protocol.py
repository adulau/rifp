#!/usr/bin/env python3
"""Shared wire-format definitions for the Radio Image Framing Protocol (RIFP).

RIFP is independent of a particular RF band.  The companion sender and
receiver implement the RIFP-CPFSK-4800 radio profile by default.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Iterable

PROTOCOL_NAME = "rifp"
VERSION_MAJOR = 1
VERSION_MINOR = 0

DEFAULT_PREAMBLE = b"\x55" * 48
SYNC_WORD = bytes.fromhex("D391C5A7")

# major, minor, type, header length, flags, session, sequence, total,
# payload length, reserved
BASE_HEADER = struct.Struct(">BBBBIQIIHH")
CRC32 = struct.Struct(">I")
TLV_HEADER = struct.Struct(">HH")
MIN_HEADER_LENGTH = BASE_HEADER.size
MAX_HEADER_LENGTH = 255
MAX_WIRE_PAYLOAD = 65_535

FRAME_MANIFEST = 0x01
FRAME_DATA = 0x02
FRAME_END = 0x03
FRAME_CANCEL = 0x04
FRAME_OBJECT_DESCRIPTOR = 0x05

KNOWN_FRAME_TYPES = {
    FRAME_MANIFEST,
    FRAME_DATA,
    FRAME_END,
    FRAME_CANCEL,
    FRAME_OBJECT_DESCRIPTOR,
}

# OBJECT_DESCRIPTOR payload (version 1).  The fixed layout is deliberately
# small enough for constrained radio packets and embedded implementations.
OBJECT_DESCRIPTOR_VERSION = 1
OBJECT_DESCRIPTOR = struct.Struct(">BBBBHHHHQI32s")

ENCODING_CCITT_GROUP3 = 0x01
ENCODING_CCITT_GROUP4 = 0x02
ENCODING_PNG = 0x03
ENCODING_JPEG = 0x04
ENCODING_RAW = 0x05
ENCODING_RLE = 0x06
ENCODING_ZLIB = 0x07

PIXEL_GRAY1 = 0x01
PIXEL_GRAY2 = 0x02
PIXEL_GRAY4 = 0x03
PIXEL_GRAY8 = 0x04
PIXEL_RGB888 = 0x05
PIXEL_RGBA8888 = 0x06

KNOWN_ENCODINGS = frozenset(range(ENCODING_CCITT_GROUP3, ENCODING_ZLIB + 1))
KNOWN_PIXEL_FORMATS = frozenset(
    (PIXEL_GRAY1, PIXEL_GRAY2, PIXEL_GRAY4, PIXEL_GRAY8, PIXEL_RGB888, PIXEL_RGBA8888)
)

# Flags 0-15 are advisory.  Flags 16-31 are critical: a receiver that does
# not understand a set critical flag must discard the frame.
FLAG_RETRANSMISSION = 0x00000001
KNOWN_ADVISORY_FLAGS = FLAG_RETRANSMISSION
KNOWN_CRITICAL_FLAGS = 0x00000000
CRITICAL_FLAGS_MASK = 0xFFFF0000

# Header TLV type bit 15 marks a critical extension.
TLV_CRITICAL = 0x8000
TLV_TYPE_MASK = 0x7FFF
TLV_SENDER_ID = 0x0001
TLV_RADIO_PROFILE = 0x0002
TLV_CONTENT_HINT = 0x0003
KNOWN_TLV_TYPES = {TLV_SENDER_ID, TLV_RADIO_PROFILE, TLV_CONTENT_HINT}

RADIO_PROFILE_CPFSK_4800 = "rifp-cpfsk-4800"

# END payload: encoded size, payload CRC-32, SHA-256 digest.
END_PAYLOAD = struct.Struct(">QI32s")


class ProtocolError(ValueError):
    """Raised when a frame violates the RIFP wire format."""


class UnsupportedCriticalExtension(ProtocolError):
    """Raised when safe processing requires an unsupported extension."""


@dataclass(frozen=True)
class HeaderExtension:
    type_id: int
    value: bytes

    @property
    def critical(self) -> bool:
        return bool(self.type_id & TLV_CRITICAL)

    @property
    def base_type(self) -> int:
        return self.type_id & TLV_TYPE_MASK


@dataclass(frozen=True)
class FrameHeader:
    major: int
    minor: int
    frame_type: int
    header_length: int
    flags: int
    session_id: int
    sequence: int
    total: int
    payload_length: int
    extensions: tuple[HeaderExtension, ...] = ()


@dataclass(frozen=True)
class ParsedFrame:
    header: FrameHeader
    payload: bytes
    crc32: int


@dataclass(frozen=True)
class ObjectDescriptor:
    encoding_id: int
    pixel_format: int
    width: int
    height: int
    chunk_size: int
    encoded_size: int
    payload_crc32: int
    payload_sha256: bytes
    version: int = OBJECT_DESCRIPTOR_VERSION

    def encode(self) -> bytes:
        if self.version != OBJECT_DESCRIPTOR_VERSION:
            raise ProtocolError(f"unsupported object descriptor version {self.version}")
        if self.encoding_id not in KNOWN_ENCODINGS and not 0x80 <= self.encoding_id <= 0xFF:
            raise ProtocolError("unregistered object encoding ID")
        if self.pixel_format not in KNOWN_PIXEL_FORMATS and not 0x80 <= self.pixel_format <= 0xFF:
            raise ProtocolError("unregistered pixel format ID")
        if not 1 <= self.width <= 0xFFFF or not 1 <= self.height <= 0xFFFF:
            raise ProtocolError("descriptor dimensions are outside the 16-bit range")
        if not 1 <= self.chunk_size <= 0xFFFF:
            raise ProtocolError("descriptor chunk size is outside the 16-bit range")
        if not 0 <= self.encoded_size <= 0xFFFFFFFFFFFFFFFF:
            raise ProtocolError("descriptor encoded size is outside the 64-bit range")
        if not 0 <= self.payload_crc32 <= 0xFFFFFFFF:
            raise ProtocolError("descriptor payload CRC-32 is outside the 32-bit range")
        if len(self.payload_sha256) != 32:
            raise ProtocolError("descriptor SHA-256 must contain 32 bytes")
        return OBJECT_DESCRIPTOR.pack(
            self.version, self.encoding_id, self.pixel_format, 0,
            self.width, self.height, self.chunk_size, 0,
            self.encoded_size, self.payload_crc32, self.payload_sha256,
        )

    @classmethod
    def decode(cls, payload: bytes) -> "ObjectDescriptor":
        if len(payload) != OBJECT_DESCRIPTOR.size:
            raise ProtocolError("OBJECT_DESCRIPTOR payload must be exactly 56 bytes")
        version, encoding, pixel, reserved1, width, height, chunk_size, reserved2, size, crc, sha = OBJECT_DESCRIPTOR.unpack(payload)
        if reserved1 or reserved2:
            raise ProtocolError("non-zero reserved OBJECT_DESCRIPTOR field")
        descriptor = cls(encoding, pixel, width, height, chunk_size, size, crc, sha, version)
        descriptor.encode()  # Apply the same registry and range validation.
        return descriptor


def encode_extensions(extensions: Iterable[HeaderExtension]) -> bytes:
    encoded = bytearray()
    for extension in extensions:
        if not 0 <= extension.type_id <= 0xFFFF:
            raise ProtocolError("TLV type is outside the 16-bit range")
        if len(extension.value) > 0xFFFF:
            raise ProtocolError("TLV value is too large")
        encoded.extend(TLV_HEADER.pack(extension.type_id, len(extension.value)))
        encoded.extend(extension.value)
    if MIN_HEADER_LENGTH + len(encoded) > MAX_HEADER_LENGTH:
        raise ProtocolError("RIFP extension header exceeds 255 bytes")
    return bytes(encoded)


def decode_extensions(data: bytes, reject_unknown_critical: bool = True) -> tuple[HeaderExtension, ...]:
    result: list[HeaderExtension] = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < TLV_HEADER.size:
            raise ProtocolError("truncated RIFP TLV header")
        type_id, length = TLV_HEADER.unpack_from(data, offset)
        offset += TLV_HEADER.size
        if len(data) - offset < length:
            raise ProtocolError("truncated RIFP TLV value")
        extension = HeaderExtension(type_id, data[offset : offset + length])
        offset += length
        if reject_unknown_critical and extension.critical and extension.base_type not in KNOWN_TLV_TYPES:
            raise UnsupportedCriticalExtension(
                f"unknown critical RIFP TLV type 0x{type_id:04x}"
            )
        result.append(extension)
    return tuple(result)


def build_header(
    frame_type: int,
    session_id: int,
    sequence: int,
    total: int,
    payload_length: int,
    *,
    flags: int = 0,
    extensions: Iterable[HeaderExtension] = (),
    major: int = VERSION_MAJOR,
    minor: int = VERSION_MINOR,
) -> bytes:
    if not 0 <= frame_type <= 0xFF:
        raise ProtocolError("frame type is outside the 8-bit range")
    if not 0 <= session_id <= 0xFFFFFFFFFFFFFFFF:
        raise ProtocolError("session ID is outside the 64-bit range")
    if not 0 <= sequence <= 0xFFFFFFFF or not 0 <= total <= 0xFFFFFFFF:
        raise ProtocolError("sequence or total is outside the 32-bit range")
    if not 0 <= payload_length <= MAX_WIRE_PAYLOAD:
        raise ProtocolError("payload length is outside the 16-bit range")
    if not 0 <= flags <= 0xFFFFFFFF:
        raise ProtocolError("flags are outside the 32-bit range")

    extension_bytes = encode_extensions(extensions)
    header_length = MIN_HEADER_LENGTH + len(extension_bytes)
    base = BASE_HEADER.pack(
        major,
        minor,
        frame_type,
        header_length,
        flags,
        session_id,
        sequence,
        total,
        payload_length,
        0,
    )
    return base + extension_bytes


def build_air_frame(
    frame_type: int,
    session_id: int,
    sequence: int,
    total: int,
    payload: bytes,
    *,
    flags: int = 0,
    extensions: Iterable[HeaderExtension] = (),
    preamble: bytes = DEFAULT_PREAMBLE,
) -> bytes:
    header = build_header(
        frame_type,
        session_id,
        sequence,
        total,
        len(payload),
        flags=flags,
        extensions=extensions,
    )
    checksum = zlib.crc32(header + payload) & 0xFFFFFFFF
    return preamble + SYNC_WORD + header + payload + CRC32.pack(checksum)


def parse_base_header(data: bytes) -> FrameHeader:
    if len(data) < MIN_HEADER_LENGTH:
        raise ProtocolError("truncated RIFP base header")
    (
        major,
        minor,
        frame_type,
        header_length,
        flags,
        session_id,
        sequence,
        total,
        payload_length,
        reserved,
    ) = BASE_HEADER.unpack_from(data)
    if header_length < MIN_HEADER_LENGTH or header_length > MAX_HEADER_LENGTH:
        raise ProtocolError("invalid RIFP header length")
    if reserved != 0:
        raise ProtocolError("non-zero reserved RIFP header field")
    return FrameHeader(
        major,
        minor,
        frame_type,
        header_length,
        flags,
        session_id,
        sequence,
        total,
        payload_length,
    )


def parse_complete_frame(frame_bytes: bytes) -> ParsedFrame:
    base = parse_base_header(frame_bytes)
    expected_length = base.header_length + base.payload_length + CRC32.size
    if len(frame_bytes) != expected_length:
        raise ProtocolError("RIFP frame length mismatch")
    if base.major != VERSION_MAJOR:
        raise ProtocolError(f"unsupported RIFP major version {base.major}")
    unknown_critical_flags = base.flags & CRITICAL_FLAGS_MASK & ~KNOWN_CRITICAL_FLAGS
    if unknown_critical_flags:
        raise UnsupportedCriticalExtension(
            f"unknown critical RIFP flags 0x{unknown_critical_flags:08x}"
        )

    extension_bytes = frame_bytes[MIN_HEADER_LENGTH : base.header_length]
    extensions = decode_extensions(extension_bytes)
    header = FrameHeader(
        base.major,
        base.minor,
        base.frame_type,
        base.header_length,
        base.flags,
        base.session_id,
        base.sequence,
        base.total,
        base.payload_length,
        extensions,
    )
    payload = frame_bytes[base.header_length : base.header_length + base.payload_length]
    received_crc = CRC32.unpack_from(frame_bytes, base.header_length + base.payload_length)[0]
    calculated_crc = zlib.crc32(frame_bytes[: base.header_length] + payload) & 0xFFFFFFFF
    if received_crc != calculated_crc:
        raise ProtocolError("RIFP frame CRC-32 mismatch")
    return ParsedFrame(header, payload, received_crc)


def utf8_extension(type_id: int, value: str, critical: bool = False) -> HeaderExtension:
    encoded_type = type_id | (TLV_CRITICAL if critical else 0)
    return HeaderExtension(encoded_type, value.encode("utf-8"))


def extension_text(extensions: Iterable[HeaderExtension], base_type: int) -> str | None:
    for extension in extensions:
        if extension.base_type == base_type:
            return extension.value.decode("utf-8", errors="strict")
    return None
