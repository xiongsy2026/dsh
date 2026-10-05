param(
  [string]$Hide = "",
  [string]$Focus = "",
  [string]$Out = "C:\dsh\.tmp-dsh-real.png"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W6 {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int left, top, right, bottom; }
}
"@
if ($Hide -ne "") {
  $hh = [IntPtr][int]([int64]$Hide)
  [void][W6]::ShowWindow($hh, 0)   # SW_HIDE
  Write-Output ("hid " + $Hide)
}
if ($Focus -ne "") {
  $fh = [IntPtr][int]([int64]$Focus)
  [void][W6]::ShowWindow($fh, 9)   # SW_RESTORE
  Start-Sleep -Milliseconds 1200
  [void][W6]::SetForegroundWindow($fh)
  Start-Sleep -Milliseconds 1500
  $r = New-Object W6+RECT
  [void][W6]::GetWindowRect($fh, [ref]$r)
  Write-Output ("focused rect=" + $r.left + "," + $r.top + " " + ($r.right - $r.left) + "x" + ($r.bottom - $r.top) + " vis=" + [W6]::IsWindowVisible($fh))
}
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
$g.Dispose(); $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
Write-Output ("saved " + $Out)
