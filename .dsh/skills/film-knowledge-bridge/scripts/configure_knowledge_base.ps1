param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [string]$Output
)

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$scriptPath = Join-Path $PSScriptRoot "configure_knowledge_base.py"
$arguments = @($scriptPath, $Root)

if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $arguments += @("--output", $Output)
}

if (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython @arguments
} else {
    & python @arguments
}

exit $LASTEXITCODE
