param(
  [Parameter(Mandatory = $true)][string]$TaskId,
  [string]$ApiUrl = "https://zexapi.com",
  [string]$ApiKey,
  [int]$IntervalSec = 5,
  [int]$TimeoutSec = 900,
  [string]$OutFile
)

# Polls GET /v1/videos/{task_id} until completed/succeeded/failed.
# Works for image tasks (url / image_url field) and video tasks (video_url field).
# nano_banana_pro returns status "succeeded" + image_url; gpt-image returns "completed" + url.
# API key: -ApiKey param, or ZEXAPI_API_KEY env var. Never hardcode it here.

if (-not $ApiKey) { $ApiKey = $env:ZEXAPI_API_KEY }
if (-not $ApiKey) { Write-Error "ApiKey missing: pass -ApiKey or set ZEXAPI_API_KEY"; exit 1 }

$h = @{ Authorization = "Bearer $ApiKey" }
$sw = [Diagnostics.Stopwatch]::StartNew()

while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
  try {
    $s = Invoke-RestMethod -Uri "$ApiUrl/v1/videos/$TaskId" -Method Get -Headers $h
    Write-Output ("[{0:mm\:ss}] {1} {2}%" -f $sw.Elapsed, $s.status, $s.progress)
    if ($s.status -in @("completed", "succeeded", "failed")) {
      if ($OutFile) { $s | ConvertTo-Json -Depth 10 | Out-File $OutFile -Encoding utf8 }
      if ($s.status -in @("completed", "succeeded")) {
        $asset = $s.url
        if (-not $asset) { $asset = $s.image_url }
        if (-not $asset) { $asset = $s.video_url }
        if ($asset) { Write-Output "ASSET_URL: $asset" }
        else { Write-Output "COMPLETED without url/image_url/video_url field"; exit 3 }
      } else {
        Write-Output ("ERROR: {0} ({1})" -f $s.error.message, $s.error.code)
        exit 4
      }
      exit 0
    }
  } catch {
    Write-Output ("poll error: " + $_.Exception.Message)
  }
  Start-Sleep -Seconds $IntervalSec
}

Write-Output "TIMEOUT after ${TimeoutSec}s"
exit 2
