param(
    [Parameter(Mandatory=$true)][string]$P207Root,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [string]$Python='C:\net470-v3\application\net470-v3-app\.venv\Scripts\python.exe',
    [string]$ExpectedFreezeSHA256='FC1EE3D88B66FF79CEE04510B03CC267280274DEE7C147933F6AFAADADA5BCFC'
)
$ErrorActionPreference='Stop'
$PackageRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
if(-not(Test-Path -LiteralPath $Python -PathType Leaf)){throw "Python missing: $Python"}
if(-not(Test-Path -LiteralPath $P207Root -PathType Container)){throw "P2-07 root missing: $P207Root"}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
"PYTHON_SELECTED=$Python"
& $Python "$PackageRoot\Invoke-P2-08-Final-Analysis.py" --self-test
if($LASTEXITCODE -ne 0){throw 'P2-08 self-test failed.'}
"Starting offline P2-08 validation, analysis, paper artifacts and final freeze."
"Kubernetes queries=0; cluster mutations=0; live workload requests=0."
& $Python "$PackageRoot\Invoke-P2-08-Final-Analysis.py" --p2-07-root $P207Root --output-root $OutputRoot --expected-freeze-sha256 $ExpectedFreezeSHA256
$Code=$LASTEXITCODE
"P2_08_EXIT_CODE=$Code"
if($Code -ne 0){throw "P2-08 stopped with exit code $Code. Preserve every output."}
