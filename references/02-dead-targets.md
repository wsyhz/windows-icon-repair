# Dead targets: renames, moved drives, remaps

The most common real-world cause of mass icon breakage is not an icon problem at
all. A directory was renamed or moved, and every shortcut, registry value and
service path that referenced the old location kept the old string.

## Recognising it

Symptoms cluster. Instead of one broken icon you see a dozen or a hundred, all
created around the same time, all pointing into the same missing prefix:

```powershell
python scripts/diag_icons.py --csv scan.csv
# then group the failures by their missing prefix
Import-Csv scan.csv | Where-Object { $_.TargetExists -eq 'False' } |
    Group-Object { Split-Path $_.TargetPath -Parent } | Sort-Object Count -Descending
```

If one parent directory accounts for most of the failures, you have a rename,
not 29 independent bugs.

## Confirm the rename hypothesis before touching anything

The dangerous mistake is rewriting `A\x` to `B\x` on a hunch. Prove it first:
take every missing target, swap the prefix, and check that the result exists.

```powershell
$old = 'C:\_FromD'
$new = 'C:\FromD'
$broken = Import-Csv scan.csv | Where-Object { $_.TargetExists -eq 'False' }
foreach ($r in $broken) {
    $candidate = $r.TargetPath -replace [regex]::Escape($old), $new
    '{0,-12} {1}' -f (Test-Path $candidate), $candidate
}
```

Every line `True` means the hypothesis holds and a single remap repairs the
whole cluster. Any `False` means that entry has a *different* problem — do not
fold it into the remap.

`fix.py --remap` enforces exactly this: a rule is applied only when the
rewritten path exists on disk, so a wrong guess is skipped, never written.

## The repair is not only shortcuts

The same stale prefix lives in four more places, and each one causes a separate
symptom:

| Store | Symptom if left alone |
|---|---|
| `Uninstall` registry keys (`DisplayIcon`, `InstallLocation`) | Add/Remove Programs shows a blank icon |
| `App Paths` keys | `Win+R` cannot launch the app by name |
| `Services\*\ImagePath` | Service fails to start, error 2 / 3 |
| Scheduled tasks (`<Command>`) | Task fails silently at the next run |

Scan all of them, not just the visible icons:

```powershell
# registry: every string value under the software hives
reg query HKLM\SOFTWARE /s /f "_FromD" 2>$null
reg query HKCU\Software  /s /f "_FromD" 2>$null

# services only expose ImagePath in the registry
Get-ChildItem HKLM:\SYSTEM\CurrentControlSet\Services |
    ForEach-Object { (Get-ItemProperty $_.PSPath -Name ImagePath -EA 0).ImagePath } |
    Where-Object { $_ -like "*_FromD*" }

# scheduled tasks: the XML holds the real command line
Get-ScheduledTask | ForEach-Object {
    $x = Export-ScheduledTask -TaskName $_.TaskName
    if ($x -like "*_FromD*") { $_.TaskName }
}
```

## Traps in this repair

**Case and slash variants.** The same prefix appears as `C:\_FromD`,
`c:/_fromd`, `\Device\HarddiskVolume3\_FromD` and inside `REG_MULTI_SZ` arrays.
Match case-insensitively and handle both separators.

**`REG_MULTI_SZ` needs a multi-string write.** `Set-ItemProperty -Value` on an
array silently writes one joined string. Use:

```powershell
Set-ItemProperty $key -Name $name -Value $newArray -Type MultiString
```

**Filtering an array value needs a cast.** `$_.Value -like '*_FromD*'` is false
for `String[]`; coerce first: `"$($_.Value)" -like '*_FromD*'`.

**Scheduled-task XML cannot be round-tripped through `>`.** PowerShell
redirection writes UTF-8 bytes while the XML declaration still says
`encoding="UTF-16"`, so re-import fails with:

```
错误: 任务 XML 格式错误。
(1,2)::错误: 不正确的文档语法
```

Use `Export-ScheduledTask` → string replace → `WriteAllText` with a UTF-16 BOM:

```powershell
$xml = Export-ScheduledTask -TaskName $name
$new = $xml -replace '(?i)_fromd', 'FromD'
[System.IO.File]::WriteAllText($tmp, $new,
    (New-Object System.Text.UnicodeEncoding($false, $true)))
schtasks /Create /TN $name /XML $tmp /F
```

**A locked registry key can look like a permissions bug.** One `Run` value in
the original case refused every write — `Set-ItemProperty`, `SetValue`,
`reg add /f`, `reg import`, even creating a *new* value under that key — with:

```
The data area passed to a system call is too small.
```

while the parent key was fully writable and the ACL was clean. `DeleteValue`
returned success and the value came back. That is a filter driver or an
in-flight caller interfering with `ValueInfo` queries, not an ACL. Do not spend
an hour on ACLs: probe once (create a throwaway value under the same key — if
that also fails, it is not your value), record it as an unfixable residual, and
move on. A `rem|`-prefixed value is inert anyway.

## After the remap

1. Re-run `diag_icons.py`; the cluster should collapse to 0.
2. Re-scan for leftovers (`reg query ... /s /f`, task XML, environment `PATH`).
3. `python scripts/refresh_shell.py` to rebuild the icon cache.
4. Report the residuals honestly — a renamed prefix cannot resurrect a file the
   user deleted, and pretending otherwise is worse than saying "uninstalled".

## What a remap cannot fix

- The app was genuinely uninstalled (target directory gone, only config left).
- The target lives on a drive that is no longer attached.
- The prefix exists but the executable name changed.

In all three the correct outcome is either "reinstall" or "delete the
shortcut" — and the report should say which.
