# P2-06 controlled pilot R02

Runs exactly two sequential live workload episodes using the frozen Stage 9 R07
episode core: C2 tuned static HPA and the selected bounded T4-T5 prewarm action.

The prewarm action is applied during the low-precondition phase, must reach two
available processing replicas before T0, and is restored at low recovery and in
`finally`. Stable recovery uses the frozen 123.472 ms threshold, 15-second
windows, eight consecutive compliant windows, and the 300-second censoring
horizon. Stage 9 and Stage 10 evidence are never modified.

This is a two-episode technical and directional pilot. It cannot establish
statistical superiority.

R02 uses the established `C5-` episode-ID and `V3-K-` workload-run-ID prefixes.
It is resumable: prior failed attempts remain preserved, the next attempt number
is used, and an already scientifically admissible arm is selected rather than
rerun.
