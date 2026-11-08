param(
    [Parameter(Mandatory=$true)][string]$Root,
    [Parameter(Mandatory=$true)][string]$Stage8R02Root,
    [Parameter(Mandatory=$true)][string]$Stage6Root,
    [int]$MaxBlocks=1,
    [string]$Context='net470-v3',
    [string]$Namespace='net470-v3',
    [string]$RunRoot='C:\NET470-C5-Fast\Stage9-R02\results\C5-FORMAL-C1-C5-A01'
)
$ErrorActionPreference='Stop'
$PackageRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
$Python="$Root\application\net470-v3-app\.venv\Scripts\python.exe"
if(-not(Test-Path -LiteralPath $Python)){throw 'Project Python interpreter is missing.'}
"PYTHON_SELECTED=$Python"
& $Python "$PackageRoot\Invoke-C5-Fast-09-R07.py" --package-root $PackageRoot --self-test
if($LASTEXITCODE -ne 0){throw 'Stage 9 R07 self-test failed.'}
Write-Host "Starting complete-right-censoring resumable C5 Stage 9 R07; maximum blocks this invocation: $MaxBlocks."
Write-Host 'Existing evidence remains unchanged. Complete breach-without-recovery episodes are right-censored at the frozen 300-second horizon.'
& $Python "$PackageRoot\Invoke-C5-Fast-09-R07.py" --package-root $PackageRoot --root $Root --stage8-r02-root $Stage8R02Root --stage6-root $Stage6Root --run-root $RunRoot --max-blocks $MaxBlocks --context $Context --namespace $Namespace
$Code=$LASTEXITCODE
"C5_STAGE9_R07_RESULT=$RunRoot"
"C5_STAGE9_R07_EXIT_CODE=$Code"
if($Code -ne 0){throw "C5 Stage 9 R07 stopped with exit code $Code. Preserve every attempt; scientifically admissible episodes remain resumable."}
