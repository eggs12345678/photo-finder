"""Draw the app icon (a magnifier over a photo) and write a multi-size .ico."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 24, 32, 48, 64, 128, 256)
SCALE = 4
INK = (12, 14, 18, 255)
LIME = (216, 240, 90, 255)
PAPER = (238, 241, 245, 255)
MUTED = (120, 130, 145, 255)


def draw(size: int = 256) -> Image.Image:
    canvas = size * SCALE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = canvas / 256

    def box(*values: float) -> tuple[float, ...]:
        return tuple(v * s for v in values)

    draw.rounded_rectangle(box(8, 8, 232, 232), radius=46 * s, fill=INK)

    # Photo card with a little landscape.
    draw.rounded_rectangle(box(50, 62, 166, 150), radius=12 * s, fill=PAPER)
    draw.ellipse(box(66, 78, 90, 102), fill=(255, 199, 92, 255))
    draw.polygon(box(58, 142, 96, 102, 128, 142), fill=MUTED)
    draw.polygon(box(96, 142, 128, 115, 160, 142), fill=(92, 105, 122, 255))

    # Magnifier over the card.
    draw.ellipse(box(126, 118, 206, 198), fill=INK)
    draw.ellipse(box(132, 124, 200, 192), outline=LIME, width=int(15 * s))
    draw.line(box(190, 184, 224, 218), fill=LIME, width=int(17 * s), joint="curve")

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        default="build/assets/icon.ico",
        help="path of the .ico to write (default: build/assets/icon.ico)",
    )
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw(size) for size in SIZES]
    frames[-1].save(target, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
