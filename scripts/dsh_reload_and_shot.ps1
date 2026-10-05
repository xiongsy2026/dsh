param(
  [string]$Out = "C:\dsh\.tmp-dsh-shot.png",
  [switch]$NoKey
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@

$p = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and $_.ProcessName -like "*DSH*" } | Select-Object -First 1
if ($null -eq $p) { Write-Output "NO-DSH-WINDOW"; exit 2 }
Write-Output ("window pid=" + $p.Id + " title=" + $p.MainWindowTitle)

if (-not $NoKey) {
  [void][W]::SetForegroundWindow($p.MainWindowHandle)
  Start-Sleep -Milliseconds 400
  $fg = [W]::GetForegroundWindow()
  $fgpid = 0
  [void][W]::GetWindowThreadProcessId($fg, [ref]$fgpid)
  if ($fg -ne $p.MainWindowHandle) { Write-Output ("ABORT foreground is not the DSH window (fgpid=" + $fgpid + ")"); exit 3 }
  [System.Windows.Forms.SendKeys]::SendWait("^r")
  Start-Sleep -Milliseconds 5000
}

$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output ("saved " + $Out + " " + $b.Width + "x" + $b.Height)
