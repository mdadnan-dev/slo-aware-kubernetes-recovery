"""Offline-safe dominant-stage diagnosis and action selection for Part 2.

No Kubernetes mutation is performed by this module. Live action adapters must be
qualified separately before they are enabled.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping, Any


STAGES = ("T0_T1", "T1_T2", "T2_T3", "T3_T4", "T4_T5", "T5_T6", "T6_T7", "T7_T8")


@dataclass(frozen=True)
class Decision:
    dominant_stage: str | None
    observed_seconds: float | None
    budget_seconds: float | None
    excess_seconds: float
    excess_ratio: float
    action: str
    mode: str
    authorized: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ACTION_MAP = {
    "T0_T1": ("HOLD_METRIC_PATH_UNQUALIFIED", "DIAGNOSIS_ONLY"),
    "T1_T2": ("EVALUATE_BOUNDED_HPA_MIN_FLOOR", "QUALIFICATION_REQUIRED"),
    "T2_T3": ("HOLD_EXISTING_SCALE_DECISION", "DIAGNOSIS_ONLY"),
    "T3_T4": ("EVALUATE_BOUNDED_PREWARM", "QUALIFICATION_REQUIRED"),
    "T4_T5": ("EVALUATE_SCHEDULING_PREWARM", "QUALIFICATION_REQUIRED"),
    "T5_T6": ("EVALUATE_STARTUP_PREPARED_CAPACITY", "QUALIFICATION_REQUIRED"),
    "T6_T7": ("EVALUATE_READINESS_PATH_REMEDIATION", "QUALIFICATION_REQUIRED"),
    "T7_T8": ("EVALUATE_BOUNDED_CONCURRENCY_OR_QUEUE_CONTROL", "QUALIFICATION_REQUIRED"),
}


def diagnose(
    durations: Mapping[str, float | int | None],
    budgets: Mapping[str, float | int],
    *,
    minimum_excess_seconds: float = 1.0,
    minimum_excess_ratio: float = 0.10,
) -> Decision:
    """Select the largest qualified normalized stage-budget excess.

    Score = max(0, duration - budget) / max(budget, 1 second).
    Ties are resolved by larger absolute excess, then fixed T0–T8 order.
    Missing stages are ignored and never imputed.
    """
    candidates: list[tuple[float, float, int, str, float, float]] = []
    for index, stage in enumerate(STAGES):
        value = durations.get(stage)
        budget = budgets.get(stage)
        if value is None or budget is None:
            continue
        observed = float(value)
        limit = max(0.0, float(budget))
        if observed < 0.0:
            raise ValueError(f"negative duration for {stage}")
        excess = max(0.0, observed - limit)
        ratio = excess / max(limit, 1.0)
        if excess >= minimum_excess_seconds and ratio >= minimum_excess_ratio:
            candidates.append((ratio, excess, -index, stage, observed, limit))

    if not candidates:
        return Decision(None, None, None, 0.0, 0.0, "HOLD", "NO_ACTION", False,
                        "no_stage_exceeded_both_frozen_thresholds")

    ratio, excess, _, stage, observed, limit = max(candidates)
    action, mode = ACTION_MAP[stage]
    return Decision(stage, observed, limit, excess, ratio, action, mode, False,
                    "dominant_stage_identified_action_not_live_authorized")


def stable_recovery(window_p95_ms: list[float], slo_ms: float, required_windows: int = 8) -> bool:
    """Return true only when the most recent required windows all meet the SLO."""
    if required_windows <= 0:
        raise ValueError("required_windows must be positive")
    if len(window_p95_ms) < required_windows:
        return False
    return all(float(v) <= float(slo_ms) for v in window_p95_ms[-required_windows:])

