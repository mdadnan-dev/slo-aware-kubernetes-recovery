"""Pure decision logic for the exploratory NET470 C5-R2 controller.

No Kubernetes, subprocess, filesystem, network, or workload operations occur
in this module. A separate guarded runner must apply authorized mutations and
restore the exact original HPA specification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class State(str, Enum):
    IDLE = "IDLE"
    BREACH_CONFIRMED = "BREACH_CONFIRMED"
    SCALING = "SCALING"
    STABILIZING = "STABILIZING"
    STABLE_PENDING = "STABLE_PENDING"
    RECOVERED = "RECOVERED"
    CENSORED = "CENSORED"
    FAIL_SAFE = "FAIL_SAFE"


@dataclass(frozen=True)
class Config:
    slo_p95_ms: float = 123.472
    stable_windows_required: int = 8
    breach_windows_required: int = 2
    recovery_horizon_seconds: float = 300.0
    t7_t8_budget_seconds: float = 71.82882
    original_min_replicas: int = 1
    original_max_replicas: int = 5

    def validate(self) -> None:
        if self.slo_p95_ms <= 0:
            raise ValueError("slo_p95_ms must be positive")
        if self.stable_windows_required < 1:
            raise ValueError("stable_windows_required must be >= 1")
        if self.breach_windows_required < 1:
            raise ValueError("breach_windows_required must be >= 1")
        if self.recovery_horizon_seconds <= 0:
            raise ValueError("recovery_horizon_seconds must be positive")
        if self.t7_t8_budget_seconds <= 0:
            raise ValueError("t7_t8_budget_seconds must be positive")
        if self.original_min_replicas < 1:
            raise ValueError("original_min_replicas must be >= 1")
        if self.original_max_replicas < self.original_min_replicas:
            raise ValueError("max replicas cannot be below min replicas")


class Controller:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.config.validate()
        self.state = State.IDLE
        self.breach_streak = 0
        self.stable_streak = 0
        self.breach_confirmed = False
        self.floor_active = False
        self.initial_action_taken = False
        self.escalation_taken = False
        self.target_min_replicas: int | None = None

    def _result(self, *, action: str = "HOLD", authorized: bool = False,
                reason: str, **extra: Any) -> dict[str, Any]:
        result = {
            "state": self.state.value,
            "action": action,
            "authorized": authorized,
            "reason": reason,
            "target_min_replicas": self.target_min_replicas,
            "breach_confirmed": self.breach_confirmed,
            "breach_streak": self.breach_streak,
            "stable_streak": self.stable_streak,
            "floor_active": self.floor_active,
            "initial_action_taken": self.initial_action_taken,
            "escalation_taken": self.escalation_taken,
        }
        result.update(extra)
        return result

    def observe(self, observation: dict[str, Any]) -> dict[str, Any]:
        required = (
            "phase", "window_complete", "elapsed_recovery_seconds",
            "client_p95_ms", "completed_requests", "client_errors",
            "current_replicas", "desired_replicas", "ready_endpoints",
        )
        missing = [name for name in required if observation.get(name) is None]
        if missing:
            self.state = State.FAIL_SAFE
            return self._result(
                action="RESTORE_EXACT_HPA_SPEC", authorized=True,
                reason="required_observation_missing", missing_fields=missing,
            )

        elapsed = float(observation["elapsed_recovery_seconds"])
        phase = str(observation["phase"])
        complete = bool(observation["window_complete"])
        p95 = float(observation["client_p95_ms"])
        completed = int(observation["completed_requests"])
        errors = int(observation["client_errors"])
        current = int(observation["current_replicas"])
        desired = int(observation["desired_replicas"])
        ready = int(observation["ready_endpoints"])

        if min(completed, errors, current, desired, ready) < 0:
            self.state = State.FAIL_SAFE
            return self._result(
                action="RESTORE_EXACT_HPA_SPEC", authorized=True,
                reason="invalid_negative_observation",
            )
        if current > self.config.original_max_replicas or desired > self.config.original_max_replicas or ready > self.config.original_max_replicas:
            self.state = State.FAIL_SAFE
            return self._result(
                action="RESTORE_EXACT_HPA_SPEC", authorized=True,
                reason="replica_safety_bound_exceeded",
            )

        has_traffic = completed > 0
        slo_ok = complete and has_traffic and errors == 0 and p95 <= self.config.slo_p95_ms
        violating = not slo_ok
        high_phase = phase == "high-challenge"
        recovery_phase = phase in ("low-recovery", "post-observation")

        if not self.breach_confirmed and not high_phase:
            self.state = State.IDLE
            self.breach_streak = 0
            return self._result(reason="outside_actionable_challenge_stage", slo_ok=slo_ok)

        if elapsed > self.config.recovery_horizon_seconds and self.breach_confirmed:
            self.state = State.CENSORED
            return self._result(
                action="RESTORE_EXACT_HPA_SPEC", authorized=True,
                reason="recovery_right_censored_at_300_seconds", slo_ok=slo_ok,
            )

        if not self.breach_confirmed:
            self.breach_streak = self.breach_streak + 1 if violating else 0
            if self.breach_streak < self.config.breach_windows_required:
                self.state = State.IDLE
                return self._result(reason="breach_confirmation_pending", slo_ok=slo_ok)
            self.breach_confirmed = True
            self.state = State.BREACH_CONFIRMED

        if ready < desired:
            self.state = State.SCALING
            self.stable_streak = 0
            return self._result(reason="desired_replicas_not_ready", slo_ok=slo_ok)

        if violating or not has_traffic:
            self.state = State.STABILIZING
            self.stable_streak = 0

            if not self.initial_action_taken:
                target = min(
                    self.config.original_max_replicas,
                    max(self.config.original_min_replicas, current, desired, ready),
                )
                if target > self.config.original_min_replicas:
                    self.initial_action_taken = True
                    self.floor_active = True
                    self.target_min_replicas = target
                    return self._result(
                        action=f"HPA_MIN_TO_{target}", authorized=True,
                        reason="t7_t8_stabilization_support_qualified", slo_ok=slo_ok,
                    )
                return self._result(reason="no_bounded_floor_increase_available", slo_ok=slo_ok)

            synchronized = current == desired == ready
            budget_exceeded = elapsed >= self.config.t7_t8_budget_seconds
            below_maximum = current < self.config.original_max_replicas
            if (
                self.floor_active
                and not self.escalation_taken
                and budget_exceeded
                and synchronized
                and below_maximum
            ):
                self.escalation_taken = True
                self.target_min_replicas = self.config.original_max_replicas
                return self._result(
                    action=f"HPA_MIN_TO_{self.config.original_max_replicas}",
                    authorized=True,
                    reason="t7_t8_budget_exceeded_guarded_escalation",
                    slo_ok=slo_ok,
                    calibrated_t7_t8_budget_seconds=self.config.t7_t8_budget_seconds,
                )

            if self.escalation_taken:
                return self._result(reason="guarded_escalation_already_taken", slo_ok=slo_ok)
            if not budget_exceeded:
                return self._result(reason="guarded_escalation_budget_pending", slo_ok=slo_ok)
            if not synchronized:
                return self._result(reason="guarded_escalation_replica_sync_absent", slo_ok=slo_ok)
            if not below_maximum:
                return self._result(reason="replica_ceiling_already_reached", slo_ok=slo_ok)
            return self._result(reason="recovery_floor_already_active", slo_ok=slo_ok)

        if slo_ok and high_phase:
            self.state = State.STABILIZING
            self.stable_streak = 0
            return self._result(reason="high_challenge_support_held", slo_ok=True)

        if slo_ok and recovery_phase:
            self.stable_streak += 1
            self.state = State.STABLE_PENDING
            if self.stable_streak >= self.config.stable_windows_required:
                self.state = State.RECOVERED
                return self._result(
                    action="RESTORE_EXACT_HPA_SPEC", authorized=True,
                    reason="stable_recovery_confirmed", slo_ok=True,
                )
            return self._result(reason="stable_confirmation_pending", slo_ok=True)

        self.state = State.FAIL_SAFE
        return self._result(
            action="RESTORE_EXACT_HPA_SPEC", authorized=True,
            reason="unclassified_observation", slo_ok=slo_ok,
        )


def observation(**changes: Any) -> dict[str, Any]:
    value = {
        "phase": "low-recovery", "window_complete": True,
        "elapsed_recovery_seconds": 0.0, "client_p95_ms": 100.0,
        "completed_requests": 75, "client_errors": 0,
        "current_replicas": 1, "desired_replicas": 1,
        "ready_endpoints": 1,
    }
    value.update(changes)
    return value


def self_test() -> bool:
    # One bad window cannot confirm a breach.
    c = Controller()
    r = c.observe(observation(phase="high-challenge", client_p95_ms=500.0))
    assert r["action"] == "HOLD" and r["state"] == "IDLE"

    # Second bad window with synchronized replicas enables the initial floor.
    r = c.observe(observation(phase="high-challenge", client_p95_ms=500.0,
                              current_replicas=3, desired_replicas=3,
                              ready_endpoints=3))
    assert r["authorized"] and r["action"] == "HPA_MIN_TO_3"

    # A violation before the frozen T7-T8 budget cannot escalate.
    r = c.observe(observation(phase="high-challenge", client_p95_ms=200.0,
                              elapsed_recovery_seconds=60.0,
                              current_replicas=3, desired_replicas=3,
                              ready_endpoints=3))
    assert not r["authorized"] and r["reason"] == "guarded_escalation_budget_pending"

    # Replica readiness remains a hard gate even after the budget.
    r = c.observe(observation(phase="high-challenge", client_p95_ms=200.0,
                              elapsed_recovery_seconds=90.0,
                              current_replicas=3, desired_replicas=5,
                              ready_endpoints=3))
    assert not r["authorized"] and r["state"] == "SCALING"

    # A post-budget violation with synchronized replicas permits one escalation.
    r = c.observe(observation(phase="high-challenge", client_p95_ms=200.0,
                              elapsed_recovery_seconds=105.0,
                              current_replicas=3, desired_replicas=3,
                              ready_endpoints=3))
    assert r["authorized"] and r["action"] == "HPA_MIN_TO_5"
    assert r["reason"] == "t7_t8_budget_exceeded_guarded_escalation"

    # A second escalation is prohibited.
    r = c.observe(observation(phase="high-challenge", client_p95_ms=200.0,
                              elapsed_recovery_seconds=120.0,
                              current_replicas=4, desired_replicas=4,
                              ready_endpoints=4))
    assert not r["authorized"] and r["reason"] == "guarded_escalation_already_taken"

    # Eight complete SLO-good recovery windows are still required.
    for expected in range(1, 8):
        r = c.observe(observation(elapsed_recovery_seconds=130.0 + 15 * expected,
                                  current_replicas=5, desired_replicas=5,
                                  ready_endpoints=5))
        assert r["state"] == "STABLE_PENDING" and r["stable_streak"] == expected
    r = c.observe(observation(elapsed_recovery_seconds=250.0,
                              current_replicas=5, desired_replicas=5,
                              ready_endpoints=5))
    assert r["state"] == "RECOVERED" and r["stable_streak"] == 8
    assert r["action"] == "RESTORE_EXACT_HPA_SPEC" and r["authorized"]

    # The strict 300-second censoring boundary remains unchanged.
    c = Controller()
    c.observe(observation(phase="high-challenge", client_p95_ms=500.0))
    c.observe(observation(phase="high-challenge", client_p95_ms=500.0,
                          current_replicas=3, desired_replicas=3,
                          ready_endpoints=3))
    r = c.observe(observation(elapsed_recovery_seconds=301.0,
                              client_p95_ms=100.0,
                              current_replicas=5, desired_replicas=5,
                              ready_endpoints=5))
    assert r["state"] == "CENSORED" and r["authorized"]

    # Missing data and out-of-bounds replicas fail safe.
    c = Controller()
    r = c.observe({})
    assert r["state"] == "FAIL_SAFE" and r["action"] == "RESTORE_EXACT_HPA_SPEC"
    c = Controller()
    r = c.observe(observation(phase="high-challenge", current_replicas=6))
    assert r["state"] == "FAIL_SAFE" and r["reason"] == "replica_safety_bound_exceeded"
    return True


if __name__ == "__main__":
    assert self_test()
    print("NET470_C5R2_CONTROLLER_SELF_TEST=PASS")
