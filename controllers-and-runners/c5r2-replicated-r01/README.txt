NET470 C5-R2 20-RPS Replicated Evaluation R01
=============================================

Purpose
-------
Run five fresh balanced blocks comparing C2, C5, and C5-R2 at 20 RPS.
There are 15 planned accepted episodes. The evaluation is resumable by design
cell and preserves technically invalid attempts before selecting the earliest
technically valid attempt for each block-treatment cell.

Balanced frozen order
---------------------
Block 1: C2, C5, C5-R2
Block 2: C5, C5-R2, C2
Block 3: C5-R2, C2, C5
Block 4: C2, C5-R2, C5
Block 5: C5, C2, C5-R2

Frozen experimental contract
----------------------------
- workload: 20 RPS high challenge
- p95 SLO threshold: 123.472 ms
- SLO window: 15 seconds
- breach confirmation: 2 consecutive complete violating windows
- recovery confirmation: 8 consecutive complete SLO-good windows
- strict recovery horizon: 300 seconds from confirmed breach
- frozen Stage 6 T7-T8 budget: 71.82882 seconds
- HPA baseline: min 1, max 5, CPU target 55 percent
- C5-R2 maximum one initial floor action and one guarded escalation
- exact original HPA restoration in finally

Scientific separation
---------------------
This exploratory extension remains separate from the frozen 125-episode
primary C1-C5 dataset and from both earlier safety pilots. It creates a new
15-row replicated dataset only after every design cell has one scientifically
admissible episode.

Safety
------
Default mode is dry-run only: zero Kubernetes queries, zero Kubernetes
modifications, and zero workload requests. Live execution requires the exact
authorization frozen in this package, an immediate cluster preflight, and a
new empty output directory. Never delete failed attempts or manually retry an
individual treatment outside the resumable schedule.
