# SLO-Aware Recovery-Stage-Adaptive Control for Kubernetes Microservices

This repository contains the implementation and reproducibility artifacts for
the NET 470 project:

**SLO-Aware Recovery-Stage-Adaptive Control for Kubernetes Microservices under Dynamic Workloads**

## Research objective

The project measures the complete Kubernetes recovery path from SLO detection
through HPA response, scheduling, container startup, readiness, traffic
activation, and sustained application-level SLO recovery.

## Evaluated conditions

- C1: Reference HPA
- C2: Tuned static HPA and primary baseline
- C3: Recovery-stage diagnosis without intervention
- C4: Guarded adaptive HPA-floor intervention
- C5: Complete recovery-stage-aware framework

C1-C4 are controlled baselines and ablations. C5 is the complete proposed
framework. The preplanned primary contrast is C5 minus C2.

## Formal experiment

- Treatments: 5
- Randomized blocks: 10
- Formal episodes: 50
- Episodes per treatment: 10
- Scheduled requests: 322,500
- Completed requests: 322,473
- Request errors: 27
- Observed recoveries: 43
- Right-censored episodes: 7
- Censoring horizon: 300 seconds

## Primary result

The paired C5-minus-C2 RMST(300) difference was +13.322 seconds with a 95%
confidence interval from -16.651 to +43.296 seconds. The exact sign-flip
p-value was 0.3359 and the Holm-adjusted p-value was 0.7188.

The experiment therefore did not support the hypothesis that C5 provides
faster recovery than C2. Equivalence was not tested.

## Repository structure

- `application/` - microservice application, tests and Kubernetes manifests
- `controllers-and-runners/` - final calibration, qualification, binding,
  formal-experiment and analysis packages
- `analysis/final-stage10-results/` - normalized final analysis tables
- `analysis/figure-generation/` - code used to create the final figures
- `figures/` - three final publication figures
- `docs/` - comparison, evidence and reproducibility records

## Important interpretation

The evaluated C5 controller was bounded, measurable and safely restored the
original HPA configuration after every selected episode. It did not
demonstrate statistically supported recovery-time superiority over C2. The
measured stage decomposition indicates that T7-T8 application stabilization
was the dominant observed delay, while the implemented C5 action primarily
targeted an earlier HPA-response stage.

## Data policy

Large raw request-level evidence, virtual environments, failed attempts,
credentials, Minikube state and machine-specific files are intentionally not
stored in this repository. The normalized formal dataset and final analysis
tables required to verify the reported conclusions are included.

## Reproduction

See the package README files and scripts under `controllers-and-runners/`.
The final figures can be regenerated using:

```powershell
python analysis/figure-generation/Generate-C5-Final-Three-Figures-R03.py
```

## Exploratory multistage follow-up

A separate three-arm exploratory follow-up compared:

- C2: tuned static HPA baseline
- C5: original T1-T2 stage-aware controller
- PREWARM: dominant-stage-selected T4-T5 bounded prewarming

Three randomized complete blocks produced nine scientifically admissible episodes. KM RMST(300) was 265.811 seconds for C2, 273.432 seconds for C5, and 267.698 seconds for PREWARM. PREWARM was descriptively close to C2 and faster than C5, but the exploratory paired comparisons did not support superiority. Equivalence was not tested.

The follow-up reused the frozen 5-25-5 RPS workload, applied strict right censoring at 300 seconds, retained observed T0-T8 timestamps without imputation, and restored the original HPA specification after every episode.

Compact final results are under `analysis/part2-final-results/`. Controller code and offline analysis scripts are under `controllers-and-runners/part2-multistage-controller/`.
