# P2-05 Bounded Prewarm Qualification

This qualification briefly changes only `processing-hpa.spec.minReplicas` from
1 to 2, verifies two available processing replicas, and restores the exact
original HPA specification in `finally`. It executes no workload and makes no
application-code change. The live path requires an exact action acknowledgement.

Passing this step proves safe actuation and restoration only. It does not prove
recovery-time improvement; that requires a prospective workload comparison.

