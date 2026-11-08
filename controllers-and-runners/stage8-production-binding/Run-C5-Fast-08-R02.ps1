param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)][string]$Stage8R01Root,[Parameter(Mandatory=$true)][string]$Stage7Root,[Parameter(Mandatory=$true)][string]$Stage6Root,[string]$OutputRoot='C:\NET470-C5-Fast\Stage8-R02')
$ErrorActionPreference='Stop';$Pkg=Split-Path -Parent $MyInvocation.MyCommand.Path;$Py="$Root\application\net470-v3-app\.venv\Scripts\python.exe";if(-not(Test-Path $Py)){throw 'Python missing'}
& $Py "$Pkg\Invoke-C5-Fast-08-R02.py" --package-root $Pkg --self-test;if($LASTEXITCODE -ne 0){throw 'R02 self-test failed'}
$Stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ');$Out=Join-Path $OutputRoot "results\C5-FAST-08-R02-$Stamp"
Write-Host 'Starting offline/read-only Stage 8 R02 binding correction. No workload or mutation will run.'
& $Py "$Pkg\Invoke-C5-Fast-08-R02.py" --package-root $Pkg --root $Root --stage8-r01-root $Stage8R01Root --stage7-root $Stage7Root --stage6-root $Stage6Root --output $Out
$Code=$LASTEXITCODE;"C5_STAGE8_R02_RESULT=$Out";"C5_STAGE8_R02_EXIT_CODE=$Code";if($Code -ne 0){throw "Stage 8 R02 failed with exit code $Code"}
