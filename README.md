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

## Exploratory C5-R2 follow-up

A separate balanced experiment at 20 requests/s compared C2, C5, and the refined C5-R2 controller over five randomized blocks (15 scientifically admissible episodes). The primary 125-episode C1-C5 dataset was not modified.

C5-R2 uses the calibrated T7-T8 stage budget, suppresses intervention when no qualifying breach exists, permits at most one initial HPA-floor action, and permits one guarded escalation after persistent budget excess. Exact HPA restoration and the 300-second censoring rule were retained.

| Result at 20 requests/s | C2 | C5 | C5-R2 |
|---|---:|---:|---:|
| Qualifying breaches | 2/5 | 5/5 | 2/5 |
| No qualifying breach | 3/5 | 0/5 | 3/5 |
| Recovered after breach | 2/2 | 5/5 | 1/2 |
| Right-censored | 0 | 0 | 1 |
| Mean SLO-violation duration (s) | 36.00 | 63.00 | 39.00 |
| Mean replica-seconds | 1846.19 | 1971.52 | 1735.99 |
| Mean peak CPU (%) | 151.4 | 160.8 | 135.2 |
| Mean T7-T8 duration (s) | 23.37 | 39.67 | 19.97 |

These directions are exploratory. Every block-paired 95% confidence interval included zero, and one C5-R2 breach was right-censored at 300 seconds. Therefore, the follow-up does **not** establish statistical superiority over C2 or C5.

## Final evidence

- Primary dataset: `data/final-five-level/NET470-five-level-C1-C5-primary-dataset.csv`
- Primary dataset SHA-256: `B7939B0FFE443AD76AC5ECE693CDFC03015C735895D27835E2DE2AF1A87781BD`
- Primary accepted episodes: 125
- Primary outcomes: 72 no qualifying breach, 32 recovered within 300 seconds, 21 right-censored at 300 seconds
- Technical exclusions: four preserved failed attempts, separate from the selected primary dataset
- Primary analysis: `analysis/final-five-level-r02/`
- Primary runner: `controllers-and-runners/final-five-level-stress-runner-r01/`
- C5-R2 runner and controller: `controllers-and-runners/c5r2-replicated-r01/`
- C5-R2 selected dataset and freeze records: `data/c5r2-exploratory-r01/`
- C5-R2 analysis: `analysis/c5r2-exploratory-r01/`
- C5-R2 selected dataset SHA-256: `A3D98519712E91AB5507E2AF1FF74E78282E97E2DC8E64BD115F8E67F09FA4C1`
- C5-R2 verified analysis archive SHA-256: `BB9A12D7D6B5CE8C07A657D4D9B3DB27405F3E79F5C9017E0F61DB4BEB397581`

## Required research figures

1. Recovery time versus incoming requests: conditional KM RMST(300), block-bootstrap 95% confidence intervals, and explicit recovered/censored/no-breach counts.
2. SLO-violation duration versus incoming requests: C1-C5 means with block-bootstrap 95% confidence intervals.
3. Replica-seconds versus incoming requests: C1-C5 resource cost with block-bootstrap 95% confidence intervals.

The plots use observed episode data without artificial smoothing. `NO_QUALIFYING_BREACH` is not encoded as zero-second recovery, and unrecovered breaches are retained as right-censored observations at 300 seconds. The C5-R2 comparison is retained as an exploratory table/analysis artifact rather than replacing the three primary figures.

## Repository structure

- `application/` — microservice application, tests, and Kubernetes manifests
- `controllers-and-runners/` — controller implementations and experiment runners
- `controllers-and-runners/c5r2-replicated-r01/` — verified C5-R2 controller, replicated runner, schedule, freeze, and checksums
- `data/final-five-level/` — frozen 125-row primary dataset and checksum
- `data/c5r2-exploratory-r01/` — separate frozen 15-row C2/C5/C5-R2 dataset, state, freeze, summaries, and manifest
- `analysis/final-five-level-r02/` — primary offline analysis code, tables, figures, freezes, and proof manifest
- `analysis/c5r2-exploratory-r01/` — separate 15-episode C5-R2 follow-up summaries and exploratory figure
-- `analysis/final-stage10-results/` — earlier formal experiment retained for provenance
- `analysis/part2-final-results/` — earlier exploratory multistage follow-up retained for provenance

## Reproduce the primary analysis

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

The primary experiment is frozen. C5-R2 is a separate exploratory follow-up and must not overwrite or be merged into the 125-row primary dataset.
