# Part 2 Step 2 Dominant Stage Diagnosis

This package adds an offline-safe dominant-stage diagnosis and stage-to-action
selection core. It does not mutate Kubernetes. It preserves the frozen Stage 9
and Stage 10 evidence.

Budgets are fitted as `median + 3*MAD` from C2 and are bounded below by a
one-second resolution floor so shared coarse timestamps do not create a false
zero-time budget. The diagnosis score is the normalized positive excess:

`max(0, observed - budget) / max(budget, 1 second)`

A stage must exceed both the absolute and relative thresholds. Missing values
are ignored rather than imputed. Every proposed action remains unauthorized
until its adapter is separately qualified.

Stable recovery uses the frozen protocol rule: eight consecutive complete
15-second windows at or below the 123.472 ms p95 threshold after breach.

Run the unit tests from the Part 2 root:

`python -m unittest discover -s tests -v`

Fit prospective C2 budgets from the frozen Stage 10 duration table:

`python analysis/fit_stage_budgets.py --input <C5-T0-T8-stage-durations.csv> --output config/stage-budgets.json`

