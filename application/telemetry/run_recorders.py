"""Canonical workload-phase and request-result telemetry recorders."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from telemetry.contracts import TelemetryValidationError
from telemetry.writers import (
    AppendOnlyTelemetryWriter,
    TelemetryWriterError,
)


class RunRecorderError(RuntimeError):
    """Raised when canonical run telemetry cannot be recorded."""


def _format_utc(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise RunRecorderError(
            "recorder clock must return datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise RunRecorderError(
            "recorder clock must return UTC datetime"
        )

    return value.isoformat().replace("+00:00", "Z")


class _RunRecorder:
    stream_id: str
    writer_name: str

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        writer_instance_id: str,
        source_version: str,
        dataset_class: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        for field_name, value in (
            ("source_version", source_version),
            ("dataset_class", dataset_class),
        ):
            if not isinstance(value, str) or not value.strip():
                raise RunRecorderError(
                    f"{field_name} is empty"
                )

        self.run_id = run_id
        self.writer_instance_id = writer_instance_id
        self.source_version = source_version
        self.dataset_class = dataset_class
        self._clock = clock or (lambda: datetime.now(UTC))

        self._writer = AppendOnlyTelemetryWriter(
            root,
            stream_id=self.stream_id,
            run_id=run_id,
            writer_name=self.writer_name,
            writer_instance_id=writer_instance_id,
        )

    @property
    def data_path(self) -> Path:
        return self._writer.data_path

    @property
    def ownership_path(self) -> Path:
        return self._writer.ownership_path

    def _append(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise RunRecorderError(
                "telemetry payload must be a mapping"
            )

        record = dict(payload)
        record["run_id"] = self.run_id
        record["writer_instance_id"] = (
            self.writer_instance_id
        )
        record["source_version"] = self.source_version
        record["dataset_class"] = self.dataset_class
        record["written_at_utc"] = _format_utc(
            self._clock()
        )

        try:
            return self._writer.append(record)
        except (
            TelemetryValidationError,
            TelemetryWriterError,
        ) as exc:
            raise RunRecorderError(
                f"{self.stream_id} record rejected: {exc}"
            ) from exc

    def close(self) -> None:
        self._writer.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        self.close()


class WorkloadPhaseRecorder(_RunRecorder):
    """Authorized append-only writer for workload_phase."""

    stream_id = "workload_phase"
    writer_name = "workload_generator"

    def record_phase(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._append(payload)


class RequestResultRecorder(_RunRecorder):
    """Authorized append-only writer for request_results."""

    stream_id = "request_results"
    writer_name = "qualification_client"

    def record_result(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._append(payload)
