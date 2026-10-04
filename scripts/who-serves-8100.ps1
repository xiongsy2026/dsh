# Who is listening on 8100 right now? (PID + start time = the only proof of which code is running)
$conns = Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue
foreach ($c in ($conns | Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique)) {
  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$c"
  "pid=$c name=" + $p.Name + " start=" + $p.CreationDate + " exe=" + $p.ExecutablePath
}
