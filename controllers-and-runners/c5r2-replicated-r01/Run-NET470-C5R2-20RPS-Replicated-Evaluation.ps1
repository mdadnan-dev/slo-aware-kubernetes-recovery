[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$RuntimeR20,
    [string]$Stage6Budget,
    [string]$Stage6Root,
    [string]$Stage8R02Root,
    [string]$Stage9PackageRoot,
    [string]$ProjectRoot='C:\net470-v3',
    [string]$OutputRoot='C:\NET470-C5-Fast\Stress-Evaluation-R01\C5-R-Evaluation-R01\replicated-20rps-c5r2-r01',
    [string]$Context='net470-v3',
    [string]$Namespace='net470-v3',
    [string]$Authorization='',
    [switch]$Live,
    [switch]$SelfTest
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$PackageRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner=Join-Path $PackageRoot 'Invoke-NET470-C5R2-20RPS-Replicated-Evaluation.py'
if(-not(Test-Path -LiteralPath $Python -PathType Leaf)){throw "Python not found: $Python"}
if($SelfTest){& $Python $Runner --self-test; exit $LASTEXITCODE}
$ArgsList=@($Runner,'--runtime-r20',$RuntimeR20,'--stage6-budget',$Stage6Budget,'--stage9-package-root',$Stage9PackageRoot,'--project-root',$ProjectRoot,'--output-root',$OutputRoot,'--context',$Context,'--namespace',$Namespace)
if($Live){
    $ArgsList+=@('--live','--acknowledge-mutation',$Authorization,'--stage6-root',$Stage6Root,'--stage8-r02-root',$Stage8R02Root)
}
& $Python @ArgsList
exit $LASTEXITCODE
