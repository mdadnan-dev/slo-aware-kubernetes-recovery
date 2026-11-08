NET470 C5 Fast Track Stage 10 R01

Purpose
-------
Offline, hash-bound final analysis of the completed Stage 9 R07 balanced
C1-C5 experiment. It performs no Kubernetes query, mutation, or workload.

Inputs
------
Stage 9 R07 run root containing the exact frozen record, freeze, primary
dataset, selection amendment, and all selected episode evidence.

Main command
------------
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Run-C5-Fast-10-R01.ps1 `
  -Stage9RunRoot 'C:\NET470-C5-Fast\Stage9-R02\results\C5-FORMAL-C1-C5-A01' `
  -OutputRoot 'C:\NET470-C5-Fast\Stage10-R01\results'

Outputs
-------
Normalized dataset, treatment RMST summaries, Kaplan-Meier curves, paired
block contrasts, exact sign-flip tests, Holm adjustment, T0-T8 stage tables,
SVG figures, final conclusion, hashes, proof ZIP, record, and freeze.

Interpretation
--------------
For all contrasts, negative paired difference favors the left-hand treatment.
The primary hypothesis supports C5 over C2 only if C5-C2 is negative and its
Holm-adjusted p-value is <= 0.05.
