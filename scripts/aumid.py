# -*- coding: utf-8 -*-
"""aumid -- read, register and diagnose AppUserModelIDs.

Why this exists
---------------
A Win32 app that wants a taskbar identity stamps an AUMID onto its window:
Steam uses "Valve.Steam.Client", Chrome uses "Chrome". The shell then resolves
that app's taskbar button in this order:

    1. HKCU/HKLM\\Software\\Classes\\AppUserModelId\\<AUMID>  -> IconUri
    2. the window's own RelaunchIconResource property
    3. the shortcut matched by AUMID in the pinned-folder cache
    4. the generic "application window" glyph  <-- the bug people report

So a pinned app can show the generic icon even though its .lnk is perfect,
because the shell never looks at the .lnk for a *running* button. That is a
distinct failure from a broken shortcut, and it needs a different fix.

CLI:
    python aumid.py get     <file-or-exe>       # AUMID of a shortcut/exe
    python aumid.py window  <hwnd>              # AUMID a live window advertises
    python aumid.py find                        # windows that have an AUMID
    python aumid.py status  <AUMID>             # is it registered, and where
    python aumid.py register <AUMID> --icon C:\\path\\to.ico [--name Steam]
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lnk

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")

COINIT_APARTMENTTHREADED = 0x2
GPS_READWRITE = 0x2

IID_IPropertyStore = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
PS_GETVALUE = 5

# System.AppUserModel.* share one fmtid; the pid selects the property.
FMTID_AUM = (0x9F4C2855, 0x9F79, 0x4B39,
             (0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3))
PID_AUM = {
    "RelaunchCommand": 2,
    "RelaunchIconResource": 3,
    "RelaunchDisplayName": 4,
    "AUMID": 5,
}

VT_LPWSTR = 31
VT_EMPTY = 0


class PROPVARIANT(ctypes.Structure):
    """Only the string member matters for the AppUserModel properties."""

    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("pwszVal", ctypes.c_wchar_p),
        ("pad", ctypes.c_void_p),
    ]


ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)


def _vtbl(ptr):
    return ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def _call(ptr, index, restype, argtypes, *args):
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(_vtbl(ptr)[index])(ptr, *args)


def _read_store(ps):
    """Pull all four AppUserModel properties out of an IPropertyStore."""
    out = {}
    for label, pid in PID_AUM.items():
        key = lnk._pkey((FMTID_AUM, pid))
        pv = PROPVARIANT()
        hr = _call(ps, PS_GETVALUE, ctypes.c_long,
                   [ctypes.POINTER(lnk.PROPERTYKEY),
                    ctypes.POINTER(PROPVARIANT)],
                   ctypes.byref(key), ctypes.byref(pv))
        if hr == 0 and pv.vt == VT_LPWSTR and pv.pwszVal:
            out[label] = pv.pwszVal
            ole32.PropVariantClear(ctypes.byref(pv))
        else:
            out[label] = ""
    return out


def from_path(path):
    """Read the AUMID properties stored on a file (shortcut or exe)."""
    if not os.path.exists(path):
        raise OSError("not found: %s" % path)
    p = ctypes.c_void_p()
    hr = shell32.SHGetPropertyStoreFromParsingName(
        ctypes.c_wchar_p(path), None, 0,
        ctypes.byref(lnk.guid(IID_IPropertyStore)), ctypes.byref(p))
    if hr != 0 or not p.value:
        raise OSError("SHGetPropertyStoreFromParsingName failed hr=0x%08X"
                      % (hr & 0xFFFFFFFF))
    try:
        return _read_store(p)
    finally:
        _call(p, 2, ctypes.c_ulong, [])


def from_window(hwnd):
    """Read the AUMID properties a *live window* advertises.

    This is the decisive measurement for a taskbar icon bug: it tells you the
    AUMID the running process actually stamped, which may differ from anything
    stored on disk.
    """
    p = ctypes.c_void_p()
    hr = shell32.SHGetPropertyStoreForWindow(
        wt.HWND(hwnd), ctypes.byref(lnk.guid(IID_IPropertyStore)), ctypes.byref(p))
    if hr != 0 or not p.value:
        raise OSError("SHGetPropertyStoreForWindow failed hr=0x%08X (hwnd=%s)"
                      % (hr & 0xFFFFFFFF, hwnd))
    try:
        return _read_store(p)
    finally:
        _call(p, 2, ctypes.c_ulong, [])


def window_owner(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(wt.HWND(hwnd), title, 512)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wt.HWND(hwnd), cls, 256)
    return pid.value, title.value, cls.value


def registered(aumid):
    """Return [(hive_name, key_path, {values})] for every place it is declared."""
    import winreg

    found = []
    for hive, hname in ((winreg.HKEY_CURRENT_USER, "HKCU"),
                        (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        try:
            key = winreg.OpenKey(hive, r"Software\Classes\AppUserModelId\%s" % aumid)
        except OSError:
            continue
        with key:
            values = {}
            i = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                values[name] = value
                i += 1
            found.append((hname, r"Software\Classes\AppUserModelId\%s" % aumid,
                          values))
    return found


def register(aumid, icon_uri, display_name=None):
    """Declare the AUMID under HKCU so the taskbar can draw the icon."""
    import winreg

    key_path = r"Software\Classes\AppUserModelId\%s" % aumid
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ,
                          display_name or aumid)
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_EXPAND_SZ, icon_uri)
    return key_path


def enum_aumid_windows():
    """Every visible top-level window that advertises an AUMID."""
    rows = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        try:
            props = from_window(hwnd)
        except OSError:
            return True
        if props.get("AUMID"):
            pid, title, cls = window_owner(hwnd)
            rows.append({"hwnd": hwnd, "pid": pid, "title": title,
                         "class": cls, "props": props})
        return True

    user32.EnumWindows(cb, 0)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action",
                    choices=["get", "window", "find", "status", "register"])
    ap.add_argument("target", nargs="?", default="")
    ap.add_argument("--icon", default="", help="icon path for register")
    ap.add_argument("--name", default="", help="display name for register")
    ap.add_argument("--all", action="store_true",
                    help="show empty properties too")
    args = ap.parse_args(argv)

    if args.action == "get":
        if not args.target:
            ap.error("get needs a path")
        props = from_path(args.target)
        for label in ("AUMID", "RelaunchCommand", "RelaunchIconResource",
                      "RelaunchDisplayName"):
            if args.all or props.get(label):
                print("  %-22s %s" % (label, props.get(label, "")))
        if props.get("AUMID"):
            hits = registered(props["AUMID"])
            print("  %-22s %s" % ("Registered",
                                  hits[0][0] if hits else "NO -- shell cannot "
                                  "resolve an icon from this AUMID"))
        return 0

    if args.action == "window":
        if not args.target:
            ap.error("window needs an hwnd")
        hwnd = int(args.target, 0)
        pid, title, cls = window_owner(hwnd)
        print("  hwnd=%-10d pid=%-7d class=%s" % (hwnd, pid, cls))
        print("  title=%s" % title)
        props = from_window(hwnd)
        for label in ("AUMID", "RelaunchCommand", "RelaunchIconResource",
                      "RelaunchDisplayName"):
            if args.all or props.get(label):
                print("  %-22s %s" % (label, props.get(label, "")))
        if props.get("AUMID"):
            hits = registered(props["AUMID"])
            if hits:
                print("  Registered in %s:" % hits[0][0])
                for name, value in sorted(hits[0][2].items()):
                    print("      %-16s %s" % (name, value))
            else:
                print("  Registered            NO -- this is why the taskbar "
                      "falls back to the generic icon")
        return 0

    if args.action == "find":
        rows = enum_aumid_windows()
        print("%d visible window(s) with an AUMID" % len(rows))
        for row in rows:
            print("  hwnd=%-10d pid=%-7d %-28s %s"
                  % (row["hwnd"], row["pid"], row["props"]["AUMID"][:28],
                     row["title"][:44]))
        return 0

    if args.action == "status":
        if not args.target:
            ap.error("status needs an AUMID")
        hits = registered(args.target)
        if not hits:
            print("%s: NOT registered in HKCU or HKLM" % args.target)
            print("A running app with this AUMID cannot resolve a taskbar icon "
                  "unless it also sets RelaunchIconResource.")
            return 1
        for hive, path, values in hits:
            print("%s  %s" % (hive, path))
            for name, value in sorted(values.items()):
                print("    %-16s %s" % (name, value))
        return 0

    if args.action == "register":
        if not args.target:
            ap.error("register needs an AUMID")
        if not args.icon:
            ap.error("register needs --icon")
        icon = os.path.abspath(args.icon)
        if not os.path.exists(icon):
            print("icon path does not exist: %s" % icon, file=sys.stderr)
            return 2
        path = register(args.target, icon, args.name or None)
        print("registered HKCU\\%s" % path)
        print("  DisplayName = %s" % (args.name or args.target))
        print("  IconUri     = %s" % icon)
        print("The taskbar picks this up for newly created windows; restart the "
              "app (and Explorer if it persists) to see it.")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
