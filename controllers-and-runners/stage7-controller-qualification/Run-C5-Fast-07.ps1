param(
    [Parameter(Mandatory=$true)][string]$Root,
    [Parameter(Mandatory=$true)][string]$Stage6Root,
    [string]$Context = 'net470-v3',
    [string]$Namespace = 'net470-v3',
    [string]$OutputRoot = 'C:\NET470-C5-Fast\Stage7-R01'
)
$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Candidates = @(
    "$Root\application\net470-v3-app\.venv\Scripts\python.exe",
    "$Root\.venv\Scripts\python.exe",
    (Get-Command python.exe -ErrorAction SilentlyContinue).Source
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -Unique
$Python = $null
foreach ($Candidate in $Candidates) {
    & $Candidate -c "import sys; print(sys.version_info[:2])" *> $null
    if ($LASTEXITCODE -eq 0) { $Python = $Candidate; break }
}
if (-not $Python) { throw 'No usable Python interpreter was found.' }
"PYTHON_SELECTED=$Python"
& $Python "$PackageRoot\Invoke-C5-Fast-07.py" --self-test
if ($LASTEXITCODE -ne 0) { throw 'C5 Stage 7 self-test failed.' }
$CurrentContext = (kubectl config current-context).Trim()
if ($CurrentContext -ne $Context) { throw "Kubernetes context mismatch: $CurrentContext" }
$Timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
$Output = Join-Path $OutputRoot "results\C5-FAST-07-$Timestamp"
Write-Host 'Starting C5 Stage 7 qualification pilots. Two frozen-protocol episodes will run.'
Write-Host 'The original HPA specification is restored in every controller finally path.'
& $Python "$PackageRoot\Invoke-C5-Fast-07.py" --root $Root --package-root $PackageRoot --stage6-root $Stage6Root --output $Output --context $Context --namespace $Namespace
$Code = $LASTEXITCODE
"C5_STAGE7_RESULT=$Output"
"C5_STAGE7_EXIT_CODE=$Code"
if ($Code -ne 0) { throw "C5 Stage 7 stopped with exit code $Code. Preserve every pilot attempt." }
