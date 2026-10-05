param(
  [int]$X = 0,
  [int]$Y = 0,
  [string]$Out = "",
  [int]$Wait = 3,
  [string]$Hwnd = ""
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class M1 {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint d, uint e, uint t, IntPtr x);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out P p);
  [StructLayout(LayoutKind.Sequential)] public struct P { public int x; public int y; }
}
"@
if ($Hwnd -ne "") {
  [void][M1]::SetForegroundWindow([IntPtr][int]([int64]$Hwnd))
  Start-Sleep -Milliseconds 600
}
[void][M1]::SetCursorPos($X, $Y)
Start-Sleep -Milliseconds 250
$p = New-Object M1+P
[void][M1]::GetCursorPos([ref]$p)
if ($p.x -ne $X -or $p.y -ne $Y) { Write-Output ("ABORT cursor at " + $p.x + "," + $p.y + " not " + $X + "," + $Y); exit 3 }
[M1]::mouse_event(2, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 60
[M1]::mouse_event(4, 0, 0, 0, [IntPtr]::Zero)
Write-Output ("clicked " + $X + "," + $Y)
if ($Out -ne "") {
  Start-Sleep -Seconds $Wait
  $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
  $g.Dispose(); $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
  Write-Output ("saved " + $Out)
}
