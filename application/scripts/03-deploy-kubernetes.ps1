[CmdletBinding()]
param(
    [string]$AttemptId = "V3-Q-G0G1-A01",
    [string]$Profile = "net470-v3",
    [string]$EvidenceRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $EvidenceRoot) { $EvidenceRoot = "C:\net470-v3\evidence\$AttemptId\application" }
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null

$status = minikube status -p $Profile --output=json | ConvertFrom-Json
if ($status.Host -ne "Running" -or $status.APIServer -ne "Running") { throw "Minikube profile $Profile is not healthy." }
kubectl config use-context $Profile | Out-Null
if ((kubectl config current-context).Trim() -ne $Profile) { throw "kubectl context is not $Profile." }

foreach ($tag in @("net470-v3-ingestion:q1", "net470-v3-processing:q1", "net470-v3-results:q1")) {
    minikube image load $tag -p $Profile
    if ($LASTEXITCODE -ne 0) { throw "Failed to load image $tag into Minikube." }
}

Push-Location $ProjectRoot
try {
    kubectl apply -k k8s/base | Tee-Object -FilePath (Join-Path $EvidenceRoot "kubectl-apply.txt")
    if ($LASTEXITCODE -ne 0) { throw "Base manifest application failed." }
} finally { Pop-Location }

foreach ($deployment in @("results", "processing", "ingestion")) {
    kubectl rollout status "deployment/$deployment" -n net470-v3 --timeout=180s
    if ($LASTEXITCODE -ne 0) { throw "Rollout failed for $deployment." }
}
if (kubectl get hpa -n net470-v3 --no-headers 2>$null) { throw "HPA is prohibited during base qualification." }

kubectl get deployment,pod,service,endpoints -n net470-v3 -o wide |
    Out-File (Join-Path $EvidenceRoot "kubernetes-base-state.txt") -Encoding utf8
kubectl get pods -n net470-v3 -o json |
    Out-File (Join-Path $EvidenceRoot "kubernetes-pods.json") -Encoding utf8
Write-Host "PASS: one replica of each service is Ready and no HPA exists."

