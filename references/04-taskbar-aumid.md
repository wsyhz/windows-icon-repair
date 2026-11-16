# Taskbar buttons: AUMID and the implicit shortcut cache

A running app's taskbar button is resolved by a chain that **never reads the
pinned `.lnk` on disk**. People fix the shortcut, see no change, and conclude
the fix failed. This is the single most expensive misunderstanding in this
domain.

## The chain

```
running process
   └─ window property  System.AppUserModel.ID  (AUMID)
        ├─ 1. HKCU|HKLM\Software\Classes\AppUserModelId\<AUMID>  → IconUri
        ├─ 2. window property RelaunchIconResource
        ├─ 3. ...\User Pinned\ImplicitAppShortcuts\<hash>\<Name>.lnk
        └─ 4. generic "application window" glyph   ← the reported bug
```

Any one of 1–3 being intact is enough. The generic glyph means all three failed.

## Measure the window, not the shortcut

```powershell
python scripts/aumid.py find                     # which windows declare an AUMID
python scripts/aumid.py window 0x40604           # inspect one window
```

Output that identifies this bug:

```
  AUMID                  Valve.Steam.Client
  RelaunchCommand        C:\FromD\steam\steam.exe
  RelaunchIconResource   (empty)
  Registered             NO -- this is why the taskbar falls back to ...
```

`RelaunchIconResource` empty **and** the AUMID unregistered is the signature.

A note on the AUMID string: it is not validated by the shell. If the process
sets `Valve.Steam.Client` but nothing registers that name, the shell has nowhere
to look. Apps like Chrome, Edge, QQ and Office also leave their AUMIDs
unregistered, yet their icons are fine — because they ship a full icon cache of
their own and set `RelaunchIconResource`. So "AUMID not registered" alone is not
a bug; it becomes one only when the icon also renders wrong.

## The implicit shortcut cache — the decisive find

```
%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\
        ImplicitAppShortcuts\<16-hex-hash>\<AppName>.lnk
```

When you pin a *running* app, Explorer does not pin the visible shortcut. It
creates this cached copy, which is what the taskbar reads to draw the button.
The hash directory is derived from the app's identity, so the file survives
blindly even when the world it points into has changed.

In the original case the chain was broken at all three levels:

| Level | State |
|---|---|
| 1. AUMID registry | `Valve.Steam.Client` not registered |
| 2. RelaunchIconResource | empty |
| 3. Implicit shortcut | pointed at `D:\steam\steam.exe` — **no D: drive on that machine** |

The visible pinned `.lnk` was perfect and irrelevant. That is why a clean
`diag_icons.py` scan coexisted with a broken taskbar button.

## Repair

Pick the cheapest level that is broken, and fix that one.

**Level 1 — register the AUMID** (no admin needed, `HKCU`):

```powershell
python scripts/make_ico.py --from-exe "C:\App\app.exe" --out "C:\App\app_256.ico"
python scripts/aumid.py register Valve.Steam.Client --icon "C:\App\app_256.ico" --name Steam
```

This writes `DisplayName` (REG_SZ) and `IconUri` (REG_EXPAND_SZ). Use a
multi-size `.ico`, not the `.exe` — the taskbar asks for 24, 32 and 48 px at
various scalings, and a single-size icon scales badly.

**Level 3 — fix or delete the implicit shortcut.** If you edit it, note that
`IconLocation` there is usually `,0`, which resolves against *its own*
`TargetPath`; so the target must be valid for the button to get an icon at all:

```python
import lnk
p = r"...\ImplicitAppShortcuts\d356105fac5527ef\Steam.lnk"
lnk.write(p, TargetPath=r"C:\FromD\steam\steam.exe",
             WorkingDirectory=r"C:\FromD\steam",
             IconLocation=r"C:\FromD\steam\public\steam_logo_256.ico,0")
```

If the app is genuinely gone, delete the cached shortcut instead of leaving a
dangling pointer.

**Level 2 — set `RelaunchIconResource`.** Possible via `IPropertyStore` on the
window, but this must be done by the app itself; changing it from outside after
the window exists does not always invalidate the cached button.

## Verifying

Window-level changes are only visible on a **newly created** window. Sequence:

1. register / repair
2. quit the app completely (check no `app.exe` or helper process remains —
   Steam's window belongs to `steamwebhelper.exe`, not `steam.exe`)
3. relaunch
4. screenshot the taskbar and crop the button region

Reading the icon out of the process is a useful sanity check that the binary is
not the problem:

```powershell
python scripts/aumid.py window <hwnd> --all
# WM_GETICON returning 0 for all of ICON_BIG/SMALL/SMALL2 is normal;
# GetClassLongPtr(-14) (GCLP_HICON) may still return a valid handle.
```

If the class icon renders as the real logo while the taskbar shows the generic
glyph, the binary is fine and the problem is purely the AUMID chain.

## Do not trust these short-circuits

- **The pinned `.lnk` icon.** Irrelevant to a running button.
- **`Taskband\Favorites`.** A binary blob; string-searching it for the app name
  finds nothing even when the pin is there.
- **`iconcache_*.db` timestamps.** A cache that was just rebuilt can still hold
  the generic glyph, because the glyph is the *answer*, not a stale entry.
- **`MainWindowHandle` on the launcher process.** Steam's is 0; the real window
  belongs to a child process. Enumerate by window class (`SDL_app`,
  `Chrome_WidgetWin_1`) or by title instead.
