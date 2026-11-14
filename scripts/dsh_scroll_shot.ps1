param(
  [int]$X = 1300,
  [int]$Y = 600,
  [int]$Notches = 6,
  [string]$Hwnd = "",
  [string]$Out = "C:\dsh\.tmp-fold-scroll.png"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Sc {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint d, uint e, int t, IntPtr x);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out P p);
  [StructLayout(LayoutKind.Sequential)] public struct P { public int x; public int y; }
}
"@
if ($Hwnd -ne "") {
  [void][Sc]::SetForegroundWindow([IntPtr][int]([int64]$Hwnd))
  Start-Sleep -Milliseconds 700
  if ([Sc]::GetForegroundWindow() -ne [IntPtr][int]([int64]$Hwnd)) { Write-Output "ABORT-not-foreground"; exit 3 }
}
[void][Sc]::SetCursorPos($X, $Y)
Start-Sleep -Milliseconds 250
$p = New-Object Sc+P
[void][Sc]::GetCursorPos([ref]$p)
if ($p.x -ne $X -or $p.y -ne $Y) { Write-Output ("ABORT cursor=" + $p.x + "," + $p.y); exit 3 }
for ($i = 0; $i -lt $Notches; $i++) {
  [Sc]::mouse_event(0x0800, 0, 0, 120, [IntPtr]::Zero)   # MOUSEEVENTF_WHEEL, 正值=向上滚
  Start-Sleep -Milliseconds 120
}
Write-Output ("scrolled up " + $Notches + " notches at " + $X + "," + $Y)
Start-Sleep -Milliseconds 900
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
$g.Dispose(); $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
Write-Output ("saved " + $Out)
