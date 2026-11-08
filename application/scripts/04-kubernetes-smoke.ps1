[CmdletBinding()]
param(
    [string]$AttemptId = "V3-Q-G0G1-A01",
    [int]$QualificationWorkUnits = 25000,
    [int]$RequestsPerCase = 10,
    [int]$CaseCount = 3,
    [string]$EvidenceRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) { throw "Create the project virtual environment before running the smoke test." }
if (-not $EvidenceRoot) { $EvidenceRoot = "C:\net470-v3\evidence\$AttemptId\application" }
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null

$stdout = Join-Path $EvidenceRoot "port-forward.stdout.txt"
$stderr = Join-Path $EvidenceRoot "port-forward.stderr.txt"
$portForward = Start-Process kubectl -ArgumentList @("port-forward", "service/ingestion", "8001:8000", "-n", "net470-v3") `
    -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
try {
    $ready = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8001/health/ready" -TimeoutSec 2
            if ($health.status -eq "ready") { $ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "Port-forwarded Ingestion service did not become Ready." }

    for ($case = 1; $case -le $CaseCount; $case++) {
        $output = Join-Path $EvidenceRoot ("qualification-case-{0:D2}.csv" -f $case)
        & $PythonExe (Join-Path $ProjectRoot "tools\qualification_client.py") `
            --url http://127.0.0.1:8001 `
            --run-id "$AttemptId-APP-C$case" `
            --requests $RequestsPerCase `
            --concurrency 1 `
            --work-units $QualificationWorkUnits `
            --out $output
        if ($LASTEXITCODE -ne 0) { throw "Qualification case $case contains a failed request." }
    }
} finally {
    if ($portForward -and -not $portForward.HasExited) { Stop-Process -Id $portForward.Id -Force }
}

kubectl logs -n net470-v3 deployment/ingestion --all-pods=true |
    Out-File (Join-Path $EvidenceRoot "ingestion.log") -Encoding utf8
kubectl logs -n net470-v3 deployment/processing --all-pods=true |
    Out-File (Join-Path $EvidenceRoot "processing.log") -Encoding utf8
kubectl logs -n net470-v3 deployment/results --all-pods=true |
    Out-File (Join-Path $EvidenceRoot "results.log") -Encoding utf8
Write-Host "PASS: all repeated qualification cases completed. These are smoke results, not SLO or workload calibration."

