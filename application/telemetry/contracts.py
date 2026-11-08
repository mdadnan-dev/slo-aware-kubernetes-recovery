"""G3-05 telemetry record-admission contracts.

This module implements record-level controls from validity rules V01-V03,
the frozen required fields, and each stream's authorized writer.

Cross-record duplicate detection, joins, and canonical timeline derivation
are deliberately handled by later qualification exercises.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from re import compile as compile_regex
from typing import Any, Mapping


RUN_ID_PATTERN = compile_regex(r"^V3-[QKPR]-[A-Za-z0-9._-]+$")


class TelemetryValidationError(ValueError):
    """Raised when a record violates its frozen telemetry contract."""


@dataclass(frozen=True)
class StreamContract:
    stream_id: str
    authorized_writer: str
    primary_key: tuple[str, ...]
    join_fields: tuple[str, ...]
    time_fields: tuple[str, ...]
    provenance_fields: tuple[str, ...]
    mutability: str

    @property
    def required_fields(self) -> tuple[str, ...]:
        """Return the ordered union of all frozen required fields."""
        return tuple(
            dict.fromkeys(
                self.primary_key
                + self.join_fields
                + self.time_fields
                + self.provenance_fields
            )
        )


def _split(value: str) -> tuple[str, ...]:
    return tuple(value.split(";"))


def _contract(
    stream_id: str,
    writer: str,
    primary_key: str,
    join_fields: str,
    time_fields: str,
    provenance_fields: str,
    mutability: str,
) -> StreamContract:
    return StreamContract(
        stream_id=stream_id,
        authorized_writer=writer,
        primary_key=_split(primary_key),
        join_fields=_split(join_fields),
        time_fields=_split(time_fields),
        provenance_fields=_split(provenance_fields),
        mutability=mutability,
    )


_RAW_CONTRACTS = (
    (
        "workload_phase",
        "workload_generator",
        "run_id;phase_id;sequence",
        "run_id;phase_id",
        "scheduled_start_utc;actual_start_utc;written_at_utc",
        "writer_instance_id;source_version;dataset_class",
        "APPEND_ONLY",
    ),
    (
        "request_results",
        "qualification_client",
        "run_id;request_id",
        "run_id;phase_id;episode_id;request_id;processing_pod_uid",
        (
            "scheduled_time_utc;client_started_at_utc;"
            "client_completed_at_utc;processing_received_at_utc;"
            "written_at_utc"
        ),
        "writer_instance_id;source_version;dataset_class",
        "APPEND_ONLY",
    ),
    (
        "resource_metrics",
        "metrics_watcher",
        "run_id;sample_time_utc;deployment_uid;pod_uid",
        "run_id;episode_id;deployment_uid;pod_uid",
        "sample_time_utc;written_at_utc",
        (
            "writer_instance_id;source_version;"
            "metrics_api_resource_version"
        ),
        "APPEND_ONLY",
    ),
    (
        "hpa_state",
        "hpa_watcher",
        "run_id;hpa_uid;resource_version",
        (
            "run_id;episode_id;hpa_uid;"
            "deployment_uid;resource_version"
        ),
        "observed_at_utc",
        "writer_instance_id;source_version;resource_version",
        "APPEND_ONLY",
    ),
    (
        "pod_lifecycle",
        "pod_watcher",
        "run_id;pod_uid;event_type;event_time_utc",
        (
            "run_id;episode_id;pod_uid;"
            "replicaset_uid;deployment_uid"
        ),
        "event_time_utc;observed_at_utc",
        "writer_instance_id;source_version;resource_version",
        "APPEND_ONLY",
    ),
    (
        "application_events",
        "application_log_collector",
        "run_id;request_id;service;event;pod_uid",
        "run_id;request_id;pod_uid;service",
        "timestamp_utc;collected_at_utc",
        "writer_instance_id;source_version;image_version",
        "APPEND_ONLY",
    ),
    (
        "slo_windows",
        "window_analyzer",
        "run_id;window_sequence",
        "run_id;episode_id;window_sequence",
        "window_start_utc;window_end_utc",
        "writer_instance_id;analyzer_version;input_hashes",
        "DERIVED_REPRODUCIBLE",
    ),
    (
        "canonical_timeline",
        "timeline_joiner",
        "run_id;episode_id;event_id;pod_uid",
        "run_id;episode_id;event_id;pod_uid;request_id",
        "event_time_utc;observed_at_utc",
        (
            "writer_instance_id;joiner_version;"
            "input_hashes;source_record_key"
        ),
        "DERIVED_REPRODUCIBLE",
    ),
)


STREAM_CONTRACTS: dict[str, StreamContract] = {
    contract.stream_id: contract
    for contract in (
        _contract(*contract_values)
        for contract_values in _RAW_CONTRACTS
    )
}


def _require_value(
    record: Mapping[str, Any],
    field: str,
    *,
    allow_empty: bool = False,
) -> Any:
    if field not in record:
        raise TelemetryValidationError(
            f"missing required field: {field}"
        )

    value = record[field]

    if not allow_empty and (
        value is None
        or (isinstance(value, str) and not value.strip())
    ):
        raise TelemetryValidationError(
            f"empty required field: {field}"
        )

    return value


def _normalize_utc(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()

        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"

            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TelemetryValidationError(
                f"invalid timestamp in {field}"
            ) from exc
    else:
        raise TelemetryValidationError(
            f"invalid timestamp type in {field}"
        )

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TelemetryValidationError(
            f"timestamp lacks UTC offset: {field}"
        )

    if parsed.utcoffset() != timedelta(0):
        raise TelemetryValidationError(
            f"timestamp is not UTC: {field}"
        )

    return parsed.isoformat().replace("+00:00", "Z")


def validate_telemetry_record(
    stream_id: str,
    record: Mapping[str, Any],
    *,
    declared_run_id: str,
    writer_name: str,
) -> dict[str, Any]:
    """Validate and normalize one record without mutating its input."""

    contract = STREAM_CONTRACTS.get(stream_id)

    if contract is None:
        raise TelemetryValidationError(
            f"unknown stream: {stream_id}"
        )

    if not isinstance(record, Mapping):
        raise TelemetryValidationError(
            "record must be a mapping"
        )

    if (
        not isinstance(declared_run_id, str)
        or not RUN_ID_PATTERN.fullmatch(declared_run_id)
    ):
        raise TelemetryValidationError(
            "declared_run_id is invalid"
        )

    if writer_name != contract.authorized_writer:
        raise TelemetryValidationError(
            f"unauthorized writer for {stream_id}: {writer_name}"
        )

    nullable_failure_fields = (
        {
            "processing_pod_uid",
            "processing_received_at_utc",
        }
        if stream_id == "request_results"
        else set()
    )

    failure_reason = record.get("failure_reason")

    if stream_id == "canonical_timeline":
        failure_reason = record.get("missing_reason")

    has_failure_reason = bool(
        isinstance(failure_reason, str)
        and failure_reason.strip()
    )

    normalized = dict(record)

    for field in contract.required_fields:
        allow_empty = (
            field in nullable_failure_fields
            and has_failure_reason
        )

        _require_value(
            record,
            field,
            allow_empty=allow_empty,
        )

    if record["run_id"] != declared_run_id:
        raise TelemetryValidationError(
            "record run_id does not match declared run_id"
        )

    for field in contract.time_fields:
        if (
            field in nullable_failure_fields
            and record[field] in (None, "")
        ):
            continue

        if (
            stream_id == "canonical_timeline"
            and record[field] == "NA"
            and has_failure_reason
        ):
            continue

        normalized[field] = _normalize_utc(
            record[field],
            field,
        )

    return normalized