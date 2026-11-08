"""Canonical T0-T8 timeline records for NET 470 telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from telemetry.writers import AppendOnlyTelemetryWriter


EPISODE_EVENTS = ("T0", "T1", "T2", "T8")
POD_EVENTS = ("T3", "T4", "T5", "T6", "T7")
ALL_EVENTS = EPISODE_EVENTS + POD_EVENTS

EVENT_SCOPE = {
    "T0": "episode",
    "T1": "episode",
    "T2": "episode",
    "T3": "per_pod",
    "T4": "per_pod",
    "T5": "per_pod",
    "T6": "per_pod",
    "T7": "per_pod",
    "T8": "episode",
}

EVENT_SOURCE = {
    "T0": "slo_windows",
    "T1": "resource_metrics",
    "T2": "hpa_state",
    "T3": "pod_lifecycle",
    "T4": "pod_lifecycle",
    "T5": "pod_lifecycle",
    "T6": "pod_lifecycle",
    "T7": "application_events+request_results",
    "T8": "slo_windows",
}


class TimelineJoinError(ValueError):
    """Raised when canonical timeline evidence is inconsistent."""


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TimelineJoinError(f"{field} must be non-empty")
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
            raise TimelineJoinError(
                f"{field} is not a valid timestamp"
            ) from exc
    else:
        raise TimelineJoinError(f"{field} must be a timestamp")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TimelineJoinError(
            f"{field} must include a UTC offset"
        )

    if parsed.utcoffset().total_seconds() != 0:
        raise TimelineJoinError(f"{field} must be UTC")

    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


class CanonicalTimelineJoiner:
    """Append and validate one canonical episode timeline."""

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        episode_id: str,
        expected_pod_uids: Iterable[str],
        writer_instance_id: str,
        joiner_version: str,
        input_hashes: str,
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
        self.joiner_version = _required_text(
            joiner_version,
            "joiner_version",
        )
        self.input_hashes = _required_text(
            input_hashes,
            "input_hashes",
        )

        if isinstance(expected_pod_uids, (str, bytes)):
            raise TimelineJoinError(
                "expected_pod_uids must be an iterable of pod UIDs"
            )

        pod_values: list[str] = []

        for pod_uid in expected_pod_uids:
            normalized = _required_text(pod_uid, "pod_uid")
            if normalized == "NA":
                raise TimelineJoinError(
                    "expected pod UID cannot be NA"
                )
            pod_values.append(normalized)

        if len(set(pod_values)) != len(pod_values):
            raise TimelineJoinError(
                "expected_pod_uids contains duplicates"
            )

        self.expected_pod_uids = tuple(sorted(pod_values))
        self._expected_pod_set = set(self.expected_pod_uids)
        self._records: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

        self.writer = AppendOnlyTelemetryWriter(
            root,
            stream_id="canonical_timeline",
            run_id=self.run_id,
            writer_name="timeline_joiner",
            writer_instance_id=self.writer_instance_id,
        )

    def _event_identities(
        self,
        event_id: str,
        *,
        pod_uid: str | None,
        request_id: str | None,
        missing: bool,
    ) -> tuple[str, str]:
        if event_id not in ALL_EVENTS:
            raise TimelineJoinError(
                f"unknown canonical event: {event_id}"
            )

        if EVENT_SCOPE[event_id] == "episode":
            if pod_uid not in (None, "", "NA"):
                raise TimelineJoinError(
                    f"{event_id} is episode scoped"
                )
            if request_id not in (None, "", "NA"):
                raise TimelineJoinError(
                    f"{event_id} cannot carry request_id"
                )
            return "NA", "NA"

        normalized_pod = _required_text(
            pod_uid or "",
            "pod_uid",
        )

        if normalized_pod not in self._expected_pod_set:
            raise TimelineJoinError(
                f"unexpected pod_uid: {normalized_pod}"
            )

        if event_id == "T7" and not missing:
            if request_id in (None, "", "NA"):
                raise TimelineJoinError(
                    "observed T7 requires request_id"
                )

            normalized_request = _required_text(
                request_id,
                "request_id",
            )
        else:
            if request_id not in (None, "", "NA"):
                raise TimelineJoinError(
                    f"{event_id} cannot carry request_id"
                )
            normalized_request = "NA"

        return normalized_pod, normalized_request

    def _ensure_unique(
        self,
        event_id: str,
        pod_uid: str,
    ) -> None:
        key = (event_id, pod_uid)

        if key in self._records:
            raise TimelineJoinError(
                "duplicate canonical event key: "
                f"{event_id}/{pod_uid}"
            )

    def record_observed(
        self,
        *,
        event_id: str,
        event_time_utc: str | datetime,
        observed_at_utc: str | datetime,
        source_stream: str,
        source_record_key: str,
        pod_uid: str | None = None,
        request_id: str | None = None,
        breach_confirmation_at_utc: (
            str | datetime | None
        ) = None,
    ) -> dict[str, Any]:
        if event_id not in ALL_EVENTS:
            raise TimelineJoinError(
                f"unknown canonical event: {event_id}"
            )

        expected_source = EVENT_SOURCE[event_id]

        if source_stream != expected_source:
            raise TimelineJoinError(
                f"{event_id} requires source {expected_source}"
            )

        normalized_pod, normalized_request = (
            self._event_identities(
                event_id,
                pod_uid=pod_uid,
                request_id=request_id,
                missing=False,
            )
        )

        self._ensure_unique(event_id, normalized_pod)

        event_time = _parse_utc(
            event_time_utc,
            "event_time_utc",
        )
        observed_at = _parse_utc(
            observed_at_utc,
            "observed_at_utc",
        )

        if observed_at < event_time:
            raise TimelineJoinError(
                "observed_at_utc cannot precede event_time_utc"
            )

        confirmation_text = ""

        if event_id == "T0":
            if breach_confirmation_at_utc is None:
                raise TimelineJoinError(
                    "T0 requires separate breach confirmation time"
                )

            confirmation = _parse_utc(
                breach_confirmation_at_utc,
                "breach_confirmation_at_utc",
            )

            if confirmation < event_time:
                raise TimelineJoinError(
                    "breach confirmation cannot precede T0"
                )

            if observed_at < confirmation:
                raise TimelineJoinError(
                    "T0 observation cannot precede confirmation"
                )

            confirmation_text = _utc_text(confirmation)
        elif breach_confirmation_at_utc is not None:
            raise TimelineJoinError(
                "breach confirmation is valid only for T0"
            )

        record = {
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "event_id": event_id,
            "pod_uid": normalized_pod,
            "request_id": normalized_request,
            "event_time_utc": _utc_text(event_time),
            "observed_at_utc": _utc_text(observed_at),
            "writer_instance_id": self.writer_instance_id,
            "joiner_version": self.joiner_version,
            "input_hashes": self.input_hashes,
            "source_record_key": _required_text(
                source_record_key,
                "source_record_key",
            ),
            "source_stream": source_stream,
            "event_scope": EVENT_SCOPE[event_id],
            "event_status": "OBSERVED",
            "missing_reason": "",
            "breach_confirmation_at_utc": confirmation_text,
        }

        normalized = self.writer.append(record)
        self._records[(event_id, normalized_pod)] = normalized
        return normalized

    def record_missing(
        self,
        *,
        event_id: str,
        missing_reason: str,
        pod_uid: str | None = None,
    ) -> dict[str, Any]:
        if event_id not in ALL_EVENTS:
            raise TimelineJoinError(
                f"unknown canonical event: {event_id}"
            )

        normalized_pod, normalized_request = (
            self._event_identities(
                event_id,
                pod_uid=pod_uid,
                request_id=None,
                missing=True,
            )
        )

        self._ensure_unique(event_id, normalized_pod)

        record = {
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "event_id": event_id,
            "pod_uid": normalized_pod,
            "request_id": normalized_request,
            "event_time_utc": "NA",
            "observed_at_utc": "NA",
            "writer_instance_id": self.writer_instance_id,
            "joiner_version": self.joiner_version,
            "input_hashes": self.input_hashes,
            "source_record_key": "NA",
            "source_stream": EVENT_SOURCE[event_id],
            "event_scope": EVENT_SCOPE[event_id],
            "event_status": "MISSING",
            "missing_reason": _required_text(
                missing_reason,
                "missing_reason",
            ),
            "breach_confirmation_at_utc": "",
        }

        normalized = self.writer.append(record)
        self._records[(event_id, normalized_pod)] = normalized
        return normalized

    def _observed_time(
        self,
        event_id: str,
        pod_uid: str,
    ) -> datetime | None:
        record = self._records.get((event_id, pod_uid))

        if (
            record is None
            or record["event_status"] != "OBSERVED"
        ):
            return None

        return _parse_utc(
            record["event_time_utc"],
            "event_time_utc",
        )

    def _validate_order(
        self,
        event_ids: tuple[str, ...],
        pod_uid: str,
    ) -> None:
        observed: list[tuple[str, datetime]] = []

        for event_id in event_ids:
            event_time = self._observed_time(
                event_id,
                pod_uid,
            )
            if event_time is not None:
                observed.append((event_id, event_time))

        for previous, current in zip(
            observed,
            observed[1:],
        ):
            if previous[1] > current[1]:
                raise TimelineJoinError(
                    "canonical event order is invalid: "
                    f"{previous[0]}>{current[0]}"
                )

    def validate_timeline(self) -> dict[str, Any]:
        missing_keys: list[str] = []

        for event_id in EPISODE_EVENTS:
            if (event_id, "NA") not in self._records:
                missing_keys.append(f"{event_id}/NA")

        for pod_uid in self.expected_pod_uids:
            for event_id in POD_EVENTS:
                if (event_id, pod_uid) not in self._records:
                    missing_keys.append(
                        f"{event_id}/{pod_uid}"
                    )

        if missing_keys:
            raise TimelineJoinError(
                "canonical timeline lacks explicit records: "
                + ",".join(missing_keys)
            )

        self._validate_order(EPISODE_EVENTS, "NA")

        for pod_uid in self.expected_pod_uids:
            self._validate_order(POD_EVENTS, pod_uid)

        t8 = self._observed_time("T8", "NA")

        if t8 is not None:
            t7_times = [
                event_time
                for pod_uid in self.expected_pod_uids
                if (
                    event_time := self._observed_time(
                        "T7",
                        pod_uid,
                    )
                )
                is not None
            ]

            if t7_times and t8 < max(t7_times):
                raise TimelineJoinError(
                    "T8 precedes the last observed T7"
                )

        observed_count = sum(
            record["event_status"] == "OBSERVED"
            for record in self._records.values()
        )

        missing_count = sum(
            record["event_status"] == "MISSING"
            for record in self._records.values()
        )

        return {
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "expected_pod_count": len(
                self.expected_pod_uids
            ),
            "record_count": len(self._records),
            "observed_count": observed_count,
            "missing_count": missing_count,
            "status": "VALID",
        }

    def close(self) -> None:
        self.writer.close()

    def __enter__(self) -> "CanonicalTimelineJoiner":
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()
