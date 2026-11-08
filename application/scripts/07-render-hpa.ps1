[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 100)][int]$MinReplicas,
    [Parameter(Mandatory = $true)][ValidateRange(2, 100)][int]$MaxReplicas,
    [Parameter(Mandatory = $true)][ValidateRange(1, 100)][int]$TargetCpuUtilization,
    [Parameter(Mandatory = $true)][ValidateRange(0, 3600)][int]$ScaleUpStabilizationSeconds,
    [Parameter(Mandatory = $true)][ValidateRange(1, 100)][int]$ScaleUpPods,
    [Parameter(Mandatory = $true)][ValidateRange(1, 1800)][int]$ScaleUpPeriodSeconds,
    [Parameter(Mandatory = $true)][ValidateRange(0, 3600)][int]$ScaleDownStabilizationSeconds,
    [Parameter(Mandatory = $true)][ValidateRange(1, 100)][int]$ScaleDownPercent,
    [Parameter(Mandatory = $true)][ValidateRange(1, 1800)][int]$ScaleDownPeriodSeconds,
    [switch]$ConfirmG4Passed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (-not $ConfirmG4Passed) { throw "HPA generation is blocked until -ConfirmG4Passed is explicitly supplied after documented G4 PASS." }
if ($MaxReplicas -le $MinReplicas) { throw "MaxReplicas must be greater than MinReplicas." }
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Template = Join-Path $ProjectRoot "k8s\hpa\processing-hpa.template.yaml"
$OutputDirectory = Join-Path $ProjectRoot "k8s\hpa\generated"
$Output = Join-Path $OutputDirectory "processing-hpa.yaml"
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$text = Get-Content $Template -Raw
$replacements = @{
    "__MIN_REPLICAS__" = $MinReplicas
    "__MAX_REPLICAS__" = $MaxReplicas
    "__TARGET_CPU_UTILIZATION__" = $TargetCpuUtilization
    "__SCALE_UP_STABILIZATION_SECONDS__" = $ScaleUpStabilizationSeconds
    "__SCALE_UP_PODS__" = $ScaleUpPods
    "__SCALE_UP_PERIOD_SECONDS__" = $ScaleUpPeriodSeconds
    "__SCALE_DOWN_STABILIZATION_SECONDS__" = $ScaleDownStabilizationSeconds
    "__SCALE_DOWN_PERCENT__" = $ScaleDownPercent
    "__SCALE_DOWN_PERIOD_SECONDS__" = $ScaleDownPeriodSeconds
}
foreach ($key in $replacements.Keys) { $text = $text.Replace($key, [string]$replacements[$key]) }
if ($text -match "__[A-Z0-9_]+__") { throw "The generated HPA manifest still contains placeholders." }
Set-Content -Path $Output -Value $text -Encoding utf8
Write-Host "Generated $Output. Review and hash it before any kubectl apply."

