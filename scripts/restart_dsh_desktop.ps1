param([switch]$Apply)
$ErrorActionPreference = "Stop"
$exe = "C:\Users\Administrator\AppData\Local\Programs\DSH Desktop\DSH Desktop.exe"

# Restarting the shell is NOT restarting the harness. The harness is a separate node.exe
# running resources\harness-node-entry.mjs; it survives a shell restart (its parent dies),
# so the host plugins keep serving the code loaded at ITS boot. On 2026-10-10 that made a
# woken-but-stale plugin look like "the fix didn't work" for 64 minutes.
# Proof of "which version is loaded" = the rev line the plugin prints at boot in harness.log.
function Get-DshProcs {
  @(Get-CimInstance Win32_Process -Filter "Name = 'DSH Desktop.exe'") +
  @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'harness-node-entry' })
}

$all = Get-DshProcs
Write-Output ("DSH procs (shell + harness): " + $all.Count)
foreach ($p in $all) {
  $kind = if ($p.CommandLine -match 'harness-node-entry') { "HARNESS" } else { "shell" }
  Write-Output ("  pid=" + $p.ProcessId + " " + $kind + " created=" + $p.CreationDate)
}
if (-not $Apply) { Write-Output "(dry run; add -Apply to restart)"; exit 0 }

if (-not (Test-Path $exe)) { Write-Output ("EXE NOT FOUND: " + $exe); exit 1 }
foreach ($p in $all) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }

# Refuse to relaunch unless everything is truly gone: two shells fighting over one
# orphan harness shows up as "still failing after restart".
$n = 0
while ($n -lt 25) {
  if ((Get-DshProcs).Count -eq 0) { Write-Output ("CLEAN after " + $n + "s"); break }
  Start-Sleep -Seconds 1; $n++
}
if ($n -ge 25) { Write-Output "NOT-CLEAN -- refusing to relaunch, inspect leftover PIDs above"; exit 1 }

Start-Process -FilePath $exe
Start-Sleep -Seconds 25
$new = Get-DshProcs
Write-Output ("after relaunch: " + $new.Count)
foreach ($p in $new) {
  $kind = if ($p.CommandLine -match 'harness-node-entry') { "HARNESS" } else { "shell" }
  Write-Output ("  pid=" + $p.ProcessId + " " + $kind + " created=" + $p.CreationDate)
}
Write-Output "verify the loaded plugin rev: python scripts/check-form-wake.py  (group Z1)"
