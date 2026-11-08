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

kubectl get namespace net470-v3 -o yaml | Out-File (Join-Path $EvidenceRoot "namespace.yaml") -Encoding utf8
kubectl get deployment,replicaset,pod,service,endpoints,configmap -n net470-v3 -o yaml |
    Out-File (Join-Path $EvidenceRoot "application-state.yaml") -Encoding utf8
kubectl get pods -n net470-v3 -o jsonpath="{range .items[*]}{.metadata.name}{'|'}{.metadata.uid}{'|'}{.spec.nodeName}{'|'}{.status.containerStatuses[0].imageID}{'\n'}{end}" |
    Out-File (Join-Path $EvidenceRoot "runtime-image-identities.txt") -Encoding utf8
kubectl top pods -n net470-v3 --containers |
    Out-File (Join-Path $EvidenceRoot "resource-snapshot.txt") -Encoding utf8
foreach ($deployment in @("ingestion", "processing", "results")) {
    kubectl exec -n net470-v3 "deployment/$deployment" -- python -m pip freeze |
        Out-File (Join-Path $EvidenceRoot "$deployment-packages.txt") -Encoding utf8
}
Get-ChildItem $ProjectRoot -Recurse -File |
    Where-Object { $_.FullName -notmatch "\\.venv\\|\\qualification-output\\|\\evidence\\|\\.git\\" } |
    Get-FileHash -Algorithm SHA256 |
    Select-Object Path, Hash |
    Export-Csv (Join-Path $EvidenceRoot "application-source-sha256.csv") -NoTypeInformation
Write-Host "PASS: application state, runtime identities, package versions and source hashes captured."

