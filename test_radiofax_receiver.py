#!/usr/bin/env python3
"""Session-level tests for compact descriptor reception."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import zlib
from pathlib import Path

from radiofax_receiver import DecodedFrame, SessionManager
from rifp_protocol import (
    ENCODING_RAW,
    END_PAYLOAD,
    FRAME_DATA,
    FRAME_END,
    FRAME_OBJECT_DESCRIPTOR,
    ObjectDescriptor,
    PIXEL_GRAY8,
)


class CompactDescriptorSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = SessionManager(Path(self.temporary.name), False, 1024, 1024, 4)
        self.payload = b"x"
        self.descriptor = ObjectDescriptor(
            ENCODING_RAW,
            PIXEL_GRAY8,
            1,
            1,
            1,
            len(self.payload),
            zlib.crc32(self.payload) & 0xFFFFFFFF,
            hashlib.sha256(self.payload).digest(),
        )

    def descriptor_frame(self) -> DecodedFrame:
        return DecodedFrame(FRAME_OBJECT_DESCRIPTOR, 7, 0, 1, self.descriptor.encode())

    def test_conflicting_total_count_abandons_session(self) -> None:
        self.manager.process(self.descriptor_frame())
        self.manager.process(DecodedFrame(FRAME_DATA, 7, 0, 2, self.payload))
        self.assertNotIn(7, self.manager.sessions)

    def test_end_integrity_conflict_abandons_session(self) -> None:
        self.manager.process(self.descriptor_frame())
        conflicting_end = END_PAYLOAD.pack(
            len(self.payload),
            self.descriptor.payload_crc32 ^ 1,
            self.descriptor.payload_sha256,
        )
        self.manager.process(DecodedFrame(FRAME_END, 7, 1, 1, conflicting_end))
        self.assertNotIn(7, self.manager.sessions)


if __name__ == "__main__":
    unittest.main()
