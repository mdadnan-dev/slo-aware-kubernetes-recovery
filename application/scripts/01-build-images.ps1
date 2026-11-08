[CmdletBinding()]
param(
    [string]$AttemptId = "V3-Q-G0G1-A01",
    [string]$EvidenceRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $EvidenceRoot) { $EvidenceRoot = "C:\net470-v3\evidence\$AttemptId\application" }
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null

$images = @(
    @{ Name = "ingestion"; Dockerfile = "services/ingestion/Dockerfile" },
    @{ Name = "processing"; Dockerfile = "services/processing/Dockerfile" },
    @{ Name = "results"; Dockerfile = "services/results/Dockerfile" }
)

Push-Location $ProjectRoot
try {
    foreach ($image in $images) {
        $tag = "net470-v3-$($image.Name):q1"
        docker build --pull --no-cache `
            --label "edu.depaul.net470.attempt=$AttemptId" `
            --label "edu.depaul.net470.service=$($image.Name)" `
            --file $image.Dockerfile `
            --tag $tag .
        if ($LASTEXITCODE -ne 0) { throw "Build failed for $tag" }
    }
    docker image inspect net470-v3-ingestion:q1 net470-v3-processing:q1 net470-v3-results:q1 |
        Out-File (Join-Path $EvidenceRoot "docker-image-inspect.json") -Encoding utf8
    docker images --digests --no-trunc --format "{{json .}}" |
        Select-String "net470-v3-" |
        Out-File (Join-Path $EvidenceRoot "docker-images.jsonl") -Encoding utf8
} finally {
    Pop-Location
}

Write-Host "PASS: three clean q1 images built and identities captured."
