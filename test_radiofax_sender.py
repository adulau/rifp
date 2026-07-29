#!/usr/bin/env python3
"""Tests for sender hardware validation."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from radiofax_sender import EncodedImage, SoapyTransmitter, make_transmission_frames
from rifp_protocol import FRAME_MANIFEST, FRAME_OBJECT_DESCRIPTOR, ObjectDescriptor


class FakeDevice:
    def __init__(self, tx_channels: int):
        self.tx_channels = tx_channels
        self.sample_rate_calls: list[tuple[int, int, float]] = []

    def getNumChannels(self, direction: int) -> int:
        return self.tx_channels

    def setSampleRate(self, direction: int, channel: int, sample_rate: float) -> None:
        self.sample_rate_calls.append((direction, channel, sample_rate))


class SoapyTransmitterTests(unittest.TestCase):
    def test_compact_descriptor_is_default_and_json_is_opt_in(self) -> None:
        encoded = EncodedImage(Path("test.png"), "raw", b"abcd", 2, 2, 8, "test.png", 4)
        frames, _ = make_transmission_frames(encoded, 7, 2, 1, 0, 1, "test", {})
        self.assertEqual(frames[0].frame_type, FRAME_OBJECT_DESCRIPTOR)
        self.assertNotIn(FRAME_MANIFEST, [frame.frame_type for frame in frames])
        descriptor = ObjectDescriptor.decode(frames[0].payload)
        self.assertEqual((descriptor.encoded_size, descriptor.chunk_size), (4, 2))

        extended, _ = make_transmission_frames(
            encoded, 7, 2, 1, 0, 1, "test", {}, extended_manifest=True
        )
        self.assertEqual([frame.frame_type for frame in extended[:2]], [FRAME_OBJECT_DESCRIPTOR, FRAME_MANIFEST])

    def make_soapy_module(self, device: FakeDevice) -> types.ModuleType:
        module = types.ModuleType("SoapySDR")
        module.SOAPY_SDR_CF32 = 1
        module.SOAPY_SDR_TX = 2
        module.Device = lambda _args: device
        return module

    def test_receive_only_device_is_rejected_before_sample_rate_configuration(self) -> None:
        device = FakeDevice(tx_channels=0)
        with patch.dict(sys.modules, {"SoapySDR": self.make_soapy_module(device)}):
            with self.assertRaisesRegex(RuntimeError, "RTL-SDR devices are receive-only"):
                SoapyTransmitter({}, 0, 433.92e6, 96_000, 25_000, None, None)
        self.assertEqual(device.sample_rate_calls, [])

    def test_unavailable_transmit_channel_is_rejected(self) -> None:
        device = FakeDevice(tx_channels=1)
        with patch.dict(sys.modules, {"SoapySDR": self.make_soapy_module(device)}):
            with self.assertRaisesRegex(ValueError, "TX channel 1 is unavailable"):
                SoapyTransmitter({}, 1, 433.92e6, 96_000, 25_000, None, None)
        self.assertEqual(device.sample_rate_calls, [])


if __name__ == "__main__":
    unittest.main()
