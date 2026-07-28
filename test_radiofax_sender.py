#!/usr/bin/env python3
"""Tests for sender hardware validation."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from radiofax_sender import SoapyTransmitter


class FakeDevice:
    def __init__(self, tx_channels: int):
        self.tx_channels = tx_channels
        self.sample_rate_calls: list[tuple[int, int, float]] = []

    def getNumChannels(self, direction: int) -> int:
        return self.tx_channels

    def setSampleRate(self, direction: int, channel: int, sample_rate: float) -> None:
        self.sample_rate_calls.append((direction, channel, sample_rate))


class SoapyTransmitterTests(unittest.TestCase):
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
