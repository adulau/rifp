"""Authentication conformance, hostile inputs and real CLI IQ loopbacks."""

from __future__ import annotations

import contextlib
import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
import zlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import numpy as np

import rifp_protocol
from radiofax_receiver import DecodedFrame, SessionManager
from radiofax_sender import EncodedImage, make_transmission_frames, quantize_grayscale, resize_grayscale
from rifp_auth import ObjectAuthentication, authenticate_descriptor, read_key_file
from rifp_protocol import (
    DEFAULT_PREAMBLE, SYNC_WORD, ENCODING_RAW, PIXEL_GRAY8,
    FRAME_DATA, FRAME_OBJECT_DESCRIPTOR, HeaderExtension, ObjectDescriptor,
    ProtocolError, TLV_CRITICAL, TLV_OBJECT_AUTH, UnsupportedCriticalExtension,
    build_air_frame, parse_complete_frame,
)

ROOT = Path(__file__).resolve().parent
KEY = bytes(range(32))  # Public conformance key, never a deployment credential.


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / 'received'
        self.encoded = EncodedImage(Path('test.png'), 'raw', b'abcd', 4, 1, 8, 'test.png', 4)

    def frames(self, key: bytes | None = KEY, **kwargs):
        frames, _ = make_transmission_frames(
            self.encoded, 7, 2, 2, 1, 2, 'rifp-cpfsk-4800', {},
            auth_key=key, auth_key_id='test-key', **kwargs,
        )
        return frames

    def manager(self, key: bytes | None = KEY, require: bool = True):
        return SessionManager(self.output, True, 1024, 1024, 4,
                              auth_key=key, auth_key_id='test-key', require_auth=require)

    def receive(self, frames, manager=None, session_id=7):
        manager = manager or self.manager()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for frame in frames:
                # Always rebuild valid frame CRCs, including for attacker changes.
                air = build_air_frame(frame.frame_type, session_id, frame.sequence, frame.total,
                                      frame.payload, flags=frame.flags, extensions=frame.extensions)
                parsed = parse_complete_frame(air[len(DEFAULT_PREAMBLE) + len(SYNC_WORD):])
                manager.process(DecodedFrame(frame.frame_type, session_id, frame.sequence,
                                             frame.total, parsed.payload, frame.flags,
                                             parsed.header.extensions))
        return manager

    def assert_no_output(self):
        self.assertEqual(list(self.output.iterdir()), [])

    def test_documented_vector_independently_checked_with_openssl(self):
        descriptor = ObjectDescriptor(ENCODING_RAW, PIXEL_GRAY8, 1, 1, 1, 1,
                                      zlib.crc32(b'x'), hashlib.sha256(b'x').digest()).encode()
        extension = authenticate_descriptor(KEY, 'test-key', 7, 1, descriptor)
        self.assertEqual(extension.type_id, 0xC001)
        self.assertEqual(extension.value.hex(),
                         '010108746573742d6b6579'
                         'e23c131f627f7d83f5e784d653971c53eff32049da2176dddf2a0fff2ec3d3d3')
        self.assertTrue(ObjectAuthentication.decode(extension).verify(KEY, 7, 1, descriptor))

    def test_signed_reordered_repeated_transfer_with_optional_manifest(self):
        manager = self.receive(list(reversed(self.frames(extended_manifest=True))))
        self.assertTrue(manager.sessions[7].completed)
        self.assertIsNotNone(manager.sessions[7].authentication)
        self.assertEqual(len(list(self.output.glob('*.png'))), 1)
        self.assertEqual(next(self.output.glob('*.encoded')).read_bytes(), b'abcd')
        with Image.open(next(self.output.glob('*.png'))) as image:
            np.testing.assert_array_equal(np.asarray(image), [[97, 98, 99, 100]])

    def test_unsigned_remains_supported_without_key(self):
        manager = self.receive(self.frames(None), self.manager(None, False))
        self.assertTrue(manager.sessions[7].completed)
        self.assertIsNone(manager.sessions[7].authentication)

    def test_key_does_not_require_unsigned_transfers_by_default(self):
        self.assertTrue(self.receive(self.frames(None), self.manager(KEY, False)).sessions[7].completed)

    def test_only_descriptors_carry_authentication_and_repetitions_match(self):
        frames = self.frames(extended_manifest=True)
        descriptors = [f for f in frames if f.frame_type == FRAME_OBJECT_DESCRIPTOR]
        self.assertGreater(len(descriptors), 1)
        self.assertTrue(all(f.extensions == descriptors[0].extensions for f in descriptors))
        self.assertTrue(all(not f.extensions for f in frames if f.frame_type != FRAME_OBJECT_DESCRIPTOR))

    def test_wrong_key_and_missing_key_reject_signed_image(self):
        for key in (b'!' * 32, None):
            with self.subTest(key_present=key is not None):
                self.receive(self.frames(), self.manager(key, False))
                self.assert_no_output()

    def test_unknown_key_id_rejects(self):
        manager = SessionManager(self.output, False, 1024, 1024, 4,
                                 auth_key=KEY, auth_key_id='other', require_auth=True)
        self.receive(self.frames(), manager)
        self.assert_no_output()

    def test_stripped_tlv_rejected_when_authentication_required(self):
        self.receive([replace(f, extensions=()) for f in self.frames()])
        self.assert_no_output()

    def test_signed_session_cannot_downgrade_on_descriptor_repeat(self):
        manager = self.manager(KEY, False)
        self.receive(self.frames()[:1], manager)
        self.receive(self.frames(None), manager)
        self.assert_no_output()

    def test_payload_tampering_with_original_digest_rejected(self):
        frames = [replace(f, payload=b'zz') if f.frame_type == FRAME_DATA else f
                  for f in self.frames()]
        self.receive(frames)
        self.assert_no_output()

    def test_attacker_recomputes_all_unkeyed_integrity_fields(self):
        original_auth = self.frames()[0].extensions
        self.encoded = replace(self.encoded, payload=b'zzzz')
        forged = [replace(f, extensions=original_auth) if f.frame_type == FRAME_OBJECT_DESCRIPTOR else f
                  for f in self.frames(None)]
        self.receive(forged)
        self.assert_no_output()

    def test_descriptor_semantics_session_total_and_key_id_are_bound(self):
        frame = self.frames()[0]
        auth = ObjectAuthentication.decode(frame.extensions[0])
        descriptor = ObjectDescriptor.decode(frame.payload)
        for modified in (replace(descriptor, width=2, height=2),
                         replace(descriptor, chunk_size=4),
                         replace(descriptor, encoding_id=6),
                         replace(descriptor, pixel_format=3)):
            self.assertFalse(auth.verify(KEY, 7, frame.total, modified.encode()))
        self.assertFalse(auth.verify(KEY, 8, frame.total, frame.payload))
        self.assertFalse(auth.verify(KEY, 7, frame.total + 1, frame.payload))
        self.assertFalse(replace(auth, key_id='other').verify(KEY, 7, frame.total, frame.payload))
        self.receive(self.frames(), session_id=8)
        self.assert_no_output()

    def test_corrupt_tag_rejected_with_valid_frame_crc(self):
        frames = self.frames()
        ext = frames[0].extensions[0]
        changed = replace(ext, value=ext.value[:-1] + bytes((ext.value[-1] ^ 1,)))
        self.receive([replace(f, extensions=(changed,)) if f.extensions else f for f in frames])
        self.assert_no_output()

    def test_malformed_advisory_and_duplicate_authentication_rejected(self):
        ext = self.frames()[0].extensions[0]
        invalid = [b'', ext.value[:-1], ext.value + b'\x00',
                   b'\x02' + ext.value[1:], ext.value[:1] + b'\x02' + ext.value[2:],
                   ext.value[:2] + b'\x00' + ext.value[3:],
                   ext.value[:3] + b'\xff' + ext.value[4:]]
        extensions = [(replace(ext, value=value),) for value in invalid]
        extensions += [(replace(ext, type_id=TLV_OBJECT_AUTH),), (ext, ext)]
        for exts in extensions:
            with self.subTest(extensions=exts):
                self.receive([replace(f, extensions=exts) if f.extensions else f for f in self.frames()])
                self.assert_no_output()

    def test_authentication_on_data_is_rejected(self):
        frames = self.frames()
        ext = frames[0].extensions
        self.receive([replace(f, extensions=ext) if f.frame_type == FRAME_DATA else f for f in frames])
        self.assert_no_output()

    def test_older_receiver_rejects_critical_descriptor(self):
        frame = self.frames()[0]
        air = build_air_frame(frame.frame_type, 7, 0, frame.total, frame.payload, extensions=frame.extensions)
        with patch.object(rifp_protocol, 'KNOWN_TLV_TYPES',
                          rifp_protocol.KNOWN_TLV_TYPES - {TLV_OBJECT_AUTH}):
            with self.assertRaises(UnsupportedCriticalExtension):
                parse_complete_frame(air[len(DEFAULT_PREAMBLE) + len(SYNC_WORD):])

    def test_bad_key_files_and_key_ids_rejected(self):
        path = Path(self.temporary.name) / 'key'
        for key in (b'', b'x' * 31, b'x' * 33):
            path.write_bytes(key)
            with self.assertRaises(ProtocolError):
                read_key_file(path)
        for key_id in ('', 'a' * 33, 'bad key', '\u00e9', 'x\n'):
            with self.assertRaises(ProtocolError):
                authenticate_descriptor(KEY, key_id, 7, 2, self.frames()[0].payload)
        with self.assertRaises(ValueError):
            self.manager(None, True)

    def test_cli_signed_and_unsigned_iq_loopbacks(self):
        temporary = Path(self.temporary.name)
        image = temporary / 'input.png'
        Image.new('L', (8, 8), 128).save(image)
        key_file = temporary / 'key'
        key_file.write_bytes(KEY)
        for directory in (ROOT, ROOT / 'example'):
            for signed in (False, True):
                with self.subTest(directory=directory.name, signed=signed):
                    iq = temporary / 'image.cf32'
                    output = temporary / f'{directory.name}-{signed}'
                    auth = ['--auth-key-file', str(key_file), '--auth-key-id', 'test-key'] if signed else []
                    sender = subprocess.run(
                        [sys.executable, str(directory / 'radiofax_sender.py'), str(image),
                         '--codec', 'group4', '--bits', '1', '--packet-repeats', '1',
                         '--manifest-repeats', '1', '--duty-cycle', '1', '--iq-output', str(iq), *auth],
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(sender.returncode, 0, sender.stderr)
                    receiver = subprocess.run(
                        [sys.executable, str(directory / 'radiofax_receiver.py'), '--iq-input', str(iq),
                         '--output-dir', str(output), *auth, *(['--require-auth'] if signed else [])],
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(receiver.returncode, 0, receiver.stderr)
                    self.assertEqual(len(list(output.glob('*.png'))), 1, receiver.stdout + receiver.stderr)
                    self.assertEqual('image authentication verified' in receiver.stdout, signed)
                    with Image.open(next(output.glob('*.png'))) as received:
                        self.assertEqual(received.size, (8, 8))
                        reference = quantize_grayscale(resize_grayscale(image, None, None), 1, True)
                        np.testing.assert_array_equal(np.asarray(received.convert('L')),
                                                      np.asarray(reference.convert('L')))
                    # A required-auth run must fail closed, including its exit
                    # status, both for unsigned IQ and a signed wrong-key run.
                    wrong_key = temporary / 'wrong-key'
                    wrong_key.write_bytes(b'!' * 32)
                    rejected = temporary / f'{directory.name}-{signed}-rejected'
                    failure = subprocess.run(
                        [sys.executable, str(directory / 'radiofax_receiver.py'), '--iq-input', str(iq),
                         '--output-dir', str(rejected), '--auth-key-file', str(wrong_key),
                         '--auth-key-id', 'test-key', '--require-auth'],
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(failure.returncode, 1, failure.stdout + failure.stderr)
                    self.assertEqual(list(rejected.iterdir()), [])
                    self.assertNotIn('image authentication verified', failure.stdout)


if __name__ == '__main__':
    unittest.main()
