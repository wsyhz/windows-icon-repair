# -*- coding: utf-8 -*-
"""icore -- render the icon Windows *actually draws* for a path, and fingerprint it.

Why this exists
---------------
"Is this shortcut's icon broken?" is normally answered by squinting at a
screenshot. That does not scale to a few hundred shortcuts and it is not
reproducible. This module turns the question into two numbers: a pixel hash and
a distance to three reference icons (blank page, yellow folder, generic exe).

Two hard-won requirements
-------------------------
1. **CoInitializeEx must run before any shell call.** Without it
   SHGetFileInfoW returns icon index 0 -- the blank-page icon -- for a large
   share of .lnk files, which manufactures dozens of fake "broken icon"
   findings. This module initialises COM at import time.

2. **Never feed a SHGFI_SYSICONINDEX index into the JUMBO (256px) image list.**
   That index belongs to the *small* system image list. Doing so renders a tiny
   40px icon in the corner of a 256px canvas, and real icons then look like
   blank pages. The correct 256px renderer is IShellItemImageFactory::GetImage,
   which is what Explorer's own list view uses. SHGetFileInfoW is kept only to
   read the index, which is a useful independent hint (0 = blank, 2 = generic
   exe, 3 = folder).

Usage
-----
    import icore
    buf, how, idx = icore.render(r"C:\path\to\thing.lnk")
    icore.sha12(buf)              # stable fingerprint of the rendered pixels
    icore.classify(buf)           # OK | BLANK_FILE | FOLDER | GENERIC_EXE | NO_ICON
    icore.stats(buf)              # ink pixel count + ink bounding box

CLI:
    python icore.py <path> [<path> ...]      # hash, class, index, path
"""

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import shutil
import tempfile

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")

SIDE = 256

SHGFI_SYSICONINDEX = 0x000004000
SIIGBF_ICONONLY = 0x00000004
COINIT_APARTMENTTHREADED = 2

FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_DIRECTORY = 0x10

S_OK = 0
E_FILENOTFOUND = -2147024894  # 0x80070002


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wt.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wt.DWORD),
        ("szDisplayName", wt.WCHAR * 260),
        ("szTypeName", wt.WCHAR * 80),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", ctypes.c_long),
        ("bmWidth", ctypes.c_long),
        ("bmHeight", ctypes.c_long),
        ("bmWidthBytes", ctypes.c_long),
        ("bmPlanes", ctypes.c_ushort),
        ("bmBitsPixel", ctypes.c_ushort),
        ("bmBits", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


# {BCC18B79-BA16-442F-80C4-8A59C30C463B}  IShellItemImageFactory
IID_IShellItemImageFactory = GUID(
    0xBCC18B79,
    0xBA16,
    0x442F,
    (ctypes.c_ubyte * 8)(0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B),
)

shell32.SHGetFileInfoW.argtypes = [
    wt.LPCWSTR, wt.DWORD, ctypes.POINTER(SHFILEINFOW), wt.UINT, wt.UINT,
]
shell32.SHGetFileInfoW.restype = ctypes.c_size_t
shell32.SHCreateItemFromParsingName.argtypes = [
    wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
]
shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO), wt.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]

# Everything in this module depends on COM being alive on this thread.
_COM_INITIALISED = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED) in (
    0,  # S_OK
    1,  # S_FALSE: already initialised on this thread
    -2147417850,  # RPC_E_CHANGED_MODE: someone else chose MTA, still usable
)


