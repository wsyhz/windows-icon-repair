# -*- coding: utf-8 -*-
"""lnk -- read and write Windows shortcuts through IShellLink, with no pywin32.

WScript.Shell's CreateShortcut (what almost every "fix my shortcuts" snippet
uses) silently drops the AUMID and the relaunch properties, and it cannot see
whether the drive a shortcut points at even exists. Talking to IShellLink and
IPropertyStore directly keeps every field, and lets us ask the questions that
actually decide whether an icon renders:

  * does TargetPath exist on disk?
  * does the IconLocation string resolve to a file (and does that file carry an
    icon resource at the requested index)?
  * is the shortcut bound to an AppUserModelID, and is that AUMID registered?

Both read and write are pure ctypes, so this module runs on a bare Python with
nothing but Pillow installed.

CLI:
    python lnk.py show   <file.lnk> [...]
    python lnk.py set    <file.lnk> TargetPath=... IconLocation=... [...]
    python lnk.py aumid  <file.lnk> [...]
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys


def _utf8_stdout():
    """Windows consoles default to a legacy codepage; shortcut names are not
    always representable in it and come back as mojibake."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_utf8_stdout()

ole32 = ctypes.WinDLL("ole32")
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
STGM_READ = 0x0
GPS_READWRITE = 0x2
VT_LPWSTR = 31
VT_EMPTY = 0

CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"
IID_IPropertyStore = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"

# System.AppUserModel.* property keys all share this fmtid.
FMTID_AppUserModel = (0x9F4C2855, 0x9F79, 0x4B39,
                      (0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3))
PKEY_AUMID = (FMTID_AppUserModel, 5)
PKEY_RELAUNCH_COMMAND = (FMTID_AppUserModel, 2)
PKEY_RELAUNCH_ICON = (FMTID_AppUserModel, 3)
PKEY_RELAUNCH_NAME = (FMTID_AppUserModel, 4)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __str__(self):
        return "{%08X-%04X-%04X-%02X%02X-%s}" % (
            self.Data1, self.Data2, self.Data3, self.Data4[0], self.Data4[1],
            "".join("%02X" % b for b in self.Data4[2:]),
        )


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


class PROPVARIANT(ctypes.Structure):
    """Only the string member is modelled; everything else is left as vt+pad."""

    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("pwszVal", ctypes.c_wchar_p),
        ("pad", ctypes.c_void_p),
    ]


_ole32 = ole32
_ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)


def guid(text):
    g = GUID()
    _ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


def _vtbl(ptr):
    return ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def call(ptr, index, restype, argtypes, *args):
    """Invoke vtable slot `index` (0..2 are IUnknown)."""
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(_vtbl(ptr)[index])(ptr, *args)


def _release(ptr):
    if ptr:
        call(ptr, 2, ctypes.c_ulong, [])


def _create_shortcut():
    p = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(
        ctypes.byref(guid(CLSID_ShellLink)),
        None,
        CLSCTX_INPROC_SERVER,
        ctypes.byref(guid(IID_IShellLinkW)),
        ctypes.byref(p),
    )
    if hr != 0 or not p.value:
        raise OSError("CoCreateInstance(ShellLink) failed hr=0x%08X" % (hr & 0xFFFFFFFF))
    return p


