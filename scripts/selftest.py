# -*- coding: utf-8 -*-
"""selftest -- prove the diagnostic can actually see a broken icon.

A detector that reports "everything is fine" is worthless unless you can show
it is not simply blind. This script manufactures every failure mode in a
throwaway folder and asserts that icore labels each one correctly, then asserts
that a healthy application icon is *not* flagged.

Run it before trusting any scan result:

    python selftest.py          # prints PASS/FAIL per case, exits 1 on failure
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import icore
import lnk

failures = []


def check(label, got, want):
    ok = got == want
    print("  %-34s %-12s %s" % (label, got, "PASS" if ok else "FAIL (want %s)" % want))
    if not ok:
        failures.append(label)


def main():
    tmp = tempfile.mkdtemp(prefix="icore_selftest_")
    try:
        # --- 1. the three failure glyphs, manufactured from real files -------
        blank = os.path.join(tmp, "empty.zzz")
        open(blank, "wb").close()

        folder = os.path.join(tmp, "dir_target")
        os.makedirs(folder)

        # A file that claims .exe but has no PE headers: what the shell sees when
        # a shortcut's target exe was deleted and something else took its name.
        fake_exe = os.path.join(tmp, "noicon.exe")
        with open(fake_exe, "wb") as fh:
            fh.write(b"MZ")

        # --- 2. a shortcut with no target, no icon, no AUMID -----------------
        # This is the classic "空白文件图标": nothing anywhere to render from.
        dead_lnk = os.path.join(tmp, "dead shortcut.lnk")
        lnk.create(dead_lnk, TargetPath="", IconLocation="", Description="dead")

        # --- 3. a shortcut whose target exists and carries a real icon ------
        good_lnk = os.path.join(tmp, "notepad.lnk")
        notepad = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                               "System32", "notepad.exe")
        lnk.create(good_lnk, TargetPath=notepad)

        print("baselines:")
        refs = icore.baselines(force=True)
        for name in ("BLANK_FILE", "FOLDER", "GENERIC_EXE"):
            s = icore.stats(refs[name])
            print("  %-12s %s ink=%-6d bbox=%s"
                  % (name, refs["HASH"][name], s["ink"], s["bbox"]))
        print()
        print("classification:")

        check("empty file -> BLANK_FILE",
              icore.classify(icore.render(blank)[0]), "BLANK_FILE")
        check("directory -> FOLDER",
              icore.classify(icore.render(folder)[0]), "FOLDER")
        check("iconless exe -> GENERIC_EXE",
              icore.classify(icore.render(fake_exe)[0]), "GENERIC_EXE")
        check("targetless .lnk -> BLANK_FILE",
              icore.classify(icore.render(dead_lnk)[0]), "BLANK_FILE")
        check("notepad.exe -> OK",
              icore.classify(icore.render(notepad)[0]), "OK")
        check("notepad.lnk -> OK",
              icore.classify(icore.render(good_lnk)[0]), "OK")

        # --- 4. a broad sample must not be flagged --------------------------
        # If the detector were trigger-happy, real icons would show up as
        # failures here. Sample the whole Start menu and require the false
        # positive rate to stay at zero for icons whose targets exist.
        root = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                            r"Microsoft\Windows\Start Menu\Programs")
        fp = 0
        checked = 0
        if os.path.isdir(root):
            for dirpath, _d, files in os.walk(root):
                for name in files:
                    if not name.lower().endswith(".lnk"):
                        continue
                    p = os.path.join(dirpath, name)
                    try:
                        info = lnk.read(p)
                    except OSError:
                        continue
                    target = info.get("TargetPath") or ""
                    if not target or not os.path.exists(target):
                        continue  # missing targets are legitimately suspect
                    if os.path.isdir(target):
                        continue  # a folder shortcut *should* look like a folder
                    checked += 1
                    cls = icore.classify(icore.render(p)[0])
                    if cls != "OK":
                        fp += 1
                        print("    unexpected flag: %-12s %s" % (cls, name))
        print()
        check("false positives on %d live shortcuts" % checked, fp, 0)
    finally:
        icore.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print("FAILED: %s" % ", ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
