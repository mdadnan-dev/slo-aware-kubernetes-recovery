param(
    [Parameter(Mandatory=$true)][string]$Root,
    [string]$Stage9PackageRoot='C:\NET470-C5-Fast\Stage9-Package-R07',
    [string]$Context='net470-v3',
    [string]$Namespace='net470-v3',
    [string]$OutputRoot='analysis\p2-06-pilot'
)
$ErrorActionPreference='Stop'
$PackageRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
$Python="$Root\application\net470-v3-app\.venv\Scripts\python.exe"
if(-not(Test-Path -LiteralPath $Python)){throw "Project Python is missing: $Python"}
if(-not([IO.Path]::IsPathRooted($OutputRoot))){$OutputRoot=Join-Path $PackageRoot $OutputRoot}
"PYTHON_SELECTED=$Python"
& $Python "$PackageRoot\Invoke-P2-06-Pilot.py" --self-test
if($LASTEXITCODE -ne 0){throw 'P2-06 self-test failed.'}
& $Python "$PackageRoot\Invoke-P2-06-Pilot.py" --root $Root --stage9-package-root $Stage9PackageRoot --output-root $OutputRoot --context $Context --namespace $Namespace
if($LASTEXITCODE -ne 0){throw 'P2-06 dry run failed.'}
$Ack=Read-Host 'Type RUN_P2_06_C2_AND_PREWARM_PILOT to run two live workload episodes'
if($Ack -ne 'RUN_P2_06_C2_AND_PREWARM_PILOT'){throw 'Live acknowledgement did not match.'}
& $Python "$PackageRoot\Invoke-P2-06-Pilot.py" --live --acknowledge $Ack --root $Root --stage9-package-root $Stage9PackageRoot --output-root $OutputRoot --context $Context --namespace $Namespace
$Code=$LASTEXITCODE
"P2_06_OUTPUT=$OutputRoot"
"P2_06_EXIT_CODE=$Code"
if($Code -ne 0){throw "P2-06 stopped with exit code $Code. Preserve all pilot evidence."}
