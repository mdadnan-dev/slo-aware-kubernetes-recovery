# C5-R2 exploratory follow-up analysis

This directory contains the verified offline analysis outputs for the separate C5-R2 follow-up experiment.

## Design

- Workload: 20 requests/s
- Treatments: C2, C5, and C5-R2
- Blocks: five
- Selected episodes: 15 (five per treatment)
- SLO threshold: 123.472 ms
- Window duration: 15 seconds
- Recovery censoring horizon: 300 seconds
- Replica range: one to five
- HPA restoration: exact after every selected episode

The follow-up does not modify the frozen 125-episode primary C1-C5 dataset.

## Interpretation

C5-R2 had fewer qualifying breaches and lower sample-mean resource use than C5, but one C5-R2 breach was right-censored and every paired 95% confidence interval included zero. These files support an exploratory mechanism and feasibility claim only; they do not establish statistical superiority over C2 or C5.

## Integrity

- Selected C5-R2 evaluation dataset SHA-256: `A3D98519712E91AB5507E2AF1FF74E78282E97E2DC8E64BD115F8E67F09FA4C1`
- Verified analysis-results archive SHA-256: `BB9A12D7D6B5CE8C07A657D4D9B3DB27405F3E79F5C9017E0F61DB4BEB397581`

The selected 15-row dataset is published in `data/c5r2-exploratory-r01/`. The exact controller, runner, schedule, freeze, and source checksums are published in `controllers-and-runners/c5r2-replicated-r01/`. Raw per-request telemetry is preserved outside GitHub because the evidence archive is approximately 489 MB; the compact selected dataset and integrity manifests are sufficient for the reported offline analysis.
