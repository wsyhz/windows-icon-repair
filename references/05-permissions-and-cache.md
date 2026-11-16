# Permissions, elevation and the three icon caches

## Where shortcuts live, and who can write them

| Root | Path | Writable without admin |
|---|---|---|
| All users Start menu | `%ProgramData%\Microsoft\Windows\Start Menu\Programs` | **No** (owner `BUILTIN\Administrators`) |
| This user's Start menu | `%APPDATA%\Microsoft\Windows\Start Menu\Programs` | Yes |
| This user's desktop | `%USERPROFILE%\Desktop` | Yes |
| Public desktop | `%PUBLIC%\Desktop` | **No** |
| Taskbar pins | `%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned` | Yes |

Writing to an all-users shortcut as a normal user fails at `Save()`:

```
Unable to save shortcut
```

or, from PowerShell, `Access to the path ... is denied`. Detecting this properly
matters: an elevated process and an unelevated one see the *same* path, so a
script must check what it can actually do, not what it can see.

```powershell
([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
```

## Elevation without a UAC prompt per file

When a machine already has an elevated helper scheduled task (common on OEM
laptops), reuse it instead of spawning `runas`. The pattern that worked:

1. write a job file: `{"action": "<name>", "params": {}, "result": "<out path>"}`
2. `Start-ScheduledTask -TaskName <helper>`
3. poll the result file

The helper must restrict `action` to a safe name pattern or it becomes an
arbitrary-code-execution backdoor:

```powershell
if ($action -notmatch '^[a-z0-9\-]+$') { throw "bad action name" }
```

**Do not build this yourself if a normal `Start-Process -Verb RunAs` is
acceptable** — it prompts once and is honest about what it is doing. Silent
elevation helpers deserve suspicion.

A further wrinkle: an **unelevated** process cannot register a *new*
`RunLevel Highest` scheduled task (`Access is denied`), so "create my own helper"
is not an escape hatch — you need an existing one or one UAC prompt.

## The three caches

They are independent, and clearing the wrong one wastes effort or closes the
user's windows for nothing.

**1. Shell notification (instant, free).** `SHChangeNotify(SHCNE_ASSOCCHANGED)`
tells Explorer to re-read now. Always do this first — it fixes most cases on
its own.

**2. Icon cache database (fast, safe).** `ie4uinit.exe -show` rebuilds
`iconcache_*.db`. Run it after changing a `.lnk`, an association, or an AUMID.

**3. Manual purge (disruptive).** Deleting `iconcache*.db` / `thumbcache*.db`
from `%LOCALAPPDATA%\Microsoft\Windows\Explorer` while Explorer holds them open
requires killing the shell first, which **closes every Explorer window**. Only
do this when 1 and 2 provably failed.

```powershell
python scripts/refresh_shell.py          # 1 + 2
python scripts/refresh_shell.py --deep   # 1 + 2 + 3, asks first
```

## Symptoms that are actually permissions

- The fix "works" in a test copy but the real shortcut never changes — the real
  one is under `%ProgramData%`.
- A file shows a normal icon in Explorer's *details* view but a blank one in the
  desktop grid — different cache population paths, usually resolved by step 2.
- An icon reverts after a reboot — something (an installer, a sync client) is
  rewriting the shortcut, not the cache.

## Symptoms that are *not* permissions

Before escalating, confirm the process is not simply unable to *see* the file:

- **UIPI is not the issue** when both processes run at Medium integrity. Check
  the integrity level rather than assuming:

```powershell
whoami /groups | Select-String "Mandatory Label"
```

- **A locked registry key is not a denied ACL.** See the
  `data area passed to a system call is too small` note in
  `02-dead-targets.md`: probe by creating a throwaway value under the same key.
  If that also fails while the parent key writes fine, it is not a permission
  problem and no amount of `takeown` will help.

## Safety

Elevating a bulk rewrite of every shortcut on a machine is a genuinely risky
operation — a bug there can strip icons from hundreds of entries at once.
Always:

1. back up every file you are about to write, with an index mapping backup →
   original
2. dry-run and read the list
3. apply in the smallest scope that solves the problem
4. re-scan afterwards and report what is still broken
