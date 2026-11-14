param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$scriptPath = Join-Path $PSScriptRoot "query_local_knowledge.py"

if (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython $scriptPath @Arguments
} else {
    & python $scriptPath @Arguments
}

exit $LASTEXITCODE
