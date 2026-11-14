param(
  [switch]$Check
)
# 🔴 路径不再写死 D:\video-skill：仓库克隆后根目录就是工作区，后端在 <repo>\backend。
#    计划任务指向的 start_model_admin.cmd / start_video_ui.cmd / run_hidden.vbs 是旧机器上的
#    遗留启动件；新机器上常驻服务由 backend\start-backend.ps1 统一起（可手动建任务指向它）。
$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
$VS = if ($env:VIDEO_SKILL_HOME) { $env:VIDEO_SKILL_HOME } else { Join-Path $Repo "backend" }
$VBS = Join-Path $VS "run_hidden.vbs"
$jobs = @(
  @{ Task = "WeeksirModelAdmin"; Cmd = Join-Path $VS "start_model_admin.cmd" },
  @{ Task = "WeeksirVideoUI";    Cmd = Join-Path $VS "start_video_ui.cmd" }
)
foreach ($j in $jobs) {
  if (-not (Test-Path $j.Cmd)) { Write-Output ($j.Task + " 跳过：启动件不存在 " + $j.Cmd); continue }
  $want = '//B //NoLogo "' + $VBS + '" "' + $j.Cmd + '"'
  $t = Get-ScheduledTask -TaskName $j.Task -ErrorAction SilentlyContinue
  if (-not $t) { Write-Output ($j.Task + " 不存在（新机器可不建；后端由 start-backend.ps1 起）"); continue }
  $cur = $t.Actions[0].Execute + " " + $t.Actions[0].Arguments
  if ($Check) {
    Write-Output ($j.Task + " action=" + $cur + " hidden=" + ($cur -like "*run_hidden.vbs*"))
    continue
  }
  if ($cur -like "*run_hidden.vbs*") { Write-Output ($j.Task + " already hidden"); continue }
  $a = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $want -WorkingDirectory $VS
  Set-ScheduledTask -TaskName $j.Task -Action $a | Out-Null
  Write-Output ($j.Task + " -> wscript + run_hidden.vbs")
}
