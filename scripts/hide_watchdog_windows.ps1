param(
  [switch]$Check
)
$ErrorActionPreference = "Stop"
$VBS = "D:\video-skill\run_hidden.vbs"
$jobs = @(
  @{ Task = "WeeksirModelAdmin"; Cmd = "D:\video-skill\start_model_admin.cmd" },
  @{ Task = "WeeksirVideoUI";    Cmd = "D:\video-skill\start_video_ui.cmd" }
)
foreach ($j in $jobs) {
  $want = '//B //NoLogo "' + $VBS + '" "' + $j.Cmd + '"'
  $t = Get-ScheduledTask -TaskName $j.Task
  $cur = $t.Actions[0].Execute + " " + $t.Actions[0].Arguments
  if ($Check) {
    Write-Output ($j.Task + " action=" + $cur + " hidden=" + ($cur -like "*run_hidden.vbs*"))
    continue
  }
  if ($cur -like "*run_hidden.vbs*") { Write-Output ($j.Task + " already hidden"); continue }
  $a = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $want -WorkingDirectory "D:\video-skill"
  Set-ScheduledTask -TaskName $j.Task -Action $a | Out-Null
  Write-Output ($j.Task + " -> wscript + run_hidden.vbs")
}
