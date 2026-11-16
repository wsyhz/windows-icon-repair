# -*- coding: utf-8 -*-
"""refresh_shell -- make Explorer notice that icons changed.

After rewriting a shortcut or registering an AUMID, the old icon can survive in
three separate caches. They have different lifetimes, so they are cleared in
order from cheapest to most disruptive:

    1. SHChangeNotify(SHCNE_ASSOCCHANGED)  -- ask the shell to re-read now
    2. ie4uinit.exe -show                  -- rebuild the icon *cache* files
    3. delete iconcache_*.db, restart      -- last resort, closes all windows

Doing 3 first, as most advice on the internet does, throws away the user's
Explorer windows and any unsaved File Explorer state for no reason.

CLI:
    python refresh_shell.py                 # step 1 + 2 (safe, instant)
    python refresh_shell.py --deep          # also purge caches and restart Explorer
    python refresh_shell.py --deep --yes    # skip the confirmation prompt
"""

import argparse
import ctypes
import glob
import os
import subprocess
import sys

user32 = ctypes.WinDLL("user32")
shell32 = ctypes.WinDLL("shell32")

SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000
SHCNF_FLUSH = 0x1000


def notify():
    """Tell the shell that file associations / icons changed."""
    shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST | SHCNF_FLUSH,
                           None, None)


def ie4uinit():
    """Rebuild the per-user icon cache database (what -show does)."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "ie4uinit.exe")
    if not os.path.exists(exe):
        return "not found: %s" % exe
    try:
        subprocess.run([exe, "-show"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
        return "ok"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "failed: %s" % exc


def cache_files():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Microsoft", "Windows", "Explorer")
    out = []
    for pattern in ("iconcache*.db", "thumbcache*.db"):
        out.extend(glob.glob(os.path.join(base, pattern)))
    return out


def explorer_pid():
    """The shell's PID, so we restart exactly one process."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq explorer.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, check=False)
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "explorer.exe":
            try:
                return int(parts[1])
            except ValueError:
                continue
    return None


def deep_refresh():
    """Delete icon caches and restart Explorer. Closes every Explorer window."""
    pid = explorer_pid()
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    removed = 0
    for path in cache_files():
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    # Restart the shell. Without a shell the desktop is gone, so this must
    # happen even if the cache deletion partially failed.
    subprocess.Popen(["explorer.exe"], close_fds=True)
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deep", action="store_true",
                    help="purge icon caches and restart Explorer")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = ap.parse_args(argv)

    notify()
    print("1/2 SHChangeNotify(SHCNE_ASSOCCHANGED) sent")
    print("2/2 ie4uinit -show: %s" % ie4uinit())

    if not args.deep:
        print()
        print("done. if an icon still looks wrong, re-run with --deep "
              "(this restarts Explorer and closes its windows).")
        return 0

    files = cache_files()
    print()
    print("--deep will:")
    print("  * kill explorer.exe")
    print("  * delete %d cache file(s) under %s"
          % (len(files), os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                      "Microsoft", "Windows", "Explorer")))
    print("  * start explorer.exe again")
    if not args.yes:
        answer = input("continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return 1

    removed = deep_refresh()
    print("deleted %d cache file(s); Explorer restarted" % removed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
