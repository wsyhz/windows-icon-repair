"""Regenerate examples/failure-shapes.png from this machine's real glyphs.

The three reference glyphs are measured here, not shipped as constants, because
they differ across Windows builds and themes. Run this on a machine you trust
and commit the result only as documentation -- the tool itself never uses the
image.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import icore  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

CELL, PAD = 220, 24
COLORS = {
    "BLANK_FILE": (198, 40, 40),
    "FOLDER": (198, 140, 0),
    "GENERIC_EXE": (30, 92, 200),
}


def main():
    probe = icore._probe_files()          # (empty file, dir, iconless exe)
    labels = ["BLANK_FILE", "FOLDER", "GENERIC_EXE"]

    tiles = []
    for path, label in zip(probe, labels):
        buf, how, idx = icore.render(path, side=256)
        if not buf:
            raise SystemExit("cannot render %s: %s" % (path, how))
        tiles.append((label, icore.to_image(buf, 256),
                      icore.sha12(buf), icore.stats(buf)))

    width = PAD + len(tiles) * (CELL + PAD)
    height = PAD + CELL + 72
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    for i, (label, tile, h, st) in enumerate(tiles):
        x = PAD + i * (CELL + PAD)
        img.paste(tile.resize((CELL, CELL), Image.LANCZOS), (x, PAD))
        draw.rectangle([x, PAD, x + CELL - 1, PAD + CELL - 1],
                       outline=COLORS[label], width=3)
        draw.text((x, PAD + CELL + 8), label, fill=COLORS[label])
        draw.text((x, PAD + CELL + 26), h, fill=(90, 90, 90))
        draw.text((x, PAD + CELL + 44), "ink=%d" % st["ink"], fill=(90, 90, 90))

    out = os.path.join(HERE, "failure-shapes.png")
    img.save(out)
    print("wrote %s (%dx%d)" % (out, img.size[0], img.size[1]))


if __name__ == "__main__":
    main()
