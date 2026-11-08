param(
    [string]$Python = 'C:\Users\yunus\AppData\Local\Programs\Python\Python311\python.exe',
    [string]$Context = 'net470-v3',
    [string]$Namespace = 'net470-v3',
    [string]$Output = ''
)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($Output)) {$Output=Join-Path $Root 'analysis\prewarm-qualification'}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {throw "Python not found: $Python"}
& $Python (Join-Path $Root 'qualify_prewarm_action.py') --self-test
if ($LASTEXITCODE -ne 0) {throw 'P2-05 self-test failed.'}
& $Python (Join-Path $Root 'qualify_prewarm_action.py') --live --acknowledge-mutation T4_T5_BOUNDED_PREWARM_MIN_2 --context $Context --namespace $Namespace --output $Output
if ($LASTEXITCODE -ne 0) {throw 'P2-05 live qualification failed; inspect the summary and verify restoration.'}

