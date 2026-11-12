NET470 FINAL FIVE-LEVEL C1-C5 OFFLINE ANALYSIS R01

The governing acceptance checklist is preserved in
PROFESSOR-REQUIREMENTS-LOCK.txt. Analysis outputs must conform to that file.

Purpose
-------
This package analyzes the frozen 125-episode dataset without querying or
modifying Kubernetes. It produces the three figures requested by the faculty,
summary tables, paired C5-C2 comparisons, technical-exclusion records, and a
SHA-256 manifest.

Important analysis rules
------------------------
1. NO_QUALIFYING_BREACH is not encoded as zero-second recovery.
2. BREACH_RIGHT_CENSORED_AT_300_SECONDS contributes censoring at 300 seconds.
3. Recovery RMST(300) is conditional on episodes with a qualifying breach.
4. SLO-violation and resource analyses include every accepted episode.
5. The input dataset and primary freeze are never modified.
6. Confidence intervals are deterministic block-bootstrap percentile intervals.

Run from PowerShell
-------------------
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\Run-NET470-Final-Analysis.ps1 `
  -Python 'C:\net470-v3\application\net470-v3-app\.venv\Scripts\python.exe' `
  -PrimaryDataset 'C:\NET470-C5-Fast\Stress-Evaluation-R01\final-c1-c5\NET470-five-level-C1-C5-primary-dataset.csv' `
  -EvidenceRoot 'C:\NET470-C5-Fast\Stress-Evaluation-R01\final-c1-c5' `
  -OutputRoot 'C:\NET470-C5-Fast\Stress-Evaluation-R01\final-analysis-r01' `
  -ExpectedDatasetSHA256 'B7939B0FFE443AD76AC5ECE693CDFC03015C735895D27835E2DE2AF1A87781BD'

Outputs
-------
tables/NET470-outcome-summary.csv
tables/NET470-performance-summary.csv
tables/NET470-paired-C5-vs-C2.csv
tables/NET470-stage-summary.csv
tables/NET470-controller-mechanism-summary.csv
tables/NET470-C5-vs-C2-summary.csv
tables/NET470-technical-exclusions.csv
figures/NET470-Figure-1-recovery-vs-requests.svg
figures/NET470-Figure-2-slo-violations-vs-requests.svg
figures/NET470-Figure-3-resource-vs-requests.svg
NET470-final-analysis-record.json
NET470-FINAL-ANALYSIS-FREEZE.txt
proof/NET470-final-analysis-SHA256.csv

