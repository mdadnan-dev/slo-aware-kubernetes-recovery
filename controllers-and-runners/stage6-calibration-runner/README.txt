C5 Fast Track Stage 6 R01

Purpose: execute a resumable ten-episode C2 calibration and derive T0-T8 stage budgets using median + 3 MAD.

Default behavior: one episode per invocation. Re-run the same command until 10 accepted episodes complete.
The runner preserves every attempt, never repeats an accepted episode, retains source timestamps, models T1 as interval-censored, and checks HPA restoration.

No C5 controller action or formal C1-C5 comparison is authorized by this stage.

R02 correction: a stale collector CPU poll after the HPA native action is not
used as the T1 lower bound. T1 is bounded by the last negative observation at
or before native T2. Existing R01 attempts are reanalyzed offline and are not
rerun when they satisfy all remaining acceptance predicates.