def _vtbl_call(ptr, index, restype, argtypes, *args):
    """Invoke the COM method at vtable slot `index` on the interface at ptr."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(ptr, *args)


def icon_index(path):
    """The shell's icon index for path, or -1 if the shell had nothing to say.

    Diagnostics only -- see the module docstring for why this index must not be
    used to fetch pixels. Useful as a cross-check: 0 is the blank page, 2 is the
    generic exe icon, 3 is the folder icon on a stock Windows install.
    """
    shfi = SHFILEINFOW()
    shell32.SHGetFileInfoW(
        path, 0, ctypes.byref(shfi), ctypes.sizeof(shfi), SHGFI_SYSICONINDEX
    )
    return shfi.iIcon


def _hbitmap_to_rgb(hbmp, side):
    """Copy an HBITMAP into a top-down RGB byte buffer, composited over white."""
    bm = BITMAP()
    if not gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), ctypes.byref(bm)):
        return None
    w, h = bm.bmWidth, bm.bmHeight
    if w <= 0 or h <= 0:
        return None

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h  # negative => top-down
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0  # BI_RGB

    raw = ctypes.create_string_buffer(w * h * 4)
    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        return None
    try:
        old = gdi32.SelectObject(hdc, hbmp)
        ok = gdi32.GetDIBits(hdc, hbmp, 0, h, raw, ctypes.byref(bi), 0)
        gdi32.SelectObject(hdc, old)
    finally:
        gdi32.DeleteDC(hdc)
    if not ok:
        return None

    # BGRA -> RGB over white, using the alpha channel as the mask.
    src = raw.raw
    out = bytearray(w * h * 3)
    for i in range(w * h):
        b = src[i * 4]
        g = src[i * 4 + 1]
        r = src[i * 4 + 2]
        a = src[i * 4 + 3]
        if a == 255:
            j = i * 3
            out[j] = r
            out[j + 1] = g
            out[j + 2] = b
        elif a == 0:
            j = i * 3
            out[j] = out[j + 1] = out[j + 2] = 255
        else:
            inv = 255 - a
            j = i * 3
            out[j] = (r * a + 255 * inv) // 255
            out[j + 1] = (g * a + 255 * inv) // 255
            out[j + 2] = (b * a + 255 * inv) // 255
    return bytes(out)


def render(path, out_png=None, side=SIDE):
    """Render the shell icon for `path` on a white background.

    Returns (rgb_bytes | None, how, icon_index). `how` records which channel
    produced the pixels so a failure is debuggable rather than mysterious:
      "imagefactory" -- the normal, correct path
      "notfound"     -- the path does not exist, so the shell had no item to ask
      "getimage-hr<hr>" -- the item exists but GetImage refused
    """
    idx = icon_index(path)

    item = ctypes.c_void_p()
    hr = shell32.SHCreateItemFromParsingName(
        path, None, ctypes.byref(IID_IShellItemImageFactory), ctypes.byref(item)
    )
    if hr != S_OK or not item.value:
        return None, "notfound" if hr == E_FILENOTFOUND else "item-hr%08x" % (
            hr & 0xFFFFFFFF
        ), idx

    hbmp = ctypes.c_void_p()
    try:
        hr = _vtbl_call(
            item,
            3,  # IShellItemImageFactory::GetImage
            ctypes.c_long,
            [SIZE, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
            SIZE(side, side),
            SIIGBF_ICONONLY,
            ctypes.byref(hbmp),
        )
        if hr != S_OK or not hbmp.value:
            return None, "getimage-hr%08x" % (hr & 0xFFFFFFFF), idx
        buf = _hbitmap_to_rgb(hbmp, side)
    finally:
        if hbmp.value:
            gdi32.DeleteObject(hbmp)
        _vtbl_call(item, 2, ctypes.c_ulong, [])  # IUnknown::Release

    if buf is None:
        return None, "dib-fail", idx

    if out_png:
        to_image(buf, side).save(out_png)
    return buf, "imagefactory", idx


def sha12(buf):
    """Stable 12-hex-char fingerprint of rendered pixels."""
    if not buf:
        return None
    return hashlib.sha256(buf).hexdigest()[:12]


def to_image(buf, side=SIDE):
    from PIL import Image

    return Image.frombytes("RGB", (side, side), buf)


def stats(buf, side=SIDE):
    """Ink pixel count and ink bounding box -- cheap shape features.

    A real app icon usually covers a large part of the canvas; the blank page
    glyph is a narrow sheet on a white field. These numbers make a false match
    obvious when a pixel distance looks ambiguous.
    """
    if not buf:
        return {"ink": 0, "bbox": None}
    w = h = side
    ink = 0
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = y * w * 3
        for x in range(w):
            j = row + x * 3
            if buf[j] + buf[j + 1] + buf[j + 2] < 720:  # darker than ~240 avg
                ink += 1
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
    if x1 < 0:
        return {"ink": 0, "bbox": None}
    return {"ink": ink, "bbox": (x0, y0, x1, y1)}


def diff_ratio(a, b, side=SIDE):
    """Fraction of pixels differing by more than 8 on any channel (0.0-1.0)."""
    if a is None or b is None:
        return 1.0
    if isinstance(a, bytes):
        a = to_image(a, side)
    if isinstance(b, bytes):
        b = to_image(b, side)
    from PIL import ImageChops

    delta = ImageChops.difference(a, b).convert("L")
    hist = delta.histogram()
    return 1.0 - sum(hist[:9]) / float(side * side)


_probe_dir = None
_baselines = None


def _probe_files():
    """Create real files whose icons *are* the three failure modes.

    IShellItemImageFactory needs a real item on disk; a fabricated path fails
    with 0x80070002. So the references are built from a genuine empty file, a
    genuine empty directory, and a genuine file that claims to be an .exe but
    carries no PE headers -- which is exactly what a deleted-target shortcut
    looks like to the shell.
    """
    global _probe_dir
    if _probe_dir is None:
        d = tempfile.mkdtemp(prefix="icore_probe_")
        with open(os.path.join(d, "probe.zzz"), "wb"):
            pass
        os.makedirs(os.path.join(d, "probe_dir"), exist_ok=True)
        with open(os.path.join(d, "probe.exe"), "wb") as fh:
            fh.write(b"MZ-not-a-real-pe")
        _probe_dir = d
    return (
        os.path.join(_probe_dir, "probe.zzz"),
        os.path.join(_probe_dir, "probe_dir"),
        os.path.join(_probe_dir, "probe.exe"),
    )


def baselines(force=False):
    """Reference fingerprints, measured on this machine at runtime.

    Deliberately not hardcoded: the generic icons differ between Windows
    versions and themes, so a hash copied from someone else's screenshot is a
    false oracle. Everything here is measured on the machine being diagnosed.
    """
    global _baselines
    if _baselines is not None and not force:
        return _baselines
    blank_p, folder_p, exe_p = _probe_files()
    blank = render(blank_p)[0]
    folder = render(folder_p)[0]
    generic = render(exe_p)[0]
    _baselines = {
        "BLANK_FILE": blank,
        "FOLDER": folder,
        "GENERIC_EXE": generic,
        "HASH": {
            "BLANK_FILE": sha12(blank),
            "FOLDER": sha12(folder),
            "GENERIC_EXE": sha12(generic),
        },
    }
    return _baselines


def classify(buf, tol=0.06):
    """Label a rendered icon: OK / BLANK_FILE / FOLDER / GENERIC_EXE / NO_ICON.

    Two independent tests must agree before a failure label is issued, because
    a pixel distance alone is not reliable: some genuine app icons are mostly
    white space and land close to the blank-page reference.

      (a) pixel distance to the reference is below `tol`, and
      (b) the icon's ink bounding box is consistent with the reference's.

    Anything that fails either test is reported as OK, so the tool errs toward
    silence instead of inventing damage.
    """
    if not buf:
        return "NO_ICON"
    refs = baselines()
    mine = stats(buf)
    best_name, best_d = None, 1.0
    for name in ("BLANK_FILE", "FOLDER", "GENERIC_EXE"):
        d = diff_ratio(buf, refs[name])
        if d < best_d:
            best_name, best_d = name, d
    if best_name is None or best_d > tol:
        return "OK"

    theirs = stats(refs[best_name])
    if mine["bbox"] is None or theirs["bbox"] is None:
        return "OK"
    # The failure glyphs are compact and centred; require the candidate to have
    # ink in broadly the same region (within 25% of the canvas).
    pad = SIDE * 0.25
    if any(
        abs(a - b) > pad for a, b in zip(mine["bbox"], theirs["bbox"])
    ):
        return "OK"
    return best_name


def cleanup():
    """Remove the temporary probe files created for the baselines."""
    global _probe_dir, _baselines
    if _probe_dir and os.path.isdir(_probe_dir):
        shutil.rmtree(_probe_dir, ignore_errors=True)
    _probe_dir = None
    _baselines = None


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    refs = baselines()
    print("baselines (measured on this machine):")
    for k in ("BLANK_FILE", "FOLDER", "GENERIC_EXE"):
        s = stats(refs[k])
        print("  %-12s %s  ink=%-6d bbox=%s" % (k, refs["HASH"][k], s["ink"], s["bbox"]))
    print()
    for p in argv:
        buf, how, idx = render(p)
        s = stats(buf)
        print(
            "%s  %-12s idx=%-4s ink=%-6d %s  %s"
            % (sha12(buf), classify(buf), idx, s["ink"], how, p)
        )
    cleanup()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
