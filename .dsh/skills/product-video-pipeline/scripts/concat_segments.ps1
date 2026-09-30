param(
  [Parameter(Mandatory = $true)][string]$SegmentsDir,
  [Parameter(Mandatory = $true)][string]$OutputPath
)

# Concatenates segment_N.mp4 files in SegmentsDir into a single video at OutputPath.
# Two-tier fallback:
#   1. ffmpeg concat demuxer + -c copy (fast, lossless) — if ffmpeg supports it and codecs match
#   2. Python moviepy (re-encode) — fallback for minimal ffmpeg builds (no concat filter/demuxer)
# Also handles single-segment case (direct copy).

$ErrorActionPreference = "Stop"

# Ensure output directory exists
$outDir = Split-Path $OutputPath -Parent
if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

# Collect segment files in order
$segs = Get-ChildItem -Path $SegmentsDir -Filter "segment_*.mp4" | Sort-Object Name
if ($segs.Count -eq 0) {
  Write-Error "No segment_*.mp4 files found in $SegmentsDir"
  exit 1
}
Write-Output "Found $($segs.Count) segments:"
$segs | ForEach-Object { Write-Output "  $($_.Name)" }

if ($segs.Count -eq 1) {
  Write-Output "Single segment, copying directly."
  Copy-Item -Path $segs[0].FullName -Destination $OutputPath -Force
  Write-Output "OUTPUT: $OutputPath"
  exit 0
}

# --- Tier 1: Try ffmpeg concat demuxer ---

$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffmpegOk = $false

if ($ff) {
  Write-Output ""
  Write-Output "=== Tier 1: ffmpeg concat demuxer ==="
  
  # Check if ffmpeg has concat demuxer (check by trying concat list)
  $listPath = Join-Path $SegmentsDir "concat_list.txt"
  $lines = $segs | ForEach-Object { "file '$($_.FullName -replace '\\', '/')'" }
  $lines | Out-File -FilePath $listPath -Encoding ascii
  
  # Probe for consistency
  $allConsistent = $true
  $firstCodec = ""
  $firstRes = ""
  foreach ($s in $segs) {
    $info = ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height -of csv=p=0 $s.FullName 2>&1
    if ($info) {
      $parts = $info -split ','
      $codec = $parts[0]
      $res = "$($parts[1])x$($parts[2])"
      Write-Output "  $($s.Name): codec=$codec res=$res"
      if (-not $firstCodec) { $firstCodec = $codec; $firstRes = $res }
      elseif ($codec -ne $firstCodec -or $res -ne $firstRes) { $allConsistent = $false }
    }
  }
  
  if ($allConsistent) {
    Write-Output "  Codecs consistent ($firstCodec / $firstRes), trying -c copy..."
    $ffOutput = & ffmpeg -y -f concat -i $listPath -c copy $OutputPath 2>&1
    $ffExit = $LASTEXITCODE
    if ($ffExit -eq 0 -and (Test-Path $OutputPath)) {
      $fi = Get-Item $OutputPath
      Write-Output "  SUCCESS! $([Math]::Round($fi.Length/1MB, 1)) MB"
      Write-Output ""
      Write-Output "OUTPUT: $OutputPath"
      $ffmpegOk = $true
    } else {
      Write-Output "  ffmpeg concat failed (exit code $ffExit). Falling back to moviepy."
    }
  } else {
    Write-Output "  Codecs inconsistent, cannot use -c copy. Trying re-encode..."
    # Try filter_complex concat (may not be available in minimal builds)
    $hasConcatFilter = $false
    $filters = ffmpeg -filters 2>&1
    if ($filters -match " concat ") { $hasConcatFilter = $true }
    
    if ($hasConcatFilter) {
      $inputArgs = @()
      $filterParts = @()
      for ($i = 0; $i -lt $segs.Count; $i++) {
        $inputArgs += "-i"
        $inputArgs += "`"$($segs[$i].FullName)`""
        $filterParts += "[$i`:v][$i`:a]"
      }
      $filterStr = ($filterParts -join "") + "concat=n=$($segs.Count):v=1:a=1[v][a]"
      $ffOutput = & ffmpeg -y @inputArgs -filter_complex $filterStr -map "[v]" -map "[a]" -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k $OutputPath 2>&1
      $ffExit = $LASTEXITCODE
      if ($ffExit -eq 0 -and (Test-Path $OutputPath)) {
        Write-Output "  SUCCESS (re-encode)!"
        Write-Output ""
        Write-Output "OUTPUT: $OutputPath"
        $ffmpegOk = $true
      } else {
        Write-Output "  ffmpeg re-encode failed. Falling back to moviepy."
      }
    } else {
      Write-Output "  No concat filter available (minimal ffmpeg build). Falling back to moviepy."
    }
  }
} else {
  Write-Output "ffmpeg not found. Will use moviepy."
}

# --- Tier 2: Fallback to Python moviepy ---

if (-not $ffmpegOk) {
  Write-Output ""
  Write-Output "=== Tier 2: Python moviepy fallback ==="
  
  # Check Python
  $py = Get-Command python -ErrorAction SilentlyContinue
  if (-not $py) {
    Write-Error "Python not found. Cannot use moviepy fallback."
    exit 2
  }
  
  # Check moviepy
  $mpCheck = python -c "import moviepy; print('ok')" 2>&1
  if ($mpCheck -ne "ok") {
    Write-Output "  moviepy not installed, installing..."
    pip install moviepy 2>&1 | Select-Object -Last 3
  }
  
  # Build Python script
  $pyScript = @"
from moviepy import VideoFileClip, concatenate_videoclips
import os, sys

seg_dir = sys.argv[1]
output = sys.argv[2]

segs = []
for f in sorted(os.listdir(seg_dir)):
    if f.startswith('segment_') and f.endswith('.mp4'):
        segs.append(VideoFileClip(os.path.join(seg_dir, f)))
        print(f"  Loaded: {f} ({segs[-1].duration}s)")

if len(segs) == 0:
    print("ERROR: no segments found")
    sys.exit(1)

print(f"  Concatenating {len(segs)} segments...")
final = concatenate_videoclips(segs, method='compose')
final.write_videofile(output, codec='libx264', audio_codec='aac', fps=24, preset='fast', bitrate='8000k', logger='bar')

size_mb = os.path.getsize(output) / 1024 / 1024
print(f"  Done! {size_mb:.1f} MB, {final.duration:.1f}s")
"@

  $pyFile = Join-Path $SegmentsDir "_concat_moviepy.py"
  $pyScript | Out-File $pyFile -Encoding utf8
  
  Write-Output "  Running moviepy concatenation..."
  & python $pyFile $SegmentsDir $OutputPath
  $pyExit = $LASTEXITCODE
  
  # Clean up temp script
  Remove-Item $pyFile -ErrorAction SilentlyContinue
  
  if ($pyExit -eq 0 -and (Test-Path $OutputPath)) {
    Write-Output ""
    Write-Output "OUTPUT: $OutputPath"
    exit 0
  } else {
    Write-Error "moviepy concat failed (exit code $pyExit)."
    exit 3
  }
}

if (Test-Path $OutputPath) {
  $fi = Get-Item $OutputPath
  Write-Output "Final file: $([Math]::Round($fi.Length/1MB, 1)) MB"
  exit 0
} else {
  Write-Error "Concat failed, output not found."
  exit 4
}
