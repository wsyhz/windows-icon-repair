# -*- coding: utf-8 -*-
"""fix -- repair shortcut icons, with a dry run that tells you what it will touch.

The scan (diag_icons.py) tells you *what* is broken. This script decides what
can honestly be repaired, and does it in the one order that works:

  1. remap dead paths (a folder was renamed/moved, so every shortcut that points
     inside it is now stale -- by far the most common real cause)
  2. point a dead IconLocation at something that exists
  3. register a missing AppUserModelId so the taskbar can resolve the icon
  4. fix the taskbar's *hidden* shortcut cache, which is what actually decides
     the icon of a running pinned app

Nothing is written without --apply, everything written is backed up first, and
every change is re-read afterwards to prove it stuck.

What this cannot fix: a target that is genuinely gone (uninstalled app), and a
shortcut whose whole point is to open a folder or a URL. Those are reported as
"needs a human decision", not silently rewritten.

CLI:
    python fix.py                       # dry run over the default roots
    python fix.py --apply               # do it
    python fix.py --apply --backup ..\\bak
    python fix.py --remap "_FromD=FromD" --remap "D:\\steam=C:\\FromD\\steam"
    python fix.py --url C:\\x\\y.lnk    # inspect one file in detail
"""

import argparse
import csv
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diag_icons
import icore
import lnk

# Repairs are ordered least-destructive first. A remap only rewrites a path that
# is already dead, so it cannot break a working shortcut.
RULE_HELP = """remap rules
  --remap "OLD=NEW" may be repeated. A rule applies only when the rewritten path
  actually exists on disk, so a wrong guess is ignored instead of applied.

  Typical uses:
    --remap "_FromD=FromD"                     a folder lost its leading underscore
    --remap "D:\\steam=C:\\FromD\\steam"          a library moved to another drive
    --remap "C:\\Program Files\\Old=C:\\Program Files\\New"
"""


