#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import radiofax_receiver as receiver
import radiofax_sender as sender

ROOT = Path(__file__).resolve().parent


class TelefunkenFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8"))
        cls.source = ROOT / "source" / "Telefunken_FuBK_RIFP_test_pattern.png"

    def test_all_sender_source_extensions_are_present(self) -> None:
        present = {p.suffix.lower() for p in (ROOT / "input-formats").iterdir() if p.is_file()}
        self.assertEqual(set(sender.IMAGE_SUFFIXES), present)
        for path in (ROOT / "input-formats").iterdir():
            with Image.open(path) as image:
                self.assertEqual(image.size, (768, 576))

    def test_every_codec_and_preset_is_present(self) -> None:
        pairs = {(r["preset"], r["requested_codec"]) for r in self.index["records"]}
        expected = {(p, c) for p in sender.PRESET_WIDTHS for c in sender.CODECS}
        self.assertEqual(expected, pairs)

    def test_payload_hashes_and_decoding(self) -> None:
        for manifest in self.index["records"]:
            with self.subTest(preset=manifest["preset"], codec=manifest["requested_codec"]):
                payload = (ROOT / "codec-fixtures" / manifest["payload_file"]).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest["payload_sha256"])
                decoded = receiver.decode_image_payload(payload, manifest, max_pixels=10_000_000)
                self.assertEqual(decoded.size, (manifest["width"], manifest["height"]))

                # Recreate the encoder's quantized reference. Container codecs and
                # RIFP raster codecs are lossless; JPEG is checked with an error bound.
                max_width = sender.PRESET_WIDTHS[manifest["preset"]]
                reference_gray = sender.resize_grayscale(self.source, max_width=max_width, max_height=None)
                reference = sender.quantize_grayscale(
                    reference_gray,
                    int(manifest["bits_per_pixel"]),
                    dither=(manifest["requested_codec"] in {"auto", "group3", "group4"}),
                ).convert("L")
                a = np.asarray(decoded, dtype=np.int16)
                b = np.asarray(reference, dtype=np.int16)
                if manifest["codec"] == "jpeg":
                    self.assertLess(float(np.mean(np.abs(a - b))), 12.0)
                else:
                    np.testing.assert_array_equal(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
