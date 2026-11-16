# -*- coding: utf-8 -*-
"""contact_sheet -- render many icons into one labelled image for eyeballing.

Numbers alone do not convince everyone (including me, sometimes). This draws
each shortcut's real 256px icon in a grid with its name beneath it, so a human
can confirm in one glance that the tool's verdict matches what Explorer shows.

    python contact_sheet.py --out sheet.png
    python contact_sheet.py --out bad.png --only-bad
    python contact_sheet.py --out steam.png --root "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Steam"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diag_icons
import icore
import lnk

CELL = 150
PAD = 12
COLS = 6
LABEL_H = 34


def fit_font(size=13):
    from PIL import ImageFont

    for name in ("msyh.ttc", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def shorten(text, limit):
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--only-bad", action="store_true")
    ap.add_argument("--cols", type=int, default=COLS)
    args = ap.parse_args(argv)

    import icore as _ic
    from PIL import Image, ImageDraw

    roots = diag_icons.default_roots() + [("Custom", r) for r in args.root]
    roots = [(label, r) for label, r in roots
             if r and os.path.isdir(os.path.expandvars(r))]

    entries = []
    for _label, root in roots:
        for path in diag_icons.walk(os.path.expandvars(root)):
            buf, how, idx = _ic.render(path)
            cls = _ic.classify(buf)
            if args.only_bad and cls == "OK":
                continue
            entries.append((os.path.basename(path), cls, buf, path))

    if not entries:
        print("nothing to draw")
        return 1

    cols = max(1, args.cols)
    rows = (len(entries) + cols - 1) // cols
    cell = CELL + PAD
    W = cols * cell + PAD
    H = rows * (cell + LABEL_H) + PAD
    sheet = Image.new("RGB", (W, H), (250, 250, 250))
    draw = ImageDraw.Draw(sheet)
    font = fit_font(13)
    small = fit_font(11)

    colours = {
        "OK": (60, 140, 60),
        "BLANK_FILE": (200, 40, 40),
        "GENERIC_EXE": (210, 120, 20),
        "FOLDER": (200, 160, 30),
        "NO_ICON": (120, 120, 120),
    }

    for i, (name, cls, buf, path) in enumerate(entries):
        r, c = divmod(i, cols)
        x = PAD + c * cell
        y = PAD + r * (cell + LABEL_H)
        draw.rectangle([x, y, x + CELL, y + CELL], fill=(255, 255, 255),
                       outline=(210, 210, 210))
        if buf:
            sheet.paste(_ic.to_image(buf), (x, y))
        colour = colours.get(cls, (100, 100, 100))
        draw.rectangle([x, y, x + CELL, y + CELL], outline=colour, width=3)
        draw.text((x + 3, y + CELL + 2), shorten(name, 26), fill=(30, 30, 30),
                  font=font)
        draw.text((x + 3, y + CELL + 17), cls, fill=colour, font=small)

    sheet.save(args.out)
    print("wrote %s (%dx%d, %d icons)" % (args.out, W, H, len(entries)))
    counts = {}
    for _n, cls, _b, _p in entries:
        counts[cls] = counts.get(cls, 0) + 1
    print("  " + "  ".join("%s=%d" % (k, counts[k]) for k in sorted(counts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
