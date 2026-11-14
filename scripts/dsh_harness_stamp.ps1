$ErrorActionPreference = 'Continue'
$p = Get-CimInstance Win32_Process -Filter "Name='node.exe'"
foreach ($x in $p) {
  if ($x.CommandLine -like '*harness-node-entry*' -or $x.CommandLine -like '*--port 43129*') {
    "pid=" + $x.ProcessId + " start=" + $x.CreationDate
    "  cmd=" + $x.CommandLine.Substring(0, [Math]::Min(150, $x.CommandLine.Length))
  }
}
