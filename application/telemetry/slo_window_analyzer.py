"""Configurable SLO-window analysis for NET 470 telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from telemetry.writers import AppendOnlyTelemetryWriter


BAD_WINDOWS_REQUIRED = 3
GOOD_WINDOWS_REQUIRED_FOR_RECOVERY = 2


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()


def _parse_utc(value: str | datetime, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} is not a valid timestamp") from exc
    else:
        raise ValueError(f"{field} must be a timestamp")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")

    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be UTC")

    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


def _nonnegative_integer(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} cannot be negative")
    return value


@dataclass(frozen=True)
class SloWindowPolicy:
    """Caller-supplied policy; Guide 3 does not select its values."""

    policy_id: str
    latency_threshold_ms: float
    minimum_success_ratio: float
    minimum_request_count: int

    def __post_init__(self) -> None:
        _required_text(self.policy_id, "policy_id")

        latency = float(self.latency_threshold_ms)
        ratio = float(self.minimum_success_ratio)

        if not isfinite(latency) or latency <= 0:
            raise ValueError(
                "latency_threshold_ms must be finite and positive"
            )

        if not isfinite(ratio) or ratio < 0 or ratio > 1:
            raise ValueError(
                "minimum_success_ratio must be between zero and one"
            )

        if (
            isinstance(self.minimum_request_count, bool)
            or not isinstance(self.minimum_request_count, int)
            or self.minimum_request_count < 1
        ):
            raise ValueError(
                "minimum_request_count must be a positive integer"
            )


class SloWindowAnalyzer:
    """Classify complete windows and preserve T0/T8 semantics."""

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        episode_id: str,
        writer_instance_id: str,
        analyzer_version: str,
        input_hashes: str,
        policy: SloWindowPolicy,
        recovery_eligible_after_utc: str | datetime | None,
    ) -> None:
        self.run_id = _required_text(run_id, "run_id")
        self.episode_id = _required_text(
            episode_id,
            "episode_id",
        )
        self.writer_instance_id = _required_text(
            writer_instance_id,
            "writer_instance_id",
        )
        self.analyzer_version = _required_text(
            analyzer_version,
            "analyzer_version",
        )
        self.input_hashes = _required_text(
            input_hashes,
            "input_hashes",
        )

        if not isinstance(policy, SloWindowPolicy):
            raise ValueError("policy must be an SloWindowPolicy")

        self.policy = policy

        self.recovery_eligible_after = (
            None
            if recovery_eligible_after_utc is None
            else _parse_utc(
                recovery_eligible_after_utc,
                "recovery_eligible_after_utc",
            )
        )

        self.writer = AppendOnlyTelemetryWriter(
            root,
            stream_id="slo_windows",
            run_id=self.run_id,
            writer_name="window_analyzer",
            writer_instance_id=self.writer_instance_id,
        )

        self._seen_sequences: set[int] = set()
        self._last_sequence: int | None = None
        self._consecutive_bad = 0
        self._first_bad_window_start: datetime | None = None
        self._breach_start: datetime | None = None
        self._breach_confirmed_at: datetime | None = None
        self._consecutive_recovery_good = 0
        self._recovery_at: datetime | None = None

    def record_window(
        self,
        *,
        window_sequence: int,
        window_start_utc: str | datetime,
        window_end_utc: str | datetime,
        complete: bool,
        request_count: int,
        successful_request_count: int,
        p95_latency_ms: float | None,
        incomplete_reason: str = "",
    ) -> dict[str, Any]:
        sequence = _nonnegative_integer(
            window_sequence,
            "window_sequence",
        )

        if sequence in self._seen_sequences:
            raise ValueError(
                f"duplicate window_sequence: {sequence}"
            )

        if (
            self._last_sequence is not None
            and sequence <= self._last_sequence
        ):
            raise ValueError(
                "window_sequence must increase monotonically"
            )

        if not isinstance(complete, bool):
            raise ValueError("complete must be boolean")

        requests = _nonnegative_integer(
            request_count,
            "request_count",
        )
        successes = _nonnegative_integer(
            successful_request_count,
            "successful_request_count",
        )

        if successes > requests:
            raise ValueError(
                "successful_request_count exceeds request_count"
            )

        start = _parse_utc(
            window_start_utc,
            "window_start_utc",
        )
        end = _parse_utc(
            window_end_utc,
            "window_end_utc",
        )

        if end <= start:
            raise ValueError(
                "window_end_utc must be after window_start_utc"
            )

        if not complete and not incomplete_reason.strip():
            raise ValueError(
                "incomplete windows require incomplete_reason"
            )

        latency: float | None

        if p95_latency_ms is None:
            latency = None
        else:
            latency = float(p95_latency_ms)
            if not isfinite(latency) or latency < 0:
                raise ValueError(
                    "p95_latency_ms must be finite and nonnegative"
                )

        sequence_gap = (
            self._last_sequence is not None
            and sequence != self._last_sequence + 1
        )

        consecutive_bad = self._consecutive_bad
        first_bad_start = self._first_bad_window_start
        breach_start = self._breach_start
        breach_confirmed = self._breach_confirmed_at
        recovery_good = self._consecutive_recovery_good
        recovery_at = self._recovery_at

        if sequence_gap:
            consecutive_bad = 0
            first_bad_start = None
            if recovery_at is None:
                recovery_good = 0

        success_ratio = (
            successes / requests
            if requests > 0
            else 0.0
        )

        if not complete:
            classification = "INCOMPLETE"
            consecutive_bad = 0
            first_bad_start = None
            if recovery_at is None:
                recovery_good = 0
        else:
            is_good = (
                requests >= self.policy.minimum_request_count
                and success_ratio
                >= self.policy.minimum_success_ratio
                and latency is not None
                and latency
                <= self.policy.latency_threshold_ms
            )

            classification = "GOOD" if is_good else "BAD"

            if classification == "BAD":
                if consecutive_bad == 0:
                    first_bad_start = start

                consecutive_bad += 1

                if recovery_at is None:
                    recovery_good = 0

                if (
                    breach_start is None
                    and consecutive_bad
                    == BAD_WINDOWS_REQUIRED
                ):
                    breach_start = first_bad_start
                    breach_confirmed = end
            else:
                consecutive_bad = 0
                first_bad_start = None

                if breach_start is None:
                    recovery_good = 0
                elif recovery_at is None:
                    effective_boundary = breach_start

                    if (
                        self.recovery_eligible_after is not None
                        and self.recovery_eligible_after
                        > effective_boundary
                    ):
                        effective_boundary = (
                            self.recovery_eligible_after
                        )

                    eligible = (
                        self.recovery_eligible_after is not None
                        and start >= effective_boundary
                    )

                    if eligible:
                        recovery_good += 1
                        if (
                            recovery_good
                            == GOOD_WINDOWS_REQUIRED_FOR_RECOVERY
                        ):
                            recovery_at = end
                    else:
                        recovery_good = 0

        breach_confirmed_this_window = (
            breach_confirmed is not None
            and self._breach_confirmed_at is None
        )

        recovery_confirmed_this_window = (
            recovery_at is not None
            and self._recovery_at is None
        )

        recovery_window_eligible = False

        if (
            complete
            and classification == "GOOD"
            and breach_start is not None
            and recovery_at is None
            and self.recovery_eligible_after is not None
        ):
            effective_boundary = max(
                breach_start,
                self.recovery_eligible_after,
            )
            recovery_window_eligible = start >= effective_boundary

        if recovery_confirmed_this_window:
            recovery_window_eligible = True

        record = {
            "run_id": self.run_id,
            "window_sequence": sequence,
            "episode_id": self.episode_id,
            "window_start_utc": _utc_text(start),
            "window_end_utc": _utc_text(end),
            "writer_instance_id": self.writer_instance_id,
            "analyzer_version": self.analyzer_version,
            "input_hashes": self.input_hashes,
            "policy_id": self.policy.policy_id,
            "latency_threshold_ms": (
                self.policy.latency_threshold_ms
            ),
            "minimum_success_ratio": (
                self.policy.minimum_success_ratio
            ),
            "minimum_request_count": (
                self.policy.minimum_request_count
            ),
            "complete": complete,
            "incomplete_reason": (
                "" if complete else incomplete_reason.strip()
            ),
            "request_count": requests,
            "successful_request_count": successes,
            "success_ratio": round(success_ratio, 12),
            "p95_latency_ms": (
                "" if latency is None else latency
            ),
            "classification": classification,
            "sequence_gap": sequence_gap,
            "consecutive_bad_complete_windows": (
                consecutive_bad
            ),
            "qualifying_breach_observed": (
                breach_start is not None
            ),
            "breach_confirmed_this_window": (
                breach_confirmed_this_window
            ),
            "first_bad_window_start_utc": (
                ""
                if breach_start is None
                else _utc_text(breach_start)
            ),
            "breach_confirmed_at_utc": (
                ""
                if breach_confirmed is None
                else _utc_text(breach_confirmed)
            ),
            "recovery_eligible_after_utc": (
                ""
                if self.recovery_eligible_after is None
                else _utc_text(
                    self.recovery_eligible_after
                )
            ),
            "recovery_window_eligible": (
                recovery_window_eligible
            ),
            "consecutive_good_complete_windows": (
                recovery_good
            ),
            "sustained_recovery_observed": (
                recovery_at is not None
            ),
            "recovery_confirmed_this_window": (
                recovery_confirmed_this_window
            ),
            "second_good_window_end_utc": (
                ""
                if recovery_at is None
                else _utc_text(recovery_at)
            ),
        }

        normalized = self.writer.append(record)

        self._seen_sequences.add(sequence)
        self._last_sequence = sequence
        self._consecutive_bad = consecutive_bad
        self._first_bad_window_start = first_bad_start
        self._breach_start = breach_start
        self._breach_confirmed_at = breach_confirmed
        self._consecutive_recovery_good = recovery_good
        self._recovery_at = recovery_at

        return normalized

    def summary(
        self,
        *,
        censor_time_utc: str | datetime | None = None,
    ) -> dict[str, Any]:
        censor_time = (
            None
            if censor_time_utc is None
            else _parse_utc(
                censor_time_utc,
                "censor_time_utc",
            )
        )

        if self._breach_start is None:
            outcome = "NO_QUALIFYING_BREACH"
        elif self._recovery_at is not None:
            outcome = "RECOVERED"
        elif censor_time is not None:
            outcome = "CENSORED"
        else:
            outcome = "OPEN"

        recovery_duration_seconds: float | str = ""

        if (
            self._breach_start is not None
            and self._recovery_at is not None
        ):
            recovery_duration_seconds = (
                self._recovery_at - self._breach_start
            ).total_seconds()

        return {
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "outcome": outcome,
            "first_bad_window_start_utc": (
                ""
                if self._breach_start is None
                else _utc_text(self._breach_start)
            ),
            "breach_confirmed_at_utc": (
                ""
                if self._breach_confirmed_at is None
                else _utc_text(self._breach_confirmed_at)
            ),
            "second_good_window_end_utc": (
                ""
                if self._recovery_at is None
                else _utc_text(self._recovery_at)
            ),
            "censor_time_utc": (
                ""
                if censor_time is None
                else _utc_text(censor_time)
            ),
            "recovery_duration_seconds": (
                recovery_duration_seconds
            ),
        }

    def close(self) -> None:
        self.writer.close()

    def __enter__(self) -> "SloWindowAnalyzer":
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()
