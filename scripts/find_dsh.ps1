$ErrorActionPreference = "SilentlyContinue"
$p = Get-Process -Id 55152
if ($p) { Write-Output ("55152 alive name=" + $p.ProcessName) } else { Write-Output "55152 gone" }
Write-Output "== processes whose name or path mentions DSH =="
Get-CimInstance Win32_Process | Where-Object { $_.Name -match "DSH" -or $_.ExecutablePath -like "*DSH Desktop*" } | ForEach-Object {
  $cl = $_.CommandLine
  if ($cl -and $cl.Length -gt 150) { $cl = $cl.Substring(0, 150) }
  Write-Output ("  pid=" + $_.ProcessId + " name=" + $_.Name + " cmd=" + $cl)
}
Write-Output "== windowed processes with harness-ish titles =="
Get-Process | Where-Object { $_.MainWindowTitle -match "DeepSeek|WEEKSIR|Harness" } | ForEach-Object {
  Write-Output ("  pid=" + $_.Id + " name=" + $_.ProcessName + " title=" + $_.MainWindowTitle) }
