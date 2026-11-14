param(
  [string]$Hwnd = "",
  [string]$Keys = "^+i",
  [int]$Wait = 5,
  [string]$Out = "C:\dsh\.tmp-keys.png"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Kk {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}
"@
if ($Hwnd -eq "") { Write-Output "ABORT-no-hwnd"; exit 2 }
$h = [IntPtr][int]([int64]$Hwnd)
[void][Kk]::ShowWindow($h, 9)
[void][Kk]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 900
if ([Kk]::GetForegroundWindow() -ne $h) {
  Write-Output ("ABORT foreground is NOT the target (fg=" + [int64][Kk]::GetForegroundWindow() + " want=" + [int64]$h + ")")
  exit 3
}
[System.Windows.Forms.SendKeys]::SendWait($Keys)
Write-Output ("sent " + $Keys + " to hwnd " + [int64]$h)
Start-Sleep -Seconds $Wait
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
$g.Dispose(); $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
Write-Output ("saved " + $Out)
