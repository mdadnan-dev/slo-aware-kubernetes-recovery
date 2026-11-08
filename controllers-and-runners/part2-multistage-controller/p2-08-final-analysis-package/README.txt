NET470 Part 2 P2-08 Final Offline Analysis R01
================================================

Purpose
-------
Validate the frozen P2-07 R02 three-arm dataset, produce paper-ready tables
and SVG figures, disclose T0-T8 missingness without imputation, and create a
hash-bound final freeze.

Safety properties
-----------------
- No Kubernetes calls.
- No cluster mutation.
- No live workload.
- Does not modify frozen Stage 9, Stage 10 or P2-07 evidence.
- Requires the exact P2-07 freeze SHA-256 embedded in the package.

Inputs
------
P2-07 root:
  ...\analysis\p2-07-r02-three-arm

Outputs
-------
- normalized three-arm dataset
- treatment and paired-comparison tables
- T0-T8 duration and missingness tables
- action/restoration audit
- professor-requirements checklist
- three SVG paper figures
- paper-ready results paragraph
- final analysis record, manifest and freeze

Interpretation guardrail
------------------------
The three-block study is exploratory. It cannot establish superiority or
equivalence. Missing or structurally non-applicable T0-T8 stages are not
imputed.
