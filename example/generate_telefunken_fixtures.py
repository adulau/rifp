#!/usr/bin/env python3
"""Regenerate the RIFP Telefunken FuBK raster and codec fixtures.

Run this script from any directory. It resolves all paths relative to itself.
The annotated SVG in source/ is the canonical editable input.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zlib
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parent
SOURCE_SVG = ROOT / "source" / "Telefunken_FuBK_RIFP_test_pattern.svg"
CANONICAL_PNG = ROOT / "source" / "Telefunken_FuBK_RIFP_test_pattern.png"
INPUT_DIR = ROOT / "input-formats"
CODEC_DIR = ROOT / "codec-fixtures"
PREVIEW_DIR = ROOT / "previews"

sys.path.insert(0, str(ROOT))
import radiofax_receiver as receiver  # noqa: E402
import radiofax_sender as sender  # noqa: E402


def payload_extension(codec: str) -> str:
    return {
        "group3": ".tiff",
        "group4": ".tiff",
        "png": ".png",
        "jpeg": ".jpg",
        "raw": ".raster",
        "rle": ".rle",
        "zlib": ".zlib",
    }[codec]


def codec_config(codec: str) -> tuple[int, bool, int]:
    if codec in {"group3", "group4", "auto"}:
        return 1, True, 45
    if codec == "jpeg":
        return 8, False, 45
    return 4, False, 45


def media_mapping(codec: str) -> tuple[str, str]:
    return {
        "group3": ("image/tiff", "ccitt-group3"),
        "group4": ("image/tiff", "ccitt-group4"),
        "png": ("image/png", "identity"),
        "jpeg": ("image/jpeg", "identity"),
        "raw": ("image/rifp-raster", "raw"),
        "rle": ("image/rifp-raster", "rle8"),
        "zlib": ("image/rifp-raster", "zlib"),
    }[codec]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def render_source_formats() -> None:
    INPUT_DIR.mkdir(exist_ok=True)
    cairosvg.svg2png(
        url=str(SOURCE_SVG),
        write_to=str(CANONICAL_PNG),
        output_width=768,
        output_height=576,
    )
    with Image.open(CANONICAL_PNG) as opened:
        rgba = opened.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
        palette = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
        outputs = [
            ("telefunken-rifp.bmp", rgb, "BMP", {}),
            ("telefunken-rifp.gif", palette, "GIF", {}),
            ("telefunken-rifp.jpeg", rgb, "JPEG", {"quality": 92, "subsampling": 0, "optimize": True}),
            ("telefunken-rifp.jpg", rgb, "JPEG", {"quality": 92, "subsampling": 0, "optimize": True}),
            ("telefunken-rifp.png", rgba, "PNG", {"optimize": True}),
            ("telefunken-rifp.tif", rgb, "TIFF", {"compression": "tiff_lzw"}),
            ("telefunken-rifp.tiff", rgb, "TIFF", {"compression": "tiff_lzw"}),
            ("telefunken-rifp.webp", rgb, "WEBP", {"quality": 92, "method": 6}),
        ]
        for name, image, image_format, options in outputs:
            image.save(INPUT_DIR / name, format=image_format, **options)


def generate_codec_fixtures() -> list[dict[str, object]]:
    if CODEC_DIR.exists():
        shutil.rmtree(CODEC_DIR)
    if PREVIEW_DIR.exists():
        shutil.rmtree(PREVIEW_DIR)
    CODEC_DIR.mkdir()
    PREVIEW_DIR.mkdir()

    records: list[dict[str, object]] = []
    for preset, max_width in sender.PRESET_WIDTHS.items():
        for requested_codec in sender.CODECS:
            bits, dither, jpeg_quality = codec_config(requested_codec)
            encoded = sender.encode_image(
                CANONICAL_PNG,
                codec=requested_codec,
                bits_per_pixel=bits,
                max_width=max_width,
                max_height=None,
                dither=dither,
                jpeg_quality=jpeg_quality,
                allow_lossy_auto=False,
            )
            media_type, content_encoding = media_mapping(encoded.codec)
            base = f"{preset}-{requested_codec}"
            if requested_codec == "auto":
                base += f"-as-{encoded.codec}"
            payload_name = base + payload_extension(encoded.codec)
            payload_path = CODEC_DIR / payload_name
            payload_path.write_bytes(encoded.payload)

            manifest: dict[str, object] = {
                "protocol": "rifp",
                "version": "1.0",
                "fixture": "telefunken-fubk-rifp-text",
                "source": CANONICAL_PNG.name,
                "preset": preset,
                "requested_codec": requested_codec,
                "codec": encoded.codec,
                "media_type": media_type,
                "content_encoding": content_encoding,
                "width": encoded.width,
                "height": encoded.height,
                "bits_per_pixel": encoded.bits_per_pixel,
                "encoded_size": len(encoded.payload),
                "raw_size": encoded.raw_size,
                "payload_crc32": zlib.crc32(encoded.payload) & 0xFFFFFFFF,
                "payload_sha256": hashlib.sha256(encoded.payload).hexdigest(),
                "jpeg_quality": 45 if encoded.codec == "jpeg" else None,
                "payload_file": payload_name,
            }
            decoded = receiver.decode_image_payload(encoded.payload, manifest, max_pixels=10_000_000)
            if requested_codec == "auto" or (
                preset == "full" and requested_codec in {"group3", "group4", "png", "jpeg", "raw", "rle", "zlib"}
            ):
                preview_name = f"{base}.png"
                decoded.save(PREVIEW_DIR / preview_name, format="PNG", optimize=True)
                manifest["preview_file"] = preview_name

            manifest_path = CODEC_DIR / f"{base}.manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            records.append(manifest)
    return records


def write_indexes(records: list[dict[str, object]]) -> None:
    index = {
        "fixture_set": "RIFP Telefunken FuBK diagnostic image",
        "rifp_version": "1.0",
        "canonical_dimensions": [768, 576],
        "source_input_extensions": sorted(sender.IMAGE_SUFFIXES),
        "requested_codecs": list(sender.CODECS),
        "scaling_presets": sender.PRESET_WIDTHS,
        "records": records,
    }
    (ROOT / "fixtures.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksum_lines = []
    for path in sorted(p for p in ROOT.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        checksum_lines.append(f"{file_sha256(path)}  {path.relative_to(ROOT).as_posix()}")
    (ROOT / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def main() -> int:
    render_source_formats()
    records = generate_codec_fixtures()
    write_indexes(records)
    print(f"Generated {len(records)} codec fixtures in {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
