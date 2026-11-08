param(
 [Parameter(Mandatory=$true)][string]$P208Root,
 [Parameter(Mandatory=$true)][string]$P207Root,
 [Parameter(Mandatory=$true)][string]$OutputRoot,
 [string]$Python='C:\net470-v3\application\net470-v3-app\.venv\Scripts\python.exe'
)
$ErrorActionPreference='Stop';$Here=Split-Path -Parent $MyInvocation.MyCommand.Path;$Script=Join-Path $Here 'Regenerate-P2-08-Figures-R02.py'
& $Python $Script --self-test;if($LASTEXITCODE -ne 0){throw 'Figure R02 self-test failed.'}
& $Python $Script --p2-08-root $P208Root --p2-07-root $P207Root --output-root $OutputRoot
$Code=$LASTEXITCODE;"P2_08_FIGURES_R02_EXIT_CODE=$Code";if($Code -ne 0){throw "Figure R02 generation failed: $Code"}
