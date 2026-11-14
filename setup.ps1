<#>
.SYNOPSIS
    新机器首次安装：装依赖、备份 .env 模板、检查外部工具

.DESCRIPTION
    clone 后在仓库根跑一次：
        git clone <私有库> C:\dsh
        cd C:\dsh
        .\setup.ps1                 # 装 Python 依赖 + 生成 backend\.env 模板
        # 然后手工把真实密钥填进 backend\.env（模板里有每条 key 的说明）
        .\start-all.ps1             # 起后端 8090/8100 并打开 DSH Desktop

    本脚本做：
    1. 检查 Python 3.10+（-PythonExe 可指定其它解释器）
    2. pip install -r backend\requirements.txt（-SkipPip 跳过）
    3. backend\.env 不存在则从 .env.example 复制（不覆盖已有文件）
    4. 检查 ffmpeg / node / playwright chromium（缺哪个打警告并给装法）

    不做：不装 DSH Desktop（仍需单独安装 Electron 客户端）、不填密钥、不克隆 hyperframes。
#>

param(
    [string]$PythonExe = "python",
    [switch]$SkipPip,
    [switch]$SkipPlaywright
)

$ErrorActionPreference = "Continue"
$Repo = $PSScriptRoot
$Backend = Join-Path $Repo "backend"

function Write-Color($msg, $color) { Write-Host $msg -ForegroundColor $color }

Write-Color "=== Weeksir 新机器安装 ===" "Cyan"
Write-Color "仓库: $Repo" "Gray"

# 1. Python
$pyOk = $false
try {
    $v = (& $PythonExe --version 2>&1) -join " "
    Write-Color "Python: $v" "Green"
    if ($v -match "3\.(1[0-9]|[2-9][0-9])") { $pyOk = $true }
    elseif ($v -match "3\.([0-9]+)") { $pyOk = [int]$Matches[1] -ge 10 }
} catch {
    Write-Color "❌ 找不到 Python: $PythonExe" "Red"
}
if (-not $pyOk) {
    Write-Color "❌ 需要 Python 3.10+（https://www.python.org/downloads/），或用 -PythonExe 指定" "Red"
    exit 1
}

# 2. pip 依赖
if (-not $SkipPip) {
    if (-not (Test-Path "$Backend\requirements.txt")) {
        Write-Color "❌ 缺 backend\requirements.txt —— 仓库不完整？" "Red"
        exit 1
    }
    Write-Color "安装 Python 依赖（backend\requirements.txt）..." "Cyan"
    & $PythonExe -m pip install -r "$Backend\requirements.txt"
    if ($LASTEXITCODE -ne 0) {
        Write-Color "⚠️  pip 安装有报错（见上）—— 部分可选依赖失败可继续，核心包失败则服务起不来" "Yellow"
    }
} else {
    Write-Color "跳过 pip（-SkipPip）" "Yellow"
}

# 3. .env 模板
$envFile = Join-Path $Backend ".env"
if (-not (Test-Path $envFile)) {
    $example = Join-Path $Backend ".env.example"
    if (Test-Path $example) {
        Copy-Item $example $envFile
        Write-Color "✅ 已生成 backend\.env（从模板复制）—— 请把真实密钥填进去再启动" "Green"
        Write-Color "   模板里每条 key 都有用途注释；不填则对应渠道不可用（服务仍能启动）" "Gray"
    } else {
        Write-Color "⚠️  未找到 backend\.env.example" "Yellow"
    }
} else {
    Write-Color "✅ backend\.env 已存在（不动）" "Green"
}

# 4. 外部工具
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Color "⚠️  缺 ffmpeg —— 视频处理必需: https://ffmpeg.org/download.html （winget install Gyan.FFmpeg）" "Yellow"
} else {
    Write-Color "✅ ffmpeg: $((Get-Command ffmpeg).Source)" "Green"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Color "⚠️  缺 node —— hyperframes 预览等腿需要: https://nodejs.org/" "Yellow"
} else {
    Write-Color "✅ node: $(& node --version 2>&1)" "Green"
}
if (-not $SkipPlaywright) {
    $pw = $false
    try { & $PythonExe -c "import playwright" 2>$null; $pw = $true } catch {}
    if ($pw) {
        Write-Color "安装 playwright chromium（RPA 腿用）..." "Cyan"
        & $PythonExe -m playwright install chromium
    }
}
$hf = Join-Path $Backend "hyperframes\packages\cli\bin\hyperframes.mjs"
if (-not (Test-Path $hf)) {
    Write-Color "⚠️  backend\hyperframes 未带（4.3GB 外部工具，不入 git）—— 需要 ai-edit 预览时：" "Yellow"
    Write-Color "   从旧机器复制 D:\video-skill\hyperframes 到 backend\hyperframes，或在该目录 npm install" "Gray"
    Write-Color "   也可以设置环境变量 HYPERFRAMES_CLI 指向任意 hyperframes.mjs" "Gray"
}

# 5. DSH Desktop 提示
$dsh = "$env:APPDATA\dsh-desktop\dsh-desktop.exe"
if (-not (Test-Path $dsh)) {
    $dshAlt = "$env:LOCALAPPDATA\dsh-desktop\dsh-desktop.exe"
    if (Test-Path $dshAlt) { $dsh = $dshAlt }
}
if (Test-Path $dsh) {
    Write-Color "✅ DSH Desktop: $dsh" "Green"
} else {
    Write-Color "⚠️  未检测到 DSH Desktop —— 仍需单独安装客户端（本仓库只带工作区 + 后端）" "Yellow"
}

Write-Color "`n=== 安装完成 ===" "Cyan"
Write-Color "下一步：把密钥填进 backend\.env，然后运行 .\start-all.ps1" "Green"
