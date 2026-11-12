# NET470 Final Five-Level C1-C5 Stress Runner

This package executes the research-required comparison at 5, 10, 15, 20 and 25 requests/s. It runs one randomized C1-C5 block per invocation by default and resumes from the earliest incomplete block.

## Scope

- Primary contrast: C5 minus C2 at each workload level.
- Supporting treatments: C1, C3 and C4.
- C5 remains the original frozen recovery-stage-aware controller so the experiment tests whether its T1-T2 action improves or degrades downstream T7-T8 stabilization.
- PREWARM remains a separate exploratory result and is not a sixth treatment.

## Outcome rules

- No qualifying breach: `NO_QUALIFYING_BREACH`; no recovery was required.
- Breach followed by stable recovery within 300 seconds: observed recovery.
- Breach without stable recovery by 300 seconds: right-censored at exactly 300 seconds.
- Missing or structurally non-applicable T4-T5, T6-T7 or T7-T8 events remain missing.

## Safety

- Dry run is the default.
- Live execution requires the exact acknowledgement phrase.
- Source Stage 9 core and runner hashes are verified.
- Runtime copies are parameterized; the source package is never edited.
- Original HPA specification is restored after every attempt.
- Failed attempts and core stdout/stderr are preserved.

Run the dry validation before any live block.