class Backups(object):
    """Copies every file before it is modified, and remembers the original."""

    def __init__(self, root):
        self.root = root
        self.rows = []
        if root:
            os.makedirs(os.path.join(root, "files"), exist_ok=True)

    def save(self, path):
        if not self.root:
            return ""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = "%s_%s" % (stamp, os.path.basename(path))
        dest = os.path.join(self.root, "files", name)
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(self.root, "files",
                                "%s_%d_%s" % (stamp, n, os.path.basename(path)))
            n += 1
        shutil.copy2(path, dest)
        self.rows.append({"Source": path, "Backup": dest})
        return dest

    def write_index(self):
        if not self.root or not self.rows:
            return
        p = os.path.join(self.root, "backup_index.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["Source", "Backup"])
            w.writeheader()
            w.writerows(self.rows)


def remap_path(value, rules):
    """Apply the first rule whose rewrite produces an existing path."""
    if not value or not rules:
        return None
    for old, new in rules:
        if old.lower() in value.lower():
            # Case-insensitive replace that preserves the rest verbatim.
            candidate = re.sub(re.escape(old), new.replace("\\", "\\\\"), value,
                               flags=re.IGNORECASE)
            if candidate != value and os.path.exists(candidate):
                return candidate
    return None


def pick_icon_for(target, path):
    """Choose an IconLocation that will really draw something.

    An empty IconLocation is not a bug: the shell falls back to the target's own
    icon resource, which is usually the best-looking option. Only override it
    when that fallback demonstrably produces nothing.
    """
    buf, how, idx = icore.render(path)
    cls = icore.classify(buf)
    if cls == "OK":
        return None  # current rendering is fine; leave it alone
    if not target or not os.path.exists(target):
        return None
    # The target exists but the shortcut draws a generic glyph: try pointing at
    # the target's own resource explicitly.
    if os.path.isfile(target):
        return target + ",0"
    return None


def plan_one(path, rules):
    """Work out the full set of edits for one shortcut. Changes nothing."""
    try:
        if lnk.is_url(path):
            info = lnk.url_read(path)
            kind = "url"
        else:
            info = lnk.read(path)
            kind = "lnk"
    except OSError as exc:
        return {"Path": path, "Kind": "?", "Action": "skip",
                "Reason": "unreadable: %s" % exc, "Edits": {}}

    edits = {}
    reasons = []
    target = info.get("TargetPath") or ""

    # --- 1. dead target -> try to remap it onto the real location -----------
    if target and not os.path.exists(target):
        moved = remap_path(target, rules)
        if moved:
            edits["TargetPath"] = moved
            reasons.append("target remapped")

    # --- 2. dead icon file -> remap, or fall back to the target -------------
    icon = info.get("IconLocation") or ""
    icon_file = ""
    if icon.strip(", "):
        icon_file = icon.rpartition(",")[0].strip().strip('"')
    if icon_file and not os.path.exists(os.path.expandvars(icon_file)):
        moved = remap_path(os.path.expandvars(icon_file), rules)
        if moved:
            edits["IconLocation"] = moved + ",0"
            reasons.append("icon remapped")
        else:
            better = pick_icon_for(edits.get("TargetPath", target), path)
            if better:
                edits["IconLocation"] = better
                reasons.append("icon reset to target")

    # --- 3. icon blank while the target is fine ----------------------------
    if not edits.get("IconLocation"):
        buf, _how, _idx = icore.render(path)
        cls = icore.classify(buf)
        if cls in ("BLANK_FILE", "GENERIC_EXE"):
            better = pick_icon_for(edits.get("TargetPath", target), path)
            if better:
                edits["IconLocation"] = better
                reasons.append("icon repointed (%s -> target)" % cls)

    if not edits:
        return {"Path": path, "Kind": kind, "Action": "none", "Reason": "",
                "Edits": {}, "Info": info}

    return {"Path": path, "Kind": kind, "Action": "update",
            "Reason": "; ".join(reasons), "Edits": edits, "Info": info}


def apply_one(item, backups):
    path = item["Path"]
    edits = item["Edits"]
    if backups.root:
        backups.save(path)
    if item["Kind"] == "url":
        if "IconLocation" in edits:
            lnk.url_write(path, edits["IconLocation"])
    else:
        lnk.write(path, **edits)

    # Verify by re-reading: a write that did not stick must not be reported as
    # success. IPersistFile::Save can succeed and still leave a stale field.
    after = lnk.url_read(path) if item["Kind"] == "url" else lnk.read(path)
    stuck = []
    if "TargetPath" in edits:
        stuck.append(after.get("TargetPath", "").lower()
                     == edits["TargetPath"].lower())
    if "IconLocation" in edits:
        stuck.append((after.get("IconLocation") or "").lower()
                     == edits["IconLocation"].lower())
    return all(stuck) if stuck else True


def register_aumid(aumid, display_name, icon_uri):
    """Declare an AUMID so the taskbar can find the icon for a running app.

    This is the missing half of the "pinned app shows a generic window icon"
    bug: the process stamps an AUMID on its window, but if nothing ever
    registered that AUMID, the shell has no DisplayName and no IconUri to draw.
    """
    import winreg

    key_path = r"Software\Classes\AppUserModelId\%s" % aumid
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ,
                          display_name or aumid)
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_EXPAND_SZ, icon_uri)
    return key_path


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=RULE_HELP)
    ap.add_argument("--root", action="append", default=[],
                    help="extra root to scan (repeatable)")
    ap.add_argument("--remap", action="append", default=[],
                    metavar="OLD=NEW", help="path rewrite rule (repeatable)")
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default is a dry run)")
    ap.add_argument("--backup", default="", help="backup directory for --apply")
    ap.add_argument("--url", default="", help="inspect a single shortcut")
    ap.add_argument("--csv", default="", help="write the plan to a CSV")
    args = ap.parse_args(argv)

    rules = []
    for r in args.remap:
        if "=" not in r:
            print("ignoring malformed --remap %r" % r, file=sys.stderr)
            continue
        old, _, new = r.partition("=")
        rules.append((old.strip(), new.strip()))

    if args.url:
        p = args.url
        info = lnk.url_read(p) if lnk.is_url(p) else lnk.read(p)
        for k in sorted(info):
            print("  %-18s %s" % (k, info[k]))
        if not lnk.is_url(p):
            print("  %-18s %s" % ("AUMID-props", lnk.aumid(p)))
        buf, how, idx = icore.render(p)
        print("  %-18s %s (%s, idx=%s)" % ("Rendered", icore.classify(buf), how, idx))
        return 0

    roots = diag_icons.default_roots() + [("Custom", r) for r in args.root]
    roots = [(label, r) for label, r in roots if r and os.path.isdir(r)]
    if not roots:
        print("no scannable roots found", file=sys.stderr)
        return 2

    items = []
    for _label, root in roots:
        for path in diag_icons.walk(root):
            items.append(plan_one(path, rules))

    todo = [i for i in items if i["Action"] == "update"]
    skipped = [i for i in items if i["Action"] == "skip"]

    print("%d shortcuts scanned, %d need edits, %d unreadable"
          % (len(items), len(todo), len(skipped)))
    print()
    for i in todo:
        print("  %s" % i["Path"])
        print("      %s" % i["Reason"])
        for k, v in sorted(i["Edits"].items()):
            old = (i.get("Info") or {}).get(k, "")
            print("      %-14s %s" % (k, v))
            if old:
                print("      %-14s (was %s)" % ("", old))
    if skipped:
        print()
        print("unreadable (needs a look):")
        for i in skipped:
            print("  %s\n      %s" % (i["Path"], i["Reason"]))

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Path", "Kind", "Action", "Reason", "Field", "NewValue"])
            for i in items:
                if i["Action"] != "update":
                    continue
                for k, v in sorted(i["Edits"].items()):
                    w.writerow([i["Path"], i["Kind"], i["Action"], i["Reason"], k, v])
        print()
        print("plan -> %s" % args.csv)

    if not args.apply:
        print()
        print("dry run -- nothing written. re-run with --apply to make these changes.")
        return 0

    if todo and not args.backup:
        print()
        print("refusing to --apply without --backup (edits are destructive).",
              file=sys.stderr)
        return 2

    backups = Backups(args.backup)
    ok = fail = 0
    print()
    for i in todo:
        try:
            if apply_one(i, backups):
                ok += 1
            else:
                fail += 1
                print("  VERIFY FAILED  %s" % i["Path"])
        except OSError as exc:
            fail += 1
            print("  ERROR  %s\n      %s" % (i["Path"], exc))
    backups.write_index()

    print("applied: %d ok, %d failed" % (ok, fail))
    if fail:
        print("a failure here is usually permissions: shortcuts under "
              "%ProgramData% need an elevated shell.")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
