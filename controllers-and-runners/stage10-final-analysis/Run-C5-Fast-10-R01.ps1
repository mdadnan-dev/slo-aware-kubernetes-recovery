param(
    [Parameter(Mandatory = $true)]
    [string]$Stage9RunRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$Python = 'C:\net470-v3\application\net470-v3-app\.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python executable was not found: $Python"
}

$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Analyzer = Join-Path $PackageRoot 'Invoke-C5-Fast-10-R01.py'

if (-not (Test-Path -LiteralPath $Analyzer -PathType Leaf)) {
    throw "Stage 10 analyzer is missing: $Analyzer"
}

Write-Output "PYTHON_SELECTED=$Python"

& $Python `
    $Analyzer `
    --self-test

if ($LASTEXITCODE -ne 0) {
    throw 'C5 Stage 10 self-test failed.'
}

Write-Output 'Starting offline, hash-bound C5 Stage 10 analysis and final freeze.'
Write-Output 'Kubernetes queries=0; cluster mutations=0; live workload requests=0.'

& $Python `
    $Analyzer `
    --stage9-run-root $Stage9RunRoot `
    --output-root $OutputRoot

if ($LASTEXITCODE -ne 0) {
    throw "C5 Stage 10 stopped with exit code $LASTEXITCODE. Preserve all outputs."
}
