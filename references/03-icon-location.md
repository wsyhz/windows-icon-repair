# IconLocation, IconFile, and the .url ordering bug

## The two fields

A `.lnk` stores an icon source and an index:

```
IconLocation = "C:\Apps\Thing\Thing.exe,0"
               \_________________/  \/
                 IconFile           IconIndex
```

- **`IconFile` empty** means "use the target's own icon". That is the normal,
  correct configuration for most shortcuts. `IconLocation = ",0"` is *not* a
  bug — it is an explicit "take the icon from the target".
- **`IconFile` set** means "ignore the target, load this file". Useful when the
  target is `powershell.exe` or a stub with no icon of its own.

So when diagnosing, never flag an empty `IconLocation` by itself. Flag it only
when the rendered result is also wrong.

## Failure modes, in order of frequency

**1. IconFile points at a path that no longer exists.** After a directory
rename this is the same stale-prefix problem as the target. `diag_icons.py`
reports `IconFileExists` separately for exactly this reason — a shortcut can
have a perfectly valid target and still render blank because only its icon
pointer rotted.

**2. IconFile points at an exe that has no icon resource.** Very common for
command-line tools and for `llama.cpp`-style builds whose launcher is a thin
stub. Verify with `FindResourceW(h, RT_ICON=3, ...)`; if both `RT_ICON` and
`RT_GROUP_ICON=14` are absent, no amount of path fixing helps. Supply an `.ico`:

```powershell
python scripts/make_ico.py --from-image logo.png --out app.ico
python scripts/lnk.py set "C:\path\app.lnk" --icon "C:\path\app.ico,0"
```

**3. IconIndex out of range.** An exe with one icon but `IconLocation = "x.exe,5"`
renders blank. Drop the index to `,0`.

**4. IconFile is a relative path.** `DefaultIcon = steam.exe` (no directory)
resolves against the *current* working directory, so it works sometimes and not
others. Always write absolute paths.

## .url files (Internet shortcuts)

A `.url` is an INI file, not a binary shell link:

```ini
[InternetShortcut]
URL=https://example.com/
IconFile=C:\path\to\icon.ico
IconIndex=0
```

**The ordering bug.** Real-world files are not written in a fixed order —
Steam, for instance, writes `IconIndex` *before* `IconFile`. A parser that
assembles `IconLocation` as it walks the lines will emit `",0"` and silently
discard the icon path. Read both values into locals, then combine after the
loop:

```python
info["IconLocation"] = "%s,%s" % (icon_file, icon_index)
```

This exact bug shipped in the first version of `lnk.py` in this repo and made
three Steam shortcuts look permanently unfixable.

**A `.url` with no `IconFile` renders as a blank page — and that is correct.**
"Visit vendor website" shortcuts are supposed to look like that. If the user
wants a real icon, point `IconFile` at the vendor's browser or launcher exe:

```powershell
python scripts/lnk.py set "C:\...\Visit MobaXterm Website.lnk" --icon "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe,0"
```

## Writing a shortcut safely

`IPersistFile::Save` can succeed while a field stays stale. Always re-read:

```python
before = lnk.read(path)
lnk.write(path, IconLocation=new)
after = lnk.read(path)
assert after["IconLocation"].lower() == new.lower(), "write did not stick"
```

`fix.py --apply` does this for every field and counts a mismatch as a failure,
because a silent partial write is worse than a loud error.

## Permissions

Shortcuts under `%ProgramData%\Microsoft\Windows\Start Menu\Programs` are owned
by `BUILTIN\Administrators`; `BUILTIN\Users` has read-only access. Saving there
as a normal user fails with:

```
Unable to save shortcut
```

The user's own `%APPDATA%` Start menu, Desktop and pinned-taskbar folders are
writable without elevation. See `05-permissions-and-cache.md`.
