param([switch]$Apply)
$ErrorActionPreference = "Stop"
$exe = "C:\Users\Administrator\AppData\Local\Programs\DSH Desktop\DSH Desktop.exe"
Write-Output ("exe exists: " + (Test-Path $exe))
$procs = @(Get-Process | Where-Object { $_.ProcessName -like "*DSH*" -or ($_.Path -and $_.Path -like "*DSH Desktop*") })
Write-Output ("DSH 进程数: " + $procs.Count)
$procs | ForEach-Object { Write-Output ("  pid=" + $_.Id + " name=" + $_.ProcessName + " start=" + $_.StartTime) }
if (-not $Apply) { Write-Output "（只列不动，加 -Apply 才重启）"; exit 0 }

$main = $procs | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($null -eq $main) { $main = $procs | Sort-Object StartTime | Select-Object -First 1 }
Write-Output ("关闭主进程 pid=" + $main.Id)
Stop-Process -Id $main.Id -Force
Start-Sleep -Seconds 4
$left = @(Get-Process | Where-Object { $_.ProcessName -like "*DSH*" -or ($_.Path -and $_.Path -like "*DSH Desktop*") })
Write-Output ("残留进程: " + $left.Count)
Start-Process -FilePath $exe
Start-Sleep -Seconds 12
$now = @(Get-Process | Where-Object { $_.ProcessName -like "*DSH*" -or ($_.Path -and $_.Path -like "*DSH Desktop*") })
Write-Output ("重启后进程数: " + $now.Count)
$now | Sort-Object StartTime | Select-Object -First 3 | ForEach-Object { Write-Output ("  pid=" + $_.Id + " start=" + $_.StartTime) }
