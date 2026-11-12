# SLO-Aware Recovery-Stage-Adaptive Control for Kubernetes Microservices

This repository contains the implementation and reproducibility artifacts for the NET 470 study **SLO-Aware Recovery-Stage-Adaptive Control for Kubernetes Microservices under Dynamic Workloads**.

## Final five-level evaluation

The frozen primary experiment compares five treatments under identical workload duration, Kubernetes configuration, resource limits, and SLO definition.

| Treatment | Role |
|---|---|
| C1 | Reference HPA |
| C2 | Tuned static HPA and primary baseline |
| C3 | Recovery-stage diagnosis without HPA modification |
| C4 | Guarded adaptive HPA-floor controller |
| C5 | Proposed recovery-stage-aware controller |

The design contains 125 scientifically admissible live episodes: five workload levels (5, 10, 15, 20, and 25 requests/s), five treatments, and five randomized blocks per workload-treatment cell. Across all episodes, 581,250 requests were scheduled, 581,240 completed, and 10 produced errors.

## Main finding

The final evaluation does **not** establish faster recovery for C5 relative to tuned HPA C2. At 20 requests/s, C5 averaged approximately 49.9 seconds longer recovery, 42 seconds more SLO-violation time, and 241.6 additional replica-seconds than C2 across the five paired blocks. At 25 requests/s, all five C5 episodes were right-censored at 300 seconds.

The mechanism analysis explains this negative result: C5's bounded action mainly targeted the earlier HPA-response stage, while the dominant measured delay was downstream T7-T8 application stabilization. At overload, the shared five-replica ceiling also limited what any HPA-floor intervention could accomplish. These are reported limitations, not hidden or smoothed outcomes.

## Final evidence

- Primary dataset: `data/final-five-level/NET470-five-level-C1-C5-primary-dataset.csv`
- Dataset SHA-256: `B7939B0FFE443AD76AC5ECE693CDFC03015C735895D27835E2DE2AF1A87781BD`
- Accepted episodes: 125
- Outcomes: 72 no qualifying breach, 32 recovered within 300 seconds, 21 right-censored at 300 seconds
- Technical exclusions: four preserved failed attempts, separate from the selected dataset
- Final analysis: `analysis/final-five-level-r02/`
- Final runner: `controllers-and-runners/final-five-level-stress-runner-r01/`
- Manuscript source: `paper/final-r07/`

## Faculty-required figures

1. Recovery time versus incoming requests: conditional KM RMST(300), block-bootstrap 95% confidence intervals, and explicit recovered/censored/no-breach counts.
2. SLO-violation duration versus incoming requests: C1-C5 means with block-bootstrap 95% confidence intervals.
3. Replica-seconds versus incoming requests: C1-C5 resource cost with block-bootstrap 95% confidence intervals.

The plots use observed episode data without artificial smoothing. `NO_QUALIFYING_BREACH` is not encoded as zero-second recovery, and unrecovered breaches are retained as right-censored observations at 300 seconds.

## Repository structure

- `application/` — microservice application, tests, and Kubernetes manifests
- `controllers-and-runners/` — controller implementations and experiment runners
- `data/final-five-level/` — frozen 125-row primary dataset and checksum
- `analysis/final-five-level-r02/` — offline analysis code, tables, figures, freezes, and proof manifest
- `paper/final-r07/` — final IEEE/Overleaf source, figures, traceability checklist, and compiled PDF
- `analysis/final-stage10-results/` — earlier formal experiment retained for provenance
- `analysis/part2-final-results/` — exploratory multistage follow-up retained for provenance

## Reproduce the final analysis

The analysis is offline-only and performs no Kubernetes queries or live workload requests.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\analysis\final-five-level-r02\Run-NET470-Final-Analysis.ps1 `
  -Python python `
  -PrimaryDataset .\data\final-five-level\NET470-five-level-C1-C5-primary-dataset.csv `
  -EvidenceRoot .\data\final-five-level `
  -OutputRoot .\analysis-output `
  -ExpectedDatasetSHA256 B7939B0FFE443AD76AC5ECE693CDFC03015C735895D27835E2DE2AF1A87781BD
```

The final experiment is frozen. Any future C5-R refinement must be identified as a separate follow-up experiment and must not overwrite or be merged into this dataset.
