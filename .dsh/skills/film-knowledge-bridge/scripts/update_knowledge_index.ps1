param(
    [string]$Root,
    [string]$Config,
    [string]$Database,
    [switch]$IncludeIsolated,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$python = "python"
$bundledPython = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path -LiteralPath $bundledPython) {
    $python = $bundledPython
}

$arguments = @("-X", "utf8", (Join-Path $PSScriptRoot "update_knowledge_index.py"))
if (-not [string]::IsNullOrWhiteSpace($Root)) {
    $arguments += $Root
}
if (-not [string]::IsNullOrWhiteSpace($Config)) {
    $arguments += @("--config", $Config)
}
if (-not [string]::IsNullOrWhiteSpace($Database)) {
    $arguments += @("--database", $Database)
}
if ($IncludeIsolated) {
    $arguments += "--include-isolated"
}
if ($DryRun) {
    $arguments += "--dry-run"
}

& $python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "本地知识库更新失败，退出码：$LASTEXITCODE"
}
