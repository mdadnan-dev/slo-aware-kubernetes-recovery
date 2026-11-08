[CmdletBinding()]
param(
    [string]$Profile = "net470-v3"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$required = @("docker", "kubectl", "minikube", "python", "git")
foreach ($command in $required) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is missing from PATH: $command"
    }
}

docker version
docker info --format "OSType={{.OSType}} Driver={{.Driver}} CPUs={{.NCPU}} Memory={{.MemTotal}}"
if ($LASTEXITCODE -ne 0) { throw "Docker is unavailable." }
if ((docker info --format "{{.OSType}}") -ne "linux") { throw "Docker must use Linux containers." }

minikube version
kubectl version --client --output=json
$profiles = minikube profile list -o json | ConvertFrom-Json
if ($profiles.valid.Name -contains $Profile) {
    Write-Host "Profile $Profile exists. Confirm that it belongs to the current qualification attempt before continuing."
} else {
    Write-Host "Profile $Profile does not exist yet. Create it through the locked G1A procedure."
}

Write-Host "PASS: command-line prerequisites and Docker Linux mode are available."

