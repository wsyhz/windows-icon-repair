# A worked case

This skill exists because of one machine. It is documented here because the
*shape* of the failure is common and the sequence of measurements is the
useful part — not the specific paths.

## The report

> "Icons in the Start menu keep turning into blank file icons because of AI
> misoperation. Go look and tell me which apps lost their icons."

A user's complaint like this bundles two different things: an observed symptom
(blank icons) and a hypothesis (an AI broke them). Both need testing. The
hypothesis was wrong, and chasing it would have wasted the whole session.

## Measurement 1 — count the failures

```powershell
python scripts/diag_icons.py --csv scan.csv
```

199 shortcuts in the two Start-menu roots. Rendering each at 256x256 and
classifying:

```
BLANK (6 white page) + (23 generic exe) = 29 broken, 169 OK
```

Note the split. Twenty-three of the twenty-nine were *not* the blank-page glyph
the user described. Visual inspection through a screenshot would have called
them all "broken icons" and lumped them together; the classifier separated two
different repair paths.

## Measurement 2 — find the correlation, not the cause

Group the failures, don't inspect them one by one:

```
27 of 29 broken targets begin with C:\_FromD\
```

That is the finding. Not "29 broken icons" but "one directory name is wrong in
27 places". Confirm before fixing:

```powershell
$old = 'C:\_FromD'; $new = 'C:\FromD'
# rewrite the prefix, then Test-Path every result
# 27/27 True  -> the hypothesis holds
```

`C:\_FromD` did not exist. `C:\FromD` did, with the contents intact. The
directory had been renamed and nothing that pointed into it was updated.

## Measurement 3 — the damage is wider than the icons

The same stale prefix lived in four more stores, each with its own symptom:

| Store | Count | Symptom |
|---|---|---|
| Shortcuts (all five roots) | 34 | the icons people saw |
| Registry values (HKCU + HKLM) | 156 | blank icons in Add/Remove Programs |
| Services (`ImagePath`) | 5 | services that would not start |
| Scheduled tasks | 1 | a database task that failed silently |
| Wallpaper / game config | 2 | wrong wallpaper after logon |

Fixing only the visible shortcuts would have left the machine half-repaired.

## The repair

```powershell
python scripts/fix.py --remap "C:\_FromD=C:\FromD" --apply --backup .\bak
python scripts/refresh_shell.py
```

Result: 34 shortcuts, 156 registry values, 5 services, 1 scheduled task.
Re-scan: `29 broken -> 0`.

## What could not be fixed, and why that matters

Four residuals were reported as-is rather than forced:

- **A folder shortcut** (`Saves.lnk` → a game's save directory) — a folder icon
  is the correct rendering. Not a bug.
- **Two website shortcuts** (`Visit ... Website`) — no icon file, so the blank
  page glyph is the default. Fixed by pointing `IconFile` at a browser, but they
  were never "broken".
- **VeraCrypt** — the shortcut's target drive is no longer attached. A remap
  cannot invent a drive; the honest answer is "reinstall or delete".
- **One locked registry value** — every write returned
  `The data area passed to a system call is too small.` even for a brand new
  value under the same key, while the parent key wrote fine. That is a filter
  driver, not an ACL. One probe was enough to prove it; twenty attempts would
  not have changed the answer. The value was `rem|`-prefixed and inert.

## The second bug, hidden behind the first

After the remap, the user reported the Steam taskbar button still showed the
generic glyph. A re-scan was clean — 266 OK, 2 explained — so the shortcut layer
was fine.

The cause was in a different chain:

```
AUMID Valve.Steam.Client  ->  not registered anywhere
RelaunchIconResource      ->  empty
ImplicitAppShortcuts\...\Steam.lnk -> D:\steam\steam.exe   (no D: drive)
```

All three fallbacks dead, so the shell drew the generic glyph. The pinned
`Steam.lnk` — which everyone had been "fixing" — was never consulted.

Repair: register the AUMID with a multi-size icon, repoint the implicit cached
shortcut, rebuild the cache.

## The lessons that became this repo

1. **Classify, don't eyeball.** Two of the three glyphs look like "broken" but
   need different fixes.
2. **Group before you look.** One prefix explained 27 of 29 failures. Fixing
   them individually would have taken hours and introduced errors.
3. **A running taskbar button and a shortcut are different problems.** This is
   the most expensive assumption in the domain.
4. **Prove the tool works before trusting it.** `selftest.py` exists because a
   silently broken detector reports a clean machine forever.
5. **Report what you cannot fix.** Four "failures" were correct behaviour, and
   one was a kernel-level block. Forcing them would have made things worse and
   made the report dishonest.
