# P2-07 R02 three-arm exploratory evaluation

Runs three newly preplanned randomized complete blocks, each containing C2, the
original C5 T1-T2 controller, and the multistage-selected bounded T4-T5 prewarm
action. The P2-06 pilot and interrupted P2-07 R01 episode are excluded.
Workload, SLO, recovery, censoring, collector, and restoration rules remain
frozen. Recovery after 300 seconds is right-censored even if a later TR exists.

With three pairs, the minimum attainable two-sided exact sign-flip p-value is
0.25. Results are exploratory and cannot establish conventional p<0.05
superiority. KM RMST(300), paired restricted-time differences, request outcomes,
censoring, action timing, T0-T8 missingness/non-applicability, and exact HPA
restoration are preserved. T7-T8 maps to HOLD_EXISTING_RECOVERY_CAPACITY when
the existing 300-second scale-down stabilization already covers the horizon.
