# RIFP Telefunken FuBK test fixtures

This directory contains an annotated Telefunken FuBK-style test grid for the
Radio Image Framing Protocol (RIFP) 1.0 implementation.

The diagnostic overlay adds:

- high-contrast title and radio-profile text;
- black, dark-gray, mid-gray, and white labels;
- mixed-case and numeric glyphs;
- a 7/9/11/13-pixel small-text ladder;
- ASCII and UTF-8 character rows.

## Coverage

`input-formats/` contains every source suffix accepted by the sender:
BMP, GIF, JPEG, JPG, PNG, TIF, TIFF, and WebP.

`codec-fixtures/` contains every sender codec at every scaling preset:

- `auto`
- CCITT Group 3 TIFF
- CCITT Group 4 TIFF
- PNG
- JPEG
- packed raw RIFP raster
- byte-oriented RLE
- ZLIB-compressed RIFP raster

Scaling presets are `tiny` (96 px), `small` (160 px), `medium` (320 px),
`large` (640 px), and `full` (768 px source width). Group 3, Group 4, and
auto fixtures use 1-bit Floyd-Steinberg dithering. PNG/raw/RLE/ZLIB use
4-bit grayscale. JPEG uses 8-bit grayscale at quality 45.

Each payload has a companion `.manifest.json` containing its media type,
content encoding, dimensions, bit depth, CRC-32, and SHA-256. `fixtures.json`
is the complete machine-readable index.

## Run the conformance test

```bash
python3 -m unittest -v test_telefunken_fixtures.py
```

The test verifies source-format coverage, the complete codec/preset matrix,
payload hashes, dimensions, exact lossless reconstruction, and a bounded JPEG
mean absolute error.

## Regeneration

The annotated SVG is the canonical editable source. `generate_telefunken_fixtures.py`
rebuilds the raster inputs and codec payloads. It requires Pillow, NumPy, and
CairoSVG in addition to the RIFP implementation files.

The unmodified user-provided SVG is retained as
`source/Telefunken_FuBK_test_pattern.original.svg`. Preserve the original
source's applicable attribution and license when redistributing derivatives.
