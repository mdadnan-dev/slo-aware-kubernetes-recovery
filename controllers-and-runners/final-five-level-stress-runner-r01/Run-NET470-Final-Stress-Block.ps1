[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$Python,
    [Parameter(Mandatory = $true)] [string]$ProtocolRoot,
    [Parameter(Mandatory = $false)] [string]$ProjectRoot = 'C:\net470-v3',
    [Parameter(Mandatory = $false)] [string]$Stage9PackageRoot = 'C:\NET470-C5-Fast\Stage9-Package-R07',
    [Parameter(Mandatory = $false)] [string]$Stage8R02Root = '',
    [Parameter(Mandatory = $false)] [string]$Stage6Root = '',
    [Parameter(Mandatory = $false)] [string]$OutputRoot = 'C:\NET470-C5-Fast\Stress-Evaluation-R01\final-c1-c5',
    [Parameter(Mandatory = $false)] [string]$Context = 'net470-v3',
    [Parameter(Mandatory = $false)] [string]$Namespace = 'net470-v3',
    [Parameter(Mandatory = $false)] [int]$MaxBlocks = 1,
    [Parameter(Mandatory = $false)] [switch]$Live,
    [Parameter(Mandatory = $false)] [string]$AcknowledgeMutation = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $Root 'Invoke-NET470-Final-Stress-Evaluation.py'
$Arguments = @(
    $Runner, '--protocol-root', $ProtocolRoot, '--project-root', $ProjectRoot,
    '--stage9-package-root', $Stage9PackageRoot, '--output-root', $OutputRoot,
    '--context', $Context, '--namespace', $Namespace, '--max-blocks', $MaxBlocks
)
if (-not [string]::IsNullOrWhiteSpace($Stage8R02Root)) { $Arguments += @('--stage8-r02-root', $Stage8R02Root) }
if (-not [string]::IsNullOrWhiteSpace($Stage6Root)) { $Arguments += @('--stage6-root', $Stage6Root) }
if ($Live) { $Arguments += @('--live', '--acknowledge-mutation', $AcknowledgeMutation) }
& $Python @Arguments
exit $LASTEXITCODE
