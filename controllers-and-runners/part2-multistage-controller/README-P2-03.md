# P2-03 Offline Replay

This step replays the frozen dominant-stage rule over the 50 selected Stage 10
episodes. It performs no Kubernetes query or mutation. Results are retrospective
diagnostic evidence only and do not establish that any recommended action
improves recovery.

Outputs:

- `P2-replay-decisions.csv`: one decision per episode
- `P2-replay-summary.csv`: overall and treatment-level counts
- `P2-replay-conclusion.json`: hash-bound replay conclusion

