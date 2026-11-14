<#>
.SYNOPSIS
    启动 Weeksir 视频管线后端服务（8090 引擎 + 8100 网关/右栏）

.DESCRIPTION
    同时启动两个常驻服务（与原 D:\video-skill 的 start_8090.cmd / start_model_admin.cmd 同形）：
    - 8090: app.py  —— `python -u app.py`（内部 uvicorn.run("app:app")），
      环境 WEEKSIR_ENTRY=workbench、PYTHONIOENCODING=utf-8:replace
    - 8100: model_admin.py —— `python model_admin.py`（裸 ThreadingHTTPServer，非 FastAPI），
      端口读 MODEL_ADMIN_PORT（默认 8100）

    要求：
    - Python 3.10+（-PythonExe 指定），已 pip install -r requirements.txt
    - backend/.env 已从 .env.example 复制并填入真实密钥
    - ffmpeg 在 PATH 中

    日志写到 backend/_svc_8090.out.log / _svc_8090.err.log / _svc_8100.out.log / _svc_8100.err.log
    按 Ctrl+C 停止所有服务。
#>

param(
    [switch]$Skip8090,
    [switch]$Skip8100,
    [string]$PythonExe = "python",
    [string]$BackendDir = "$PSScriptRoot"
)

$ErrorActionPreference = "Stop"

function Write-Color($msg, $color) { Write-Host $msg -ForegroundColor $color }

Write-Color "=== Weeksir 视频管线后端启动器 ===" "Cyan"
Write-Color "后端目录: $BackendDir" "Gray"

if (-not (Test-Path $BackendDir)) {
    Write-Color "❌ 后端目录不存在: $BackendDir" "Red"
    exit 1
}
if (-not (Test-Path "$BackendDir\app.py")) {
    Write-Color "❌ $BackendDir 不是后端根目录（找不到 app.py）" "Red"
    exit 1
}
Set-Location $BackendDir

if (-not (Test-Path ".env")) {
    Write-Color "⚠️  未找到 .env 文件" "Yellow"
    if (Test-Path ".env.example") {
        Write-Color "   先复制模板再填密钥:  Copy-Item .env.example .env" "Yellow"
    }
    exit 1
}

try {
    $pyVer = & $PythonExe --version 2>&1
    Write-Color "Python: $pyVer" "Green"
} catch {
    Write-Color "❌ 找不到 Python: $PythonExe（装 Python 3.10+ 或用 -PythonExe 指定）" "Red"
    exit 1
}

$missing = @()
@("fastapi", "uvicorn", "pydantic", "httpx", "playwright", "cv2", "numpy", "PIL", "openai", "dotenv") | ForEach-Object {
    try { & $PythonExe -c "import $_" 2>$null } catch { $missing += $_ }
}
if ($missing.Count -gt 0) {
    Write-Color "❌ 缺少依赖: $($missing -join ', ')" "Red"
    Write-Color "   请运行: $PythonExe -m pip install -r requirements.txt" "Yellow"
    exit 1
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Color "⚠️  未找到 ffmpeg（视频处理需要）: https://ffmpeg.org/download.html" "Yellow"
}

# GBK 控制台下裸 print 会崩 —— 与原 start_8090.cmd 同形
$env:PYTHONIOENCODING = "utf-8:replace"

$processes = @()

function Start-BackendService {
    param([string]$Name, [string[]]$Args, [int]$Port, [string]$HealthPath, [string]$LogPrefix, [hashtable]$ExtraEnv)

    Write-Color "启动 $Name (端口 $Port)..." "Green"
    if ($ExtraEnv) {
        foreach ($kv in $ExtraEnv.GetEnumerator()) { $env:$($kv.Key) = $kv.Value }
    }
    $proc = Start-Process $PythonExe -ArgumentList $Args -WorkingDirectory $BackendDir -PassThru `
        -RedirectStandardOutput "$BackendDir\_$LogPrefix.out.log" `
        -RedirectStandardError  "$BackendDir\_$LogPrefix.err.log"
    if ($ExtraEnv) {
        foreach ($kv in $ExtraEnv.GetEnumerator()) { Remove-Item "Env:$($kv.Key)" -ErrorAction SilentlyContinue }
    }
    $processes += @{Name=$Name; Process=$proc; Port=$Port; HealthPath=$HealthPath}
    Write-Color "   PID: $($proc.Id)  日志: _$LogPrefix.out.log / _$LogPrefix.err.log" "Gray"
}

function Test-ServiceHealth {
    param([int]$Port, [string]$HealthPath)
    for ($i = 0; $i -lt 15; $i++) {
        try {
            $resp = Invoke-WebRequest "http://127.0.0.1:$Port$HealthPath" -TimeoutSec 3 -ErrorAction Stop
            return $resp.StatusCode -eq 200
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Stop-All {
    foreach ($p in $script:processes) {
        if (-not $p.Process.HasExited) {
            Write-Color "   停止 $($p.Name) (PID $($p.Process.Id))..." "Yellow"
            Stop-Process -Id $p.Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not $Skip8090) {
    Start-BackendService "8090 引擎" @("-u", "app.py") 8090 "/" "svc_8090" @{ WEEKSIR_ENTRY = "workbench" }
}
if (-not $Skip8100) {
    Start-BackendService "8100 网关/右栏" @("model_admin.py") 8100 "/api/model/caps" "svc_8100" @{ MODEL_ADMIN_PORT = "8100" }
}

if ($processes.Count -eq 0) {
    Write-Color "❌ 没有启动任何服务" "Red"
    exit 1
}

Start-Sleep -Seconds 3
foreach ($p in $processes) {
    if (Test-ServiceHealth $p.Port $p.HealthPath) {
        Write-Color "  ✅ $($p.Name): http://127.0.0.1:$($p.Port)/ 健康检查通过 (PID $($p.Process.Id))" "Green"
    } else {
        Write-Color "  ⚠️  $($p.Name) 未应答 —— 看日志排障 (PID $($p.Process.Id))" "Yellow"
    }
}

[Console]::CancelKeyPress += { Stop-All; exit 0 }

Write-Color "`n按 Ctrl+C 停止所有服务..." "Cyan"
try {
    while ($true) {
        Start-Sleep -Seconds 10
        foreach ($p in $processes) {
            if ($p.Process.HasExited) {
                Write-Color "⚠️  $($p.Name) 进程已退出 (代码 $($p.Process.ExitCode))" "Red"
            }
        }
    }
} finally {
    Stop-All
}