def _load(path):
    """Return (IShellLinkW ptr, IPersistFile ptr) for an existing .lnk."""
    link = _create_shortcut()
    pf = ctypes.c_void_p()
    hr = call(link, 0, ctypes.c_long,
              [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
              ctypes.byref(guid(IID_IPersistFile)), ctypes.byref(pf))
    if hr != 0 or not pf.value:
        _release(link)
        raise OSError("QueryInterface(IPersistFile) failed hr=0x%08X" % (hr & 0xFFFFFFFF))
    hr = call(pf, 5, ctypes.c_long, [wt.LPCWSTR, wt.DWORD], path, STGM_READ)
    if hr != 0:
        _release(pf)
        _release(link)
        raise OSError("IPersistFile::Load failed hr=0x%08X for %s"
                      % (hr & 0xFFFFFFFF, path))
    return link, pf


def _get_str(link, slot, size=1024):
    buf = ctypes.create_unicode_buffer(size)
    call(link, slot, ctypes.c_long, [wt.LPWSTR, ctypes.c_int], buf, size)
    return buf.value


def _set_str(link, slot, value):
    call(link, slot, ctypes.c_long, [wt.LPCWSTR], value)


def read(path):
    """Read every field of a .lnk. Never raises for a missing target."""
    link, pf = _load(path)
    try:
        info = {}
        info["LnkPath"] = path
        info["TargetPath"] = _get_str(link, 3)          # GetPath
        info["Description"] = _get_str(link, 6)         # GetDescription
        info["WorkingDirectory"] = _get_str(link, 8)    # GetWorkingDirectory
        info["Arguments"] = _get_str(link, 10)          # GetArguments
        icon = ctypes.create_unicode_buffer(1024)
        idx = ctypes.c_int(0)
        call(link, 16, ctypes.c_long,
             [wt.LPWSTR, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
             icon, 1024, ctypes.byref(idx))
        info["IconLocation"] = "%s,%d" % (icon.value, idx.value)
        info["IconFile"] = icon.value
        info["IconIndex"] = idx.value
        sc = ctypes.c_int(0)
        call(link, 14, ctypes.c_long, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(sc))
        info["ShowCommand"] = sc.value
        return info
    finally:
        _release(pf)
        _release(link)


def create(path, **fields):
    """Create a new .lnk from scratch; existing files at `path` are replaced.

    Same keys as write(). Needed because IPersistFile::Load fails with
    0x80070002 on a path that does not exist yet, so write() cannot originate a
    shortcut -- only edit one.
    """
    link = _create_shortcut()
    pf = ctypes.c_void_p()
    hr = call(link, 0, ctypes.c_long,
              [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
              ctypes.byref(guid(IID_IPersistFile)), ctypes.byref(pf))
    if hr != 0 or not pf.value:
        _release(link)
        raise OSError("QueryInterface(IPersistFile) failed hr=0x%08X"
                      % (hr & 0xFFFFFFFF))
    try:
        _apply(link, fields)
        hr = call(pf, 6, ctypes.c_long, [wt.LPCWSTR, wt.BOOL], path, True)
        if hr != 0:
            raise OSError("IPersistFile::Save failed hr=0x%08X for %s"
                          % (hr & 0xFFFFFFFF, path))
    finally:
        _release(pf)
        _release(link)
    return path


def write(path, **fields):
    """Rewrite fields of an existing .lnk in place; unmentioned fields survive.

    Recognised keys: TargetPath, Arguments, WorkingDirectory, IconLocation,
    Description. IconLocation is "file,index" -- an empty file resets it.
    """
    link, pf = _load(path)
    try:
        _apply(link, fields)
        hr = call(pf, 6, ctypes.c_long, [wt.LPCWSTR, wt.BOOL], path, True)
        if hr != 0:
            raise OSError("IPersistFile::Save failed hr=0x%08X for %s"
                          % (hr & 0xFFFFFFFF, path))
    finally:
        _release(pf)
        _release(link)


def _apply(link, fields):
    """Set whichever fields are present. Shared by create() and write()."""
    if "TargetPath" in fields:
        _set_str(link, 20, fields["TargetPath"])          # SetPath
        # Clear the stored relative path so the new absolute path is used.
        call(link, 18, ctypes.c_long, [wt.LPCWSTR, wt.DWORD], "", 0)
    if "Description" in fields:
        _set_str(link, 7, fields["Description"])
    if "WorkingDirectory" in fields:
        _set_str(link, 9, fields["WorkingDirectory"])
    if "Arguments" in fields:
        _set_str(link, 11, fields["Arguments"])
    if "IconLocation" in fields:
        spec = fields["IconLocation"] or ",0"
        f, _, idx = spec.rpartition(",")
        if not f:
            f, idx = spec, "0"
        try:
            inum = int(idx)
        except ValueError:
            inum = 0
        call(link, 17, ctypes.c_long, [wt.LPCWSTR, ctypes.c_int], f, inum)


def _propstore(path, writable=False):
    p = ctypes.c_void_p()
    hr = shell32.SHGetPropertyStoreFromParsingName(
        path,
        None,
        GPS_READWRITE if writable else 0,
        ctypes.byref(guid(IID_IPropertyStore)),
        ctypes.byref(p),
    )
    if hr != 0 or not p.value:
        raise OSError("SHGetPropertyStoreFromParsingName failed hr=0x%08X"
                      % (hr & 0xFFFFFFFF))
    return p


def _pkey(pair):
    fmtid, pid = pair
    k = PROPERTYKEY()
    k.fmtid = GUID(fmtid[0], fmtid[1], fmtid[2], (ctypes.c_ubyte * 8)(*fmtid[3]))
    k.pid = pid
    return k


def aumid(path, writable=False):
    """Read the AppUserModel properties the shell uses to pick a taskbar icon."""
    ps = _propstore(path, writable)
    try:
        out = {}
        for label, pair in (
            ("AUMID", PKEY_AUMID),
            ("RelaunchCommand", PKEY_RELAUNCH_COMMAND),
            ("RelaunchIconResource", PKEY_RELAUNCH_ICON),
            ("RelaunchDisplayName", PKEY_RELAUNCH_NAME),
        ):
            pv = PROPVARIANT()
            hr = call(ps, 5, ctypes.c_long,
                      [ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)],
                      ctypes.byref(_pkey(pair)), ctypes.byref(pv))
            if hr == 0 and pv.vt == VT_LPWSTR and pv.pwszVal:
                out[label] = pv.pwszVal
                _ole32.PropVariantClear(ctypes.byref(pv))
            else:
                out[label] = ""
        return out
    finally:
        if not writable:
            _release(ps)
        else:
            _release(ps)


def url_read(path):
    """Read an InternetShortcut (.url) -- it is just an INI file.

    IconFile and IconIndex are stored in arbitrary order, so they are collected
    first and only combined at the end. Reading them on the fly silently drops
    the icon whenever IconIndex happens to come first, which is exactly how
    Steam writes its game shortcuts.
    """
    info = {"LnkPath": path, "TargetPath": "", "IconLocation": ",0", "URL": ""}
    icon_file = ""
    icon_index = "0"
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        in_section = False
        for line in fh:
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                in_section = s[1:-1].lower() == "internetshortcut"
                continue
            if in_section and "=" in s:
                k, _, v = s.partition("=")
                k = k.strip().lower()
                v = v.strip()
                if k == "url":
                    info["URL"] = v
                elif k == "iconfile":
                    icon_file = v
                elif k == "iconindex":
                    icon_index = v or "0"
    if icon_file:
        info["IconFile"] = icon_file
        info["IconLocation"] = "%s,%s" % (icon_file, icon_index)
    return info


def url_write(path, icon_location=None):
    """Set IconFile/IconIndex in a .url, preserving the rest of the file."""
    if not icon_location:
        return
    f, _, idx = icon_location.rpartition(",")
    if not f:
        f, idx = icon_location, "0"
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    lines = text.splitlines()
    seen = set()
    out = []
    for line in lines:
        k = line.split("=", 1)[0].strip().lower() if "=" in line else ""
        if k == "iconfile":
            out.append("IconFile=" + f)
            seen.add("iconfile")
        elif k == "iconindex":
            out.append("IconIndex=" + idx)
            seen.add("iconindex")
        else:
            out.append(line)
    if "iconfile" not in seen:
        out.append("IconFile=" + f)
    if "iconindex" not in seen:
        out.append("IconIndex=" + idx)
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(out))


def is_url(path):
    return path.lower().endswith(".url")


def show(path):
    info = url_read(path) if is_url(path) else read(path)
    for k in ("LnkPath", "TargetPath", "Arguments", "WorkingDirectory",
              "IconLocation", "URL"):
        if k in info and info[k]:
            print("  %-18s %s" % (k + ":", info[k]))
    if not is_url(path):
        try:
            a = aumid(path)
            for k, v in a.items():
                print("  %-18s %s" % (k + ":", v or "(empty)"))
        except OSError as exc:
            print("  %-18s %s" % ("property-store:", exc))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "show":
        for p in rest:
            print(os.path.basename(p))
            show(p)
        return 0
    if cmd == "aumid":
        for p in rest:
            print(os.path.basename(p), aumid(p))
        return 0
    if cmd == "set":
        path, pairs = rest[0], rest[1:]
        fields = {}
        for pair in pairs:
            k, _, v = pair.partition("=")
            fields[k] = v
        if is_url(path):
            url_write(path, fields.get("IconLocation"))
        else:
            write(path, **fields)
        print("updated", path)
        show(path)
        return 0
    print("unknown command:", cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
