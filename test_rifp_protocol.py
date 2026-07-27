#!/usr/bin/env python3
"""Conformance tests for the RIFP 1.0 wire format."""

from __future__ import annotations

import unittest
import zlib

from rifp_protocol import (
    CRC32,
    DEFAULT_PREAMBLE,
    FRAME_DATA,
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
