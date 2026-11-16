# -*- coding: utf-8 -*-
"""diag_icons -- find every Windows shortcut whose icon does not render.

Scans the five shell roots that feed the Start menu, the desktop and the
taskbar, reads each shortcut's target, icon location and AUMID, renders the
256x256 icon the shell would draw, and classifies it. Output is a CSV you can
sort, plus a human summary on stdout.

What "broken" means here, precisely -- three different failures that users all
describe as "the icon went blank":

  BLANK_FILE   white sheet of paper. Nothing resolvable anywhere: no target, no
               icon file, no AUMID. This is the classic "空白文件图标".
  GENERIC_EXE  white card with a blue window. The .exe exists but carries no
               icon resource, so the shell falls back to its generic program
               glyph. Usually an application that was uninstalled badly.
  FOLDER       yellow folder. The target is a directory (sometimes correct --
               check before "fixing"), or the .exe vanished and the shell fell
               back to the container's representation.

Everything else is OK, including icons that merely look plain.

Note on scope: a shortcut can point at an existing file and still be dark --
and a shortcut can point at nothing and still look fine (if it hardcodes an
icon resource). Target existence and icon rendering are separate checks, so
both are reported.

CLI:
    python diag_icons.py                       # scan defaults, print summary
    python diag_icons.py --csv out.csv         # also write the full CSV
    python diag_icons.py --png out\\             # also dump each rendered icon
    python diag_icons.py --root "D:\\Some Dir"  # add a root
    python diag_icons.py --only-bad            # print only the broken ones
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import icore
import lnk


def default_roots():
    appdata = os.environ.get("APPDATA", "")
    programdata = os.environ.get("ProgramData", "")
    user = os.environ.get("USERPROFILE", "")
    public = os.environ.get("PUBLIC", "")
    return [
        ("StartMenu-ProgramData",
         os.path.join(programdata, r"Microsoft\Windows\Start Menu\Programs")),
        ("StartMenu-Roaming",
         os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs")),
        ("Desktop-User", os.path.join(user, "Desktop")),
        ("Desktop-Public", os.path.join(public, "Desktop")),
        # Both the pinned folder and the ImplicitAppShortcuts cache underneath
        # it matter: the taskbar resolves a running app's icon through the
        # cached shortcut, not through the visible pin.
        ("Pinned-TaskBar",
         os.path.join(appdata,
                      r"Microsoft\Internet Explorer\Quick Launch\User Pinned")),
    ]


def walk(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            low = name.lower()
            if low.endswith(".lnk") or low.endswith(".url"):
                yield os.path.join(dirpath, name)


def resolve_icon_file(info, base_dir):
    """Turn an IconLocation string into a concrete existing file, or ''."""
    candidate = (info.get("IconFile") or "").strip().strip('"')
    if not candidate:
        return ""
    # "%SystemRoot%\..." style values are legal in a shortcut.
    candidate = os.path.expandvars(candidate)
    if not os.path.isabs(candidate):
        candidate = os.path.join(base_dir, candidate)
    return candidate if os.path.isfile(candidate) else ""


def aumid_registered(aumid):
    """Is this AUMID declared anywhere the shell would look?

    Two hives matter, and checking only the per-user one produces a large pile
    of false alarms: Windows itself and many vendors register machine-wide under
    HKLM, and UWP/MSIX packages resolve through neither (their AUMIDs are
    derived from the package family name). So a miss here is a *hint*, not a
    verdict -- only an unregistered AUMID that also has no
    RelaunchIconResource actually costs you the icon.
    """
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Classes\AppUserModelId\%s" % aumid):
                return True
        except OSError:
            continue
    # UWP/MSIX AUMIDs look like "PackageFamilyName!AppId" or are 32-hex-ish
    # publisher ids ending in "!App"; they are registered by the AppX store.
    if "!" in aumid:
        return True
    return False


def diagnose(path, root_label, png_dir=None):
    row = {
        "Root": root_label,
        "Name": os.path.basename(path),
        "LnkPath": path,
        "Kind": "url" if lnk.is_url(path) else "lnk",
        "TargetPath": "",
        "TargetExists": "",
        "IconLocation": "",
        "IconFile": "",
        "IconFileExists": "",
        "AUMID": "",
        "AUMIDRegistered": "",
        "RelaunchIconResource": "",
        "Hash": "",
        "Method": "",
        "Index": "",
        "Class": "",
        "Note": "",
    }
    try:
        info = lnk.url_read(path) if lnk.is_url(path) else lnk.read(path)
    except OSError as exc:
        row["Class"] = "READ_ERROR"
        row["Note"] = str(exc)
        return row

    target = info.get("TargetPath") or ""
    row["TargetPath"] = target
    if target:
        row["TargetExists"] = os.path.exists(target)

    icon_loc = info.get("IconLocation") or ""
    row["IconLocation"] = icon_loc
    icon_file = resolve_icon_file(info, os.path.dirname(path))
    row["IconFile"] = info.get("IconFile") or ""
    row["IconFileExists"] = bool(icon_file)

    if not lnk.is_url(path):
        try:
            a = lnk.aumid(path)
            row["AUMID"] = a.get("AUMID", "")
            row["RelaunchIconResource"] = a.get("RelaunchIconResource", "")
        except OSError:
            pass
    if row["AUMID"]:
        row["AUMIDRegistered"] = aumid_registered(row["AUMID"])

    png_path = None
    if png_dir:
        os.makedirs(png_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in row["Name"])
        png_path = os.path.join(png_dir, safe + ".png")

    buf, how, idx = icore.render(path, png_path)
    row["Hash"] = icore.sha12(buf) or ""
    row["Method"] = how
    row["Index"] = idx
    row["Class"] = icore.classify(buf)

    # A note explaining *why*, so the CSV is self-contained.
    notes = []
    if row["Kind"] == "lnk" and target and row["TargetExists"] is False:
        notes.append("target missing")
    if not target and row["Kind"] == "lnk":
        notes.append("no target")
    if row["Class"] == "GENERIC_EXE":
        notes.append("exe has no icon resource")
    if row["Class"] == "BLANK_FILE":
        notes.append("nothing to render")
    if row["Class"] == "FOLDER" and row["TargetExists"]:
        notes.append("target is a real directory")
    if row["Kind"] == "url" and not row["IconFileExists"]:
        notes.append("url icon file missing")
    if (row["AUMID"] and row["AUMIDRegistered"] is False
            and not row["RelaunchIconResource"]):
        notes.append("AUMID unregistered, no relaunch icon")
    row["Note"] = "; ".join(notes)
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=[],
                    help="extra root directory to scan (repeatable)")
    ap.add_argument("--csv", default="", help="write full results to this CSV")
    ap.add_argument("--png", default="", help="dump rendered icons into this dir")
    ap.add_argument("--only-bad", action="store_true",
                    help="print only non-OK rows")
    args = ap.parse_args(argv)

    roots = default_roots() + [("Custom", r) for r in args.root]
    roots = [(label, r) for label, r in roots if r and os.path.isdir(r)]

    if not roots:
        print("no scannable roots found", file=sys.stderr)
        return 2

    rows = []
    for label, root in roots:
        for path in walk(root):
            rows.append(diagnose(path, label, args.png or None))

    counts = {}
    for row in rows:
        counts[row["Class"]] = counts.get(row["Class"], 0) + 1

    print("ROOTS")
    for label, root in roots:
        print("  %-22s %s" % (label, root))
    print()
    print("TOTAL=%d  %s" % (len(rows), "  ".join(
        "%s=%d" % (k, counts[k]) for k in sorted(counts))))
    print()

    bad = [r for r in rows if r["Class"] not in ("OK",)]
    if bad:
        print("NOT OK (%d)" % len(bad))
        for r in sorted(bad, key=lambda x: (x["Class"], x["Name"].lower())):
            print("  %-12s %-40s %s" % (r["Class"], r["Name"][:40], r["Note"]))
        print()
    else:
        print("all shortcuts render a real icon")
        print()

    if args.only_bad:
        rows = bad

    # Target existence is an independent axis: report it even when the icon is
    # fine, because a shortcut that silently fails to launch is its own bug.
    dangling = [r for r in rows if r["TargetExists"] is False]
    if dangling and not args.only_bad:
        print("TARGET MISSING (icon may still be fine) (%d)" % len(dangling))
        for r in sorted(dangling, key=lambda x: x["Name"].lower()):
            print("  %-40s -> %s" % (r["Name"][:40], r["TargetPath"]))
        print()

    # Only surface AUMID problems that can actually cost an icon: no failed
    # RelaunchIconResource fallback and, crucially, an icon that is already not
    # rendering. A registered-in-neither-hive AUMID is extremely common and
    # usually harmless (Chrome, QQ, Edge and Office all look fine without one),
    # so reporting those unconditionally trains the reader to ignore this tool.
    unreg = [r for r in rows
             if r["AUMID"] and r["AUMIDRegistered"] is False
             and not r["RelaunchIconResource"]
             and r["Class"] != "OK"]
    if unreg and not args.only_bad:
        print("AUMID UNREGISTERED *and* icon already broken (%d)" % len(unreg))
        for r in sorted(unreg, key=lambda x: x["Name"].lower()):
            print("  %-40s %s" % (r["Name"][:40], r["AUMID"]))
        print()

    if args.csv:
        cols = list(rows[0].keys()) if rows else []
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print("csv -> %s (%d rows)" % (args.csv, len(rows)))

    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
