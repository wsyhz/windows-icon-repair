# Verification: proving the fix instead of assuming it

"Looks right in one screenshot" is not verification. Three things can make a
correct fix look broken, and three things can make a broken fix look correct.

## The only trustworthy measurement

Render the icon **the way Explorer renders it**, at 256x256, and compare
against reference glyphs measured on the same machine at the same moment.

```powershell
python scripts/diag_icons.py --csv after.csv
python scripts/contact_sheet.py --out sheet.png --only-bad
```

Then look at `sheet.png` with your own eyes. The classifier narrows the field;
it does not replace seeing it.

## Never hardcode a baseline

The generic glyphs differ by Windows build, theme, and DPI. A hash recorded on
another machine — or on this machine before a feature update — produces
confident nonsense. `icore.baselines()` measures them at runtime by rendering
three real temporary files:

```
probe.zzz    -> blank-page glyph
probe_dir\   -> folder glyph
probe.exe    -> generic-exe glyph   (a file containing only "MZ")
```

They must be **real files**. `SHCreateItemFromParsingName` returns
`0x80070002` for a path that does not exist, so the `SHGFI_USEFILEATTRIBUTES`
trick of describing a hypothetical path does not work through this API.

## Prove the detector is not blind

A broken detector and a healthy system both report "no problems". Run:

```powershell
python scripts/selftest.py
```

It builds a temporary shortcut set containing one of each failure shape and
asserts the classifier names each one correctly, then measures the false-positive
rate over the machine's real Start-menu shortcuts (files whose target exists and
is not a directory) and requires zero.

If `selftest.py` fails, **stop** — every number from `diag_icons.py` is
meaningless until it passes.

## Three traps that fake a failure

**1. A maximized window covering the desktop.** Minimize or move it before
capturing. A full-screen app makes most of the desktop invisible, and the empty
region is easy to mistake for blank icons.

**2. A stale frame.** Take two captures a second apart and compare hashes. If
they are identical after an action that should have changed the screen, you are
photographing a cache, not the desktop.

**3. DPI virtualisation.** A DPI-unaware process sees virtualised coordinates
(e.g. 1707x1067 instead of 2560x1600 at 150% scaling). Screenshots look
squashed and clicks land in the wrong place, which reads as "the UI is frozen".

```python
import ctypes
ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
```

Capture and click coordinates must both be in physical pixels, or neither.

## Three traps that fake a success

**1. Testing on a copy.** If you proved the fix on a temp copy but the original
lives under `%ProgramData%`, you proved nothing. Confirm the file under test is
the file the shell reads.

**2. Save() without re-read.** `IPersistFile::Save` can return `S_OK` and leave
a field unchanged. Always re-read and compare, per field.

**3. Fixing the visible shortcut for a taskbar bug.** The running button ignores
it (`04-taskbar-aumid.md`). A clean scan plus a bad taskbar button is a normal,
expected combination.

## Reporting

Give the user numbers they can re-derive, and separate the three outcomes:

```
scanned    267 shortcuts across 5 roots
repaired    34 shortcuts, 156 registry values, 5 services, 1 scheduled task
remaining    2   Saves.lnk          target is a real folder  -> correct as-is
                 WallpaperEngine.lnk target not installed     -> app uninstalled
unfixable    1   HKCU\...\LenovoDisabled\WallpaperEngine
                 every write returns "data area too small";
                 a probe value under the same key fails too, so it is a kernel
                 filter, not an ACL. The value is rem|-prefixed and inert.
```

An honest "cannot fix, and here is why" is far more useful than a fifth attempt
at the same write. Distinguish:

- **fixed** — re-scan is clean
- **correct as-is** — folder targets, iconless websites
- **uninstalled** — nothing to point at; offer delete or reinstall
- **blocked** — prove it with one probe, then stop
