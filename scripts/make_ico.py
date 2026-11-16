# -*- coding: utf-8 -*-
"""make_ico -- build a proper multi-size .ico from any image or exe icon.

Windows picks a different icon size for 16 (menus), 20/24 (taskbar at 125%/150%
scaling), 32 (desktop small), 48 (medium), 256 (large tiles). An .ico that only
carries 32x32 looks blurry everywhere else, which is why "I fixed the icon" so
often looks like it did not work.

CLI:
    python make_ico.py --from-image logo.png --out app.ico
    python make_ico.py --from-exe C:\\app\\app.exe --out app.ico
    python make_ico.py --from-exe app.exe --out app.ico --sizes 16,32,48,256
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SIZES = [16, 24, 32, 48, 64, 128, 256]


def from_exe(exe, side=256):
    """Render an exe's own icon at `side` via the shell (correct, DPI-aware)."""
    import icore

    buf, how, idx = icore.render(exe, side=side)
    if not buf:
        raise OSError("no icon resource in %s (%s)" % (exe, how))
    return icore.to_image(buf, side)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-image", default="")
    src.add_argument("--from-exe", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--sizes", default=",".join(str(s) for s in SIZES))
    args = ap.parse_args(argv)

    from PIL import Image

    if args.from_exe:
        base = from_exe(args.from_exe)
    else:
        base = Image.open(args.from_image).convert("RGBA")

    sizes = sorted({int(s) for s in args.sizes.split(",") if s.strip()})
    if max(sizes) > max(base.size):
        # Upscaling is lossy; say so rather than shipping a soft icon silently.
        print("warning: source is %dx%d, smaller than the largest requested "
              "size %d" % (base.size + (max(sizes),)), file=sys.stderr)

    frames = []
    for s in sizes:
        # LANCZOS keeps thin strokes readable when shrinking; NEAREST is only
        # right for pixel art, which a generic fixer cannot assume.
        frames.append(base.resize((s, s), Image.LANCZOS))

    frames[-1].save(args.out, format="ICO",
                    sizes=[(s, s) for s in sizes])
    print("wrote %s (%d sizes: %s)" % (args.out, len(sizes),
                                       ", ".join(str(s) for s in sizes)))
    print("  bytes: %d" % os.path.getsize(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
