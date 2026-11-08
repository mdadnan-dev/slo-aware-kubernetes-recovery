"""Collector for the frozen application_events telemetry stream."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from telemetry.contracts import TelemetryValidationError
from telemetry.writers import (
    AppendOnlyTelemetryWriter,
    TelemetryWriterError,
)


APPLICATION_EVENT_SERVICES = {
    "ingestion_received": "ingestion",
    "processing_call_failed": "ingestion",
    "ingestion_response": "ingestion",
    "results_call_failed": "processing",
    "processing_completed": "processing",
    "result_stored": "results",
}


class ApplicationLogCollectorError(RuntimeError):
    """Raised when an application log cannot be collected safely."""


def _format_utc(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise ApplicationLogCollectorError(
            "collector clock must return datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ApplicationLogCollectorError(
            "collector clock must return UTC datetime"
        )

    return value.isoformat().replace("+00:00", "Z")


class ApplicationLogCollector:
    """Convert structured service logs into application-event records."""

    stream_id = "application_events"
    writer_name = "application_log_collector"

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        writer_instance_id: str,
        source_version: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(source_version, str)
            or not source_version.strip()
        ):
            raise ApplicationLogCollectorError(
                "source_version is empty"
            )

        self.run_id = run_id
        self.writer_instance_id = writer_instance_id
        self.source_version = source_version
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

    def collect_record(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Collect one recognized event or ignore an unrelated log."""

        if not isinstance(payload, Mapping):
            raise ApplicationLogCollectorError(
                "log payload must be a mapping"
            )

        event = payload.get("event")

        if event not in APPLICATION_EVENT_SERVICES:
            return None

        expected_service = APPLICATION_EVENT_SERVICES[event]
        actual_service = payload.get("service")

        if actual_service != expected_service:
            raise ApplicationLogCollectorError(
                f"event {event} must be emitted by "
                f"{expected_service}"
            )

        record = dict(payload)
        record["collected_at_utc"] = _format_utc(
            self._clock()
        )
        record["writer_instance_id"] = (
            self.writer_instance_id
        )
        record["source_version"] = self.source_version

        try:
            return self._writer.append(record)
        except (
            TelemetryValidationError,
            TelemetryWriterError,
        ) as exc:
            raise ApplicationLogCollectorError(
                f"application event rejected: {exc}"
            ) from exc

    def collect_line(
        self,
        line: str,
    ) -> dict[str, Any] | None:
        """Parse and collect one JSON log line."""

        if not isinstance(line, str) or not line.strip():
            raise ApplicationLogCollectorError(
                "log line is empty or is not text"
            )

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApplicationLogCollectorError(
                "log line is not valid JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise ApplicationLogCollectorError(
                "JSON log payload must be an object"
            )

        return self.collect_record(payload)

    def close(self) -> None:
        self._writer.close()

    def __enter__(self) -> "ApplicationLogCollector":
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        self.close()
