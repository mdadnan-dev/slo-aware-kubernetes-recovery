NET470 C5 Fast Track Stage 7 R01

Purpose
  Qualify the C5 bounded HPA action, guard-driven HOLD decision, and exact
  failure-path rollback before any formal C1-C5 experiment is authorized.

Prerequisites
  1. Docker Desktop and Minikube profile net470-v3 are running.
  2. Stage 6 is VERIFIED_PASS at:
       C:\NET470-C5-Fast\Stage6-R01\results\C5-C2-CALIBRATION-A01
  3. The original project remains at C:\net470-v3.

Run from PowerShell
  $Package = 'C:\NET470-C5-Fast\Stage7-Package-R01'
  $Transcript = 'C:\NET470-C5-Fast\Stage7-R01\C5-Stage7-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt'
  New-Item -ItemType Directory -Path (Split-Path $Transcript -Parent) -Force | Out-Null
  Start-Transcript -LiteralPath $Transcript -Force
  try {
      & "$Package\Run-C5-Fast-07.ps1" `
          -Root 'C:\net470-v3' `
          -Stage6Root 'C:\NET470-C5-Fast\Stage6-R01\results\C5-C2-CALIBRATION-A01' `
          -Context 'net470-v3' `
          -Namespace 'net470-v3'
  }
  finally { Stop-Transcript }

Expected duration
  Approximately 24-30 minutes for two complete 6,450-request pilots plus reset.

Scientific safeguards
  - No retries are added.
  - Guide 11 evidence is read-only and never targeted for output.
  - Only HPA spec.minReplicas may be changed.
  - The complete original HPA spec is restored in finally.
  - Failed attempts remain preserved.
