# Research Requirements Traceability

This checklist is the acceptance gate for the final submission. The frozen primary experiment is not altered by manuscript editing.

| Research requirement | Implementation in R02 | Status |
|---|---|---|
| Five increasing workload levels spanning normal through overload | 5, 10, 15, 20, and 25 requests/s; five blocks per workload-treatment cell | Complete |
| Identical duration, configuration, and SLO across C1–C5 | Fixed 60-s precondition, 180-s challenge, 300-s recovery, 30-s observation; one-to-five replicas; 123.472-ms SLO | Complete |
| Figure 1: recovery vs incoming requests | C1–C5 conditional KM RMST(300), block-bootstrap 95% intervals, and explicit recovered/censored/no-breach counts | Complete |
| Figure 2: SLO violations vs incoming requests | C1–C5 mean violation duration with block-bootstrap 95% intervals | Complete |
| Figure 3: resource consumption vs incoming requests | C1–C5 mean replica-seconds with block-bootstrap 95% intervals | Complete |
| Optional throughput figure only if additive | Omitted; 581,240/581,250 requests completed, so it adds little beyond the primary figures | Justified |
| Strong C1–C5 comparison | Treatment roles and identical design are stated; C5 is compared numerically with C2 at 20 and 25 requests/s | Complete |
| Investigate C5 recovery limitation | Intervention timing, desired/ready replicas, CPU, T4–T5, T6–T7, T7–T8, and the five-replica ceiling are discussed | Complete |
| Address T1–T2 versus dominant T7–T8 | Results identify T7–T8 as dominant and explain why the T1–T2 HPA-floor action was misaligned | Complete |
| Statistical reliability and censoring | Five randomized blocks per cell, 95% intervals, KM/RMST for breaches, right-censoring retained, no smoothing | Complete |
| Publication-quality presentation | Times-compatible font, fixed C1–C5 styling from the frozen analysis, units, legends, and subtle grids | Complete |
| Honest measurable claims and limitations | Manuscript states that C5 did not outperform C2 and quantifies recovery, SLO, and resource penalties | Complete |
| Numerical consistency | Abstract, results, discussion, conclusion, tables, and figures use the frozen 125-row dataset and its SHA-256 | Complete |
| Six to seven pages including references | Source is compact two-column format; final page count must be confirmed in Overleaf compilation | Overleaf check |
| GitHub implementation, dataset, and scripts | Final runner, frozen 125-row dataset, R02 offline analysis, tables, figures, manuscript source, and checksums are included in the final release branch | Complete |

## Frozen evidence

- Accepted episodes: 125
- Workload-treatment cells: 25, with five blocks each
- Scheduled/completed/errors: 581,250 / 581,240 / 10
- Outcomes: 72 no qualifying breach, 32 recovered, 21 right-censored
- Primary dataset SHA-256: `B7939B0FFE443AD76AC5ECE693CDFC03015C735895D27835E2DE2AF1A87781BD`

Any C5-R refinement is a future, separately labeled experiment. It must not overwrite or be merged into this frozen primary dataset.
