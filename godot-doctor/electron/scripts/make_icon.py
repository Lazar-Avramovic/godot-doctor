"""Generate the desktop icons from the a1 hawk mark.

The geometry is the traced shard hawk and medical cross in build/hawk_mark.json.
Pillow is required to regenerate the PNG/ICO set; the committed assets are what
electron-builder packages, so the analyzer runtime stays dependency-free.
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError as exc:  # pragma: no cover - developer tooling only
    raise SystemExit("make_icon.py needs Pillow: python -m pip install pillow") from exc

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
BUILD = ROOT / "build"
ICONS = BUILD / "icons"
STATIC = PROJECT / "src" / "godot_doctor" / "static"
MARK = json.loads((BUILD / "hawk_mark.json").read_text(encoding="utf-8"))
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
SUPERSAMPLE = 4


def render(size: int) -> Image.Image:
    canvas = size * SUPERSAMPLE
    scale = canvas / 256
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (8 * scale, 8 * scale, 248 * scale, 248 * scale),
        radius=54 * scale,
        fill=MARK["background"],
        outline=MARK["border"],
        width=max(1, round(3 * scale)),
    )
    for polygon in MARK["shards"]:
        draw.polygon([(x * scale, y * scale) for x, y in polygon], fill=MARK["ink"])
    for polygon in MARK["holes"]:
        draw.polygon([(x * scale, y * scale) for x, y in polygon], fill=MARK["background"])
    return image.resize((size, size), Image.LANCZOS)


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def write_ico(path: Path, images: dict[int, bytes]) -> None:
    sizes = sorted(images)
    offset = 6 + 16 * len(sizes)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries = bytearray()
    payload = bytearray()
    for size in sizes:
        data = images[size]
        dimension = 0 if size >= 256 else size
        entries.extend(struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset,
        ))
        offset += len(data)
        payload.extend(data)
    path.write_bytes(header + bytes(entries) + bytes(payload))


def svg_path(polygons: list[list[list[float]]]) -> str:
    return " ".join(
        "M " + " L ".join(f"{x:g} {y:g}" for x, y in polygon) + " Z"
        for polygon in polygons
    )


def svg_markup() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">\n'
        f'  <rect x="8" y="8" width="240" height="240" rx="54" '
        f'fill="{MARK["background"]}" stroke="{MARK["border"]}" stroke-width="3"/>\n'
        f'  <path fill="{MARK["ink"]}" fill-rule="evenodd" '
        f'd="{svg_path(MARK["shards"])} {svg_path(MARK["holes"])}"/>\n'
        "</svg>\n"
    )


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)
    pngs: dict[int, bytes] = {}
    for size in SIZES:
        png = png_bytes(render(size))
        (ICONS / f"{size}x{size}.png").write_bytes(png)
        if size <= 256:
            pngs[size] = png
    write_ico(BUILD / "icon.ico", pngs)
    svg = svg_markup()
    (BUILD / "icon.svg").write_text(svg, encoding="utf-8")
    (STATIC / "favicon.svg").write_text(svg, encoding="utf-8")
    for size in sorted(pngs):
        print(f"icon: {ICONS / f'{size}x{size}.png'}")
    print(f"icon: {BUILD / 'icon.ico'} ({', '.join(str(size) for size in sorted(pngs))})")
    print(f"icon: {BUILD / 'icon.svg'}")
    print(f"icon: {STATIC / 'favicon.svg'}")


if __name__ == "__main__":
    main()
