param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$scriptPath = Join-Path $PSScriptRoot "retrieve_stage_sources.py"

if (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython $scriptPath @Arguments
} else {
    & python $scriptPath @Arguments
}

exit $LASTEXITCODE

