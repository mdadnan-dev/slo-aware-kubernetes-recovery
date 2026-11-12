param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$PrimaryDataset,
    [Parameter(Mandatory=$true)][string]$EvidenceRoot,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [Parameter(Mandatory=$true)][string]$ExpectedDatasetSHA256
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

foreach ($Path in @($Python, $PrimaryDataset, $EvidenceRoot)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required path is missing: $Path"
    }
}

$ActualHash = (Get-FileHash -LiteralPath $PrimaryDataset -Algorithm SHA256).Hash
if ($ActualHash -ne $ExpectedDatasetSHA256) {
    throw "Primary dataset SHA-256 mismatch. Expected $ExpectedDatasetSHA256 but found $ActualHash."
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

& $Python "$PSScriptRoot\Invoke-NET470-Final-Analysis.py" `
    --primary-dataset $PrimaryDataset `
    --evidence-root $EvidenceRoot `
    --output-root $OutputRoot `
    --expected-dataset-sha256 $ExpectedDatasetSHA256

exit $LASTEXITCODE

