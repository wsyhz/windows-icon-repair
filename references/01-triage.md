# Triage: which failure is this?

Four shapes, four chains, four fixes. Getting the shape wrong is the main
reason people spend an hour on the wrong cause.

## The four shapes

### 1. Blank white sheet of paper — `BLANK_FILE`

Nothing in the chain resolved. Concretely the shortcut has no `IconLocation`,
its target does not exist (or has none), and there is no icon resource to
inherit. Explorer draws its "unknown file" glyph.

Check:

```powershell
python scripts/fix.py --url "C:\path\to\thing.lnk"
```

If `TargetPath` is empty *and* `IconLocation` is empty, the shortcut has nothing
to point at. Either its target was uninstalled, or the shortcut was created
against a path that never existed.

### 2. Plain yellow folder — `FOLDER`

The shell resolved the target to a **directory**. Two very different
situations:

- *Correct.* A shortcut whose whole purpose is to open a folder
  (`Saves.lnk` → a game's save directory). Leave it alone.
- *Wrong.* The shortcut pointed at `C:\Apps\Thing\Thing.exe`, the app was
  uninstalled, and what remains at that path is now a folder — or the shortcut
  points at the install directory rather than the executable.

Always check `TargetExists` and whether the target is a directory before
"fixing" it. `diag_icons.py` reports both.

### 3. White card with a blue window — `GENERIC_EXE`

The target `.exe` exists but carries no icon resource, so the shell falls back
to its generic program glyph. This is the shape most often misdiagnosed as "the
icon got deleted".

Verify independently that the exe really is iconless — do not trust the
rendering alone:

```python
# A real icon resource has RT_GROUP_ICON (14) or RT_ICON (3).
import ctypes
k32 = ctypes.WinDLL("kernel32")
h = k32.LoadLibraryExW(exe, None, 0x22)   # AS_DATAFILE | AS_IMAGE_RESOURCE
grp = k32.FindResourceW(h, ctypes.c_wchar_p(14), ctypes.c_wchar_p(1))
ico = k32.FindResourceW(h, ctypes.c_wchar_p(3), ctypes.c_wchar_p(1))
```

Both `None` means the exe has no icon at all — common for CLI tools and for
`llama.cpp`-style builds. No shortcut edit can fix this; you must supply an
`.ico` (`scripts/make_ico.py`).

### 4. Generic app icon on a *running* taskbar button

Different chain entirely — see `04-taskbar-aumid.md`. The visible pinned `.lnk`
is not consulted.

## What "icon broken" is *not*

Before scanning, rule these out. Each has produced a false report in practice.

**A collapsed screen region.** A maximized window covering the desktop hides
icons behind it. Screenshot the desktop with the window minimized before
concluding anything. (In the original case, a maximized chat app covered the
whole screen and its "empty" region was interpreted as missing icons.)

**A stale screenshot.** Compare two captures a second apart. If the hashes are
identical after you clicked something, you are looking at a cached frame, not a
live desktop.

**DPI virtualisation.** A DPI-unaware process gets virtualised coordinates
(commonly 1707x1067 for a 2560x1600 screen at 150%). Clicks land in the wrong
place and the screen appears frozen. Any screenshot or input code must call:

```python
ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
```

**Scanning only `%ProgramData%`.** Three more roots feed the UI and each is
independent:

- `%APPDATA%\Microsoft\Windows\Start Menu\Programs`
- `%USERPROFILE%\Desktop` and `%PUBLIC%\Desktop`
- `%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned`
  (including the `ImplicitAppShortcuts` subfolder — see note 04)

**Theme-dependent reference icons.** The generic glyphs differ between Windows
versions and light/dark themes. Never hardcode a baseline hash from another
machine; `icore.baselines()` measures them at runtime from real temporary files.

**A blank icon that is correct.** A website `.url` with no `IconFile` renders as
the blank page glyph by design. So does an empty text file. Both are not bugs.

## Order of operations

1. `python scripts/selftest.py` — prove the detector is not blind.
2. `python scripts/diag_icons.py --csv scan.csv` — classify everything.
3. `python scripts/contact_sheet.py --out sheet.png --only-bad` — look at the
   failures with your own eyes before changing anything.
4. Only then read the reference for the shape you actually have.
