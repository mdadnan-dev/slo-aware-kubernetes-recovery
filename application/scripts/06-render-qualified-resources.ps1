[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9]+m$")][string]$IngestionCpuRequest,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9]+(Mi|Gi)$")][string]$IngestionMemoryRequest,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9]+m$")][string]$ProcessingCpuRequest,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9]+(Mi|Gi)$")][string]$ProcessingMemoryRequest,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9]+m$")][string]$ResultsCpuRequest,
    [Parameter(Mandatory = $true)][ValidatePattern("^[0-9]+(Mi|Gi)$")][string]$ResultsMemoryRequest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Template = Join-Path $ProjectRoot "k8s\qualified\resources-patch.template.yaml"
$Output = Join-Path $ProjectRoot "k8s\qualified\generated\resources-patch.yaml"
$text = Get-Content $Template -Raw
$replacements = @{
    "__INGESTION_CPU_REQUEST__" = $IngestionCpuRequest
    "__INGESTION_MEMORY_REQUEST__" = $IngestionMemoryRequest
    "__PROCESSING_CPU_REQUEST__" = $ProcessingCpuRequest
    "__PROCESSING_MEMORY_REQUEST__" = $ProcessingMemoryRequest
    "__RESULTS_CPU_REQUEST__" = $ResultsCpuRequest
    "__RESULTS_MEMORY_REQUEST__" = $ResultsMemoryRequest
}
foreach ($key in $replacements.Keys) { $text = $text.Replace($key, $replacements[$key]) }
if ($text -match "__[A-Z0-9_]+__") { throw "The generated resource patch still contains placeholders." }
Set-Content -Path $Output -Value $text -Encoding utf8
Write-Host "Generated $Output. Review, hash and apply it only after fresh single-pod observations justify every value."

