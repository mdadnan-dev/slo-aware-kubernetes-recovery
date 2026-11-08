[CmdletBinding()]
param(
    [string]$AttemptId = "V3-Q-G0G1-A01",
    [string]$EvidenceRoot = "",
    [switch]$KeepRunning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $EvidenceRoot) { $EvidenceRoot = "C:\net470-v3\evidence\$AttemptId\application" }
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null

Push-Location $ProjectRoot
try {
    docker compose up --detach --no-build
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose startup failed." }
    $ready = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8001/health/ready" -TimeoutSec 2
            if ($health.status -eq "ready") { $ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "Ingestion container did not become Ready." }

    $request = @{
        run_id = "$AttemptId-COMPOSE"
        work_units = 25000
        payload = "qualification"
    } | ConvertTo-Json
    $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8001/v1/jobs" `
        -ContentType "application/json" -Body $request -TimeoutSec 30
    $response | ConvertTo-Json -Depth 12 | Out-File (Join-Path $EvidenceRoot "compose-smoke-response.json") -Encoding utf8
    docker compose ps --format json | Out-File (Join-Path $EvidenceRoot "compose-ps.json") -Encoding utf8
    docker compose logs --no-color | Out-File (Join-Path $EvidenceRoot "compose-logs.txt") -Encoding utf8
    if ($response.status -ne "completed") { throw "The end-to-end response was not completed." }
    if (-not $response.identities.ingestion.pod_name -or -not $response.identities.processing.pod_name -or -not $response.identities.results.pod_name) {
        throw "The response does not contain all three service identities."
    }
    Write-Host "PASS: Docker Compose end-to-end request completed with three service identities."
} finally {
    if (-not $KeepRunning) { docker compose down | Out-Null }
    Pop-Location
}

