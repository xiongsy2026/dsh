<#>
.SYNOPSIS
    Weeksir DSH 工作区一键启动脚本

.DESCRIPTION
    完整启动流程：
    1. 检查 DSH Desktop 是否安装
    2. 启动后端服务 (8090 + 8100)
    3. 在 DSH Desktop 中打开本工作区

.REQUIREMENTS
    - DSH Desktop 已安装（%APPDATA%\dsh-desktop\）
    - Python 3.10+ 已安装
    - 后端依赖已安装 (pip install -r backend/requirements.txt)
    - .env 已配置真实密钥 (cp backend/.env.example backend/.env)
    - ffmpeg 在 PATH 中

.USAGE
    .\start-all.ps1
    .\start-all.ps1 -NoBackend   # 仅打开 DSH（后端已在别处跑）
    .\start-all.ps1 -NoDsh       # 仅启动后端（手动打开 DSH）
#>

param(
    [switch]$NoBackend,
    [switch]$NoDsh,
    [string]$PythonExe = "python",
    [string]$Workdir = "$PSScriptRoot",
    [string]$BackendDir = "$PSScriptRoot\backend"
)

$ErrorActionPreference = "Stop"

function Write-Color($msg, $color) { Write-Host $msg -ForegroundColor $color }

Write-Color "=== Weeksir DSH 工作区启动器 ===" "Cyan"
Write-Color "工作区: $Workdir" "Gray"

# 1. 检查 DSH Desktop
if (-not $NoDsh) {
    $dshExe = "$env:APPDATA\dsh-desktop\dsh-desktop.exe"
    if (-not (Test-Path $dshExe)) {
        $dshExeAlt = "$env:LOCALAPPDATA\dsh-desktop\dsh-desktop.exe"
        if (Test-Path $dshExeAlt) { $dshExe = $dshExeAlt }
    }
    
    if (-not (Test-Path $dshExe)) {
        Write-Color "❌ 未找到 DSH Desktop" "Red"
        Write-Color "   请先安装 DSH Desktop（内部分发或官网下载）" "Red"
        Write-Color "   预期位置: $env:APPDATA\dsh-desktop\dsh-desktop.exe" "Gray"
        if (-not $NoBackend) {
            Write-Color "   使用 -NoDsh 跳过此检查仅启动后端" "Yellow"
        } else {
            exit 1
        }
    } else {
        Write-Color "✅ 找到 DSH Desktop: $dshExe" "Green"
    }
}

# 2. 启动后端服务
if (-not $NoBackend) {
    Write-Color "`n启动后端服务..." "Cyan"
    
    if (-not (Test-Path "$BackendDir\start-backend.ps1")) {
        Write-Color "❌ 找不到后端启动脚本: $BackendDir\start-backend.ps1" "Red"
        exit 1
    }
    
    # 在新窗口启动后端（避免阻塞）
    Write-Color "在新 PowerShell 窗口启动后端..." "Green"
    $backendCmd = "cd '$BackendDir'; & '$BackendDir\start-backend.ps1' -PythonExe '$PythonExe'"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $backendCmd -WindowStyle Normal
    
    Write-Color "⏳ 等待后端服务就绪（约 10-15 秒）..." "Yellow"
    Start-Sleep -Seconds 12
    
    # 健康检查
    $ok8090 = $ok8100 = $false
    try { $r = Invoke-WebRequest "http://127.0.0.1:8090/" -TimeoutSec 3 -ErrorAction Stop; $ok8090 = $true } catch {}
    try { $r = Invoke-WebRequest "http://127.0.0.1:8100/" -TimeoutSec 3 -ErrorAction Stop; $ok8100 = $true } catch {}
    
    if ($ok8090) { Write-Color "✅ 8090 引擎就绪" "Green" } else { Write-Color "⚠️  8090 未就绪（可能还在启动）" "Yellow" }
    if ($ok8100) { Write-Color "✅ 8100 网关/右栏就绪" "Green" } else { Write-Color "⚠️  8100 未就绪（可能还在启动）" "Yellow" }
}

# 3. 打开 DSH Desktop 工作区
if (-not $NoDsh) {
    Write-Color "`n在 DSH Desktop 中打开工作区..." "Cyan"
    $arg = "--workspace `"$Workdir`""
    Start-Process $dshExe -ArgumentList $arg -ErrorAction SilentlyContinue
    Write-Color "✅ DSH Desktop 已启动，工作区应自动加载" "Green"
    Write-Color "`n下一步：" "Cyan"
    Write-Color "  1. 在 DSH 中新建会话" "Gray"
    Write-Color "  2. 输入 /weeksir 开始" "Gray"
    Write-Color "  3. 选择视频工具：/super-clone /hypit-pipeline /omni 等" "Gray"
}

Write-Color "`n=== 启动完成 ===" "Cyan"
if (-not $NoBackend) {
    Write-Color "后端日志在新打开的 PowerShell 窗口中" "Gray"
    Write-Color "关闭该窗口将停止后端服务" "Yellow"
}