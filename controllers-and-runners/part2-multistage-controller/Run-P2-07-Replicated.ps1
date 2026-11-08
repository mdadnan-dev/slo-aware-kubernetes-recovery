param(
 [Parameter(Mandatory=$true)][string]$Root,
 [Parameter(Mandatory=$true)][string]$Part2Root,
 [Parameter(Mandatory=$true)][string]$Stage6Root,
 [string]$Stage9PackageRoot='C:\NET470-C5-Fast\Stage9-Package-R07',
 [string]$Context='net470-v3',[string]$Namespace='net470-v3',
 [int]$MaxBlocks=1,
 [string]$OutputRoot='analysis\p2-07-r02-three-arm'
)
$ErrorActionPreference='Stop';$PackageRoot=Split-Path -Parent $MyInvocation.MyCommand.Path;$Python="$Root\application\net470-v3-app\.venv\Scripts\python.exe"
if(-not(Test-Path -LiteralPath $Python)){throw "Project Python missing: $Python"};if(-not([IO.Path]::IsPathRooted($OutputRoot))){$OutputRoot=Join-Path $Part2Root $OutputRoot}
"PYTHON_SELECTED=$Python";& $Python "$PackageRoot\Invoke-P2-07-Replicated.py" --self-test;if($LASTEXITCODE -ne 0){throw 'P2-07 self-test failed.'}
& $Python "$PackageRoot\Invoke-P2-07-Replicated.py";if($LASTEXITCODE -ne 0){throw 'P2-07 dry run failed.'}
$Ack=Read-Host 'Type RUN_P2_07_R02_THREE_ARM_EVALUATION to run nine live episodes';if($Ack -ne 'RUN_P2_07_R02_THREE_ARM_EVALUATION'){throw 'Acknowledgement mismatch.'}
& $Python "$PackageRoot\Invoke-P2-07-Replicated.py" --live --acknowledge $Ack --root $Root --part2-root $Part2Root --stage9-package-root $Stage9PackageRoot --stage6-root $Stage6Root --output-root $OutputRoot --context $Context --namespace $Namespace --max-blocks $MaxBlocks
$Code=$LASTEXITCODE;"P2_07_OUTPUT=$OutputRoot";"P2_07_EXIT_CODE=$Code";if($Code -ne 0){throw "P2-07 stopped with exit code $Code. Preserve all attempts."}
