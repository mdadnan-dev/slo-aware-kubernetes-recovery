param(
    [Parameter(Mandatory=$true)][string]$Root,
    [int]$MaxEpisodes = 1,
    [string]$Context = 'net470-v3',
    [string]$Namespace = 'net470-v3'
)
$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Candidates = @(
    (Join-Path $Root 'application\net470-v3-app\.venv\Scripts\python.exe'),
    'C:\Python311\python.exe', 'C:\Python314\python.exe', 'python.exe'
)
$Python = $null
foreach ($Candidate in $Candidates) {
    try { & $Candidate -c "import httpx; print('ok')" 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { $Python=$Candidate; break } } catch {}
}
if (-not $Python) { throw 'No Python interpreter with httpx was found.' }
Write-Output "PYTHON_SELECTED=$Python"
& $Python (Join-Path $PackageRoot 'Invoke-C5-Fast-06.py') --self-test
if ($LASTEXITCODE -ne 0) { throw 'C5 Stage 6 self-test failed.' }
& $Python (Join-Path $PackageRoot 'C5-Stage6-Episode-Core.py') --self-test
if ($LASTEXITCODE -ne 0) { throw 'C5 Stage 6 episode-core self-test failed.' }
$RunRoot = 'C:\NET470-C5-Fast\Stage6-R01\results\C5-C2-CALIBRATION-A01'
Write-Output 'C5_STAGE6_R02_CORRECTION=STALE_CPU_POLL_BOUND_TO_NATIVE_HPA_ACTION_TIME'
Write-Output 'PRESERVED_R01_ATTEMPTS=RECOVERED_OFFLINE_WHEN_CAUSALLY_COMPATIBLE'
Write-Output "Starting corrected resumable C5 Stage 6 R02; maximum episodes this invocation: $MaxEpisodes."
Write-Output 'Keep Docker Desktop and Minikube running. Accepted episodes are never repeated.'
& $Python (Join-Path $PackageRoot 'Invoke-C5-Fast-06.py') --root $Root --package-root $PackageRoot --run-root $RunRoot --max-episodes $MaxEpisodes --context $Context --namespace $Namespace
if ($LASTEXITCODE -ne 0) { throw "C5 Stage 6 stopped with exit code $LASTEXITCODE. Preserve all attempts; accepted episodes remain resumable." }
Write-Output "C5_STAGE6_RESULT=$RunRoot"
