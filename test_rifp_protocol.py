#!/usr/bin/env python3
"""Conformance tests for the RIFP 1.0 wire format."""

from __future__ import annotations

import unittest
import hashlib
import zlib

from rifp_protocol import (
    CRC32,
    DEFAULT_PREAMBLE,
    FRAME_DATA,
    ENCODING_ZLIB,
    OBJECT_DESCRIPTOR,
    ObjectDescriptor,
    PIXEL_GRAY1,
    HeaderExtension,
    ProtocolError,
    SYNC_WORD,
    TLV_CRITICAL,
    TLV_RADIO_PROFILE,
    UnsupportedCriticalExtension,
    build_air_frame,
    build_header,
    parse_complete_frame,
    utf8_extension,
)


class RIFPProtocolTests(unittest.TestCase):
    def test_object_descriptor_round_trip_and_fixed_size(self) -> None:
        digest = hashlib.sha256(b"image").digest()
        descriptor = ObjectDescriptor(
            ENCODING_ZLIB, PIXEL_GRAY1, 160, 96, 192, 5, 0x12345678, digest
        )
        payload = descriptor.encode()
        self.assertEqual(len(payload), 56)
        self.assertEqual(OBJECT_DESCRIPTOR.size, 56)
        self.assertEqual(ObjectDescriptor.decode(payload), descriptor)

    def test_object_descriptor_rejects_reserved_fields_and_version(self) -> None:
        payload = bytearray(
            ObjectDescriptor(ENCODING_ZLIB, PIXEL_GRAY1, 1, 1, 1, 1, 0, b"x" * 32).encode()
        )
        payload[3] = 1
        with self.assertRaisesRegex(ProtocolError, "reserved"):
            ObjectDescriptor.decode(bytes(payload))
        payload[3] = 0
        payload[0] = 2
        with self.assertRaisesRegex(ProtocolError, "version"):
            ObjectDescriptor.decode(bytes(payload))

    def test_documented_data_frame_vector(self) -> None:
        header = build_header(
            FRAME_DATA,
            0x0123456789ABCDEF,
            sequence=1,
            total=2,
            payload_length=3,
        )
        payload = b"abc"
        checksum = zlib.crc32(header + payload) & 0xFFFFFFFF
        frame = header + payload + CRC32.pack(checksum)
        self.assertEqual(
            frame.hex(),
            "0100021c000000000123456789abcdef000000010000000200030000"
            "616263ed2bb111",
        )
        parsed = parse_complete_frame(frame)
        self.assertEqual(parsed.payload, payload)
        self.assertEqual(parsed.header.session_id, 0x0123456789ABCDEF)

    def test_known_header_extension_round_trip(self) -> None:
        air = build_air_frame(
            FRAME_DATA,
            session_id=7,
            sequence=0,
            total=1,
            payload=b"payload",
            extensions=[utf8_extension(TLV_RADIO_PROFILE, "rifp-cpfsk-4800")],
        )
        frame = air[len(DEFAULT_PREAMBLE) + len(SYNC_WORD) :]
        parsed = parse_complete_frame(frame)
        self.assertEqual(parsed.header.extensions[0].value, b"rifp-cpfsk-4800")

    def test_unknown_critical_tlv_is_rejected(self) -> None:
        unknown = HeaderExtension(TLV_CRITICAL | 0x1234, b"x")
        air = build_air_frame(
            FRAME_DATA,
            session_id=8,
            sequence=0,
            total=1,
            payload=b"x",
            extensions=[unknown],
        )
        frame = air[len(DEFAULT_PREAMBLE) + len(SYNC_WORD) :]
        with self.assertRaises(UnsupportedCriticalExtension):
            parse_complete_frame(frame)

    def test_crc_corruption_is_rejected(self) -> None:
        air = bytearray(
            build_air_frame(
                FRAME_DATA,
                session_id=9,
                sequence=0,
                total=1,
                payload=b"payload",
            )
        )
        frame_offset = len(DEFAULT_PREAMBLE) + len(SYNC_WORD)
        air[frame_offset + 28] ^= 0x01
        with self.assertRaises(ProtocolError):
            parse_complete_frame(bytes(air[frame_offset:]))


if __name__ == "__main__":
    unittest.main()
