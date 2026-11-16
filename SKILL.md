---
name: windows-icon-repair
description: Diagnose and repair missing, blank or generic Windows icons for shortcuts, Start-menu entries, desktop icons and taskbar buttons. Use when an icon turned into a blank white page, a plain folder, or the generic "application window" glyph, or when a pinned app shows the wrong icon while it is running. Works by rendering each icon the way Explorer does, comparing it against measured reference glyphs, and then repairing the specific link in the chain that broke.
---

# Windows icon repair

An icon is not stored in one place. It is the *result* of the shell walking a
chain of fallbacks, and it comes out blank when every link in that chain fails.
That is why "just recreate the shortcut" fixes some cases and does nothing for
others: it repairs one link and leaves the rest alone.

This skill identifies which link broke, then repairs only that one.

## Start here: identify the failure shape

The three glyphs users describe as "the icon disappeared" are three *different*
bugs with three different fixes. Look at what is actually drawn:

| What you see | Class | What it means |
|---|---|---|
| Blank white sheet of paper | `BLANK_FILE` | Nothing resolvable anywhere: no target, no icon file, no AUMID |
| Plain yellow folder | `FOLDER` | Target resolved to a directory, or the `.exe` is gone |
| White card + blue window | `GENERIC_EXE` | `.exe` exists but carries no icon resource |
| Plain app icon while running (taskbar) | AUMID bug | The *window* stamped an AUMID nothing registered |

Do not guess from memory. Render and measure:

```powershell
python scripts/diag_icons.py
```

If it prints `TOTAL=... OK=...`, trust the classification — but only after
running `python scripts/selftest.py` once on that machine, which proves the
detector can still see each failure mode. A detector that silently stopped
working reports "everything is fine" forever.

## The chain the shell walks, in order

For a **shortcut shown in a folder** (Start menu, desktop):

1. `IconLocation` in the `.lnk` / `IconFile` in the `.url`
2. the target executable's own icon resource
3. the file-type association for the extension
4. the generic blank-page or generic-exe glyph

For a **running app's taskbar button** — a completely different chain:

1. `HKCU|HKLM\Software\Classes\AppUserModelId\<AUMID>` → `IconUri`
2. the window's `System.AppUserModel.RelaunchIconResource` property
3. the cached shortcut under `...\User Pinned\ImplicitAppShortcuts\<hash>\`
4. the generic application-window glyph

Note what is *absent*: a running button never reads the visible pinned `.lnk`.
This is the single most common wrong assumption, and it makes people "fix" a
perfectly good shortcut while the taskbar keeps showing the generic icon.

## Repair

Always dry-run first. `fix.py` writes nothing without `--apply`:

```powershell
python scripts/fix.py --remap "OLD=NEW"
python scripts/fix.py --remap "OLD=NEW" --apply --backup .\bak
```

A remap rule only applies when the rewritten path **actually exists**, so a
wrong guess is ignored rather than written into the shortcut.

Read the matching reference before choosing a repair:

- `references/01-triage.md` — telling the four failure shapes apart, and the
  traps that invent fake breakage
- `references/02-dead-targets.md` — renames, moved drives, the remap rule
- `references/03-icon-location.md` — `IconLocation` / `IconFile` semantics,
  including the `.url` ordering bug
- `references/04-taskbar-aumid.md` — registering an AUMID, the implicit
  shortcut cache, why the pinned `.lnk` is ignored
- `references/05-permissions-and-cache.md` — `%ProgramData%` ACLs, elevation,
  and the three icon caches
- `references/06-verification.md` — proving the fix instead of assuming it

## Non-negotiables

- **Back up before writing.** `--apply` refuses to run without `--backup`.
- **Re-read after writing.** `IPersistFile::Save` can succeed while leaving a
  field stale; `fix.py` re-reads every field it changed and reports failures.
- **Never "fix" these three:** a shortcut to a real folder (folders *should*
  look like folders), a website `.url` with no icon (that blank page is the
  default and correct), and an app whose target is genuinely uninstalled
  (a remap cannot invent files — tell the user to reinstall or delete).
- **Do not clear the icon cache first.** `refresh_shell.py` without `--deep`
  rebuilds it without closing any Explorer window; the sledgehammer is only for
  a cache that provably refused to update.

## Requirements

Python 3.8+ with Pillow. Everything else is `ctypes` against `shell32`,
`user32`, `gdi32` and `ole32` — no pywin32, no comtypes, no WScript.Shell.
Windows 10/11.

`python scripts/diag_icons.py --csv scan.csv` for a sortable report, and
`python scripts/contact_sheet.py --out sheet.png --only-bad` for one image a
human can check at a glance.

## A worked example

The case this skill came from: 199 Start-menu shortcuts on one machine, 29 of
which rendered wrong. The cause was not the icons at all — a directory had been
renamed from `C:\_FromD` to `C:\FromD`, and every shortcut pointing inside it
still used the old name. A single remap rule fixed 34 shortcuts, 156 registry
values and 5 services; re-scanning went from 29 failures to 0. The remaining
taskbar bug was a different chain entirely (`references/04-taskbar-aumid.md`).
