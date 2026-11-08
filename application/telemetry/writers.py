"""Append-only writers for the frozen NET 470 telemetry streams."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Mapping

from telemetry.contracts import (
    RUN_ID_PATTERN,
    STREAM_CONTRACTS,
    validate_telemetry_record,
)


STREAM_FORMATS = {
    "workload_phase": "JSONL",
    "request_results": "CSV",
    "resource_metrics": "CSV",
    "hpa_state": "JSONL",
    "pod_lifecycle": "JSONL",
    "application_events": "JSONL",
    "slo_windows": "CSV",
    "canonical_timeline": "CSV",
}

if set(STREAM_FORMATS) != set(STREAM_CONTRACTS):
    raise RuntimeError(
        "stream-format registry differs from frozen contracts"
    )


class TelemetryWriterError(RuntimeError):
    """Base error for telemetry writer failures."""


class WriterOwnershipError(TelemetryWriterError):
    """Raised when another writer owns a stream."""


class AppendOnlyTelemetryWriter:
    """Validate and append records for one stream and run."""

    def __init__(
        self,
        root: str | Path,
        *,
        stream_id: str,
        run_id: str,
        writer_name: str,
        writer_instance_id: str,
    ) -> None:
        contract = STREAM_CONTRACTS.get(stream_id)

        if contract is None:
            raise TelemetryWriterError(
                f"unknown stream: {stream_id}"
            )

        if (
            not isinstance(run_id, str)
            or not RUN_ID_PATTERN.fullmatch(run_id)
        ):
            raise TelemetryWriterError(
                "run_id is invalid"
            )

        if writer_name != contract.authorized_writer:
            raise TelemetryWriterError(
                f"unauthorized writer for {stream_id}: "
                f"{writer_name}"
            )

        if (
            not isinstance(writer_instance_id, str)
            or not writer_instance_id.strip()
        ):
            raise TelemetryWriterError(
                "writer_instance_id is empty"
            )

        self.contract = contract
        self.stream_id = stream_id
        self.run_id = run_id
        self.writer_name = writer_name
        self.writer_instance_id = writer_instance_id
        self.record_format = STREAM_FORMATS[stream_id]
        self._closed = False

        self.run_directory = Path(root) / run_id
        self.run_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        suffix = (
            ".jsonl"
            if self.record_format == "JSONL"
            else ".csv"
        )

        self.data_path = (
            self.run_directory /
            f"{stream_id}{suffix}"
        )

        self.ownership_path = (
            self.run_directory /
            f"{stream_id}.writer.json"
        )

        self._claim_ownership()

    def _claim_ownership(self) -> None:
        ownership = {
            "run_id": self.run_id,
            "stream_id": self.stream_id,
            "writer_name": self.writer_name,
            "writer_instance_id": self.writer_instance_id,
        }

        try:
            with self.ownership_path.open(
                "x",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                json.dump(
                    ownership,
                    handle,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            try:
                existing = json.loads(
                    self.ownership_path.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ) as exc:
                raise WriterOwnershipError(
                    "writer ownership record is unreadable"
                ) from exc

            if existing != ownership:
                raise WriterOwnershipError(
                    "stream already belongs to another "
                    f"writer instance: {self.stream_id}"
                )

    def append(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._closed:
            raise TelemetryWriterError(
                "writer is closed"
            )

        if not isinstance(record, Mapping):
            raise TelemetryWriterError(
                "record must be a mapping"
            )

        if (
            record.get("writer_instance_id")
            != self.writer_instance_id
        ):
            raise TelemetryWriterError(
                "record writer_instance_id does not "
                "match owner"
            )

        normalized = validate_telemetry_record(
            self.stream_id,
            record,
            declared_run_id=self.run_id,
            writer_name=self.writer_name,
        )

        if self.record_format == "JSONL":
            self._append_jsonl(normalized)
        elif self.record_format == "CSV":
            self._append_csv(normalized)
        else:
            raise TelemetryWriterError(
                f"unsupported format: {self.record_format}"
            )

        return normalized

    def _append_jsonl(
        self,
        record: Mapping[str, Any],
    ) -> None:
        encoded = json.dumps(
            record,
            separators=(",", ":"),
            sort_keys=True,
        )

        with self.data_path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _append_csv(
        self,
        record: Mapping[str, Any],
    ) -> None:
        write_header = (
            not self.data_path.exists()
            or self.data_path.stat().st_size == 0
        )

        if write_header:
            required = list(
                self.contract.required_fields
            )
            extras = sorted(
                set(record) - set(required)
            )
            fieldnames = required + extras
        else:
            with self.data_path.open(
                "r",
                encoding="utf-8",
                newline="",
            ) as handle:
                fieldnames = next(
                    csv.reader(handle),
                    [],
                )

            if not fieldnames:
                raise TelemetryWriterError(
                    "existing CSV header is missing"
                )

            if set(fieldnames) != set(record):
                raise TelemetryWriterError(
                    "record fields do not match "
                    "existing CSV header"
                )

        with self.data_path.open(
            "a",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                extrasaction="raise",
            )

            if write_header:
                writer.writeheader()

            writer.writerow(record)
            handle.flush()
            os.fsync(handle.fileno())

    def close(self) -> None:
        self._closed = True

    def __enter__(
        self,
    ) -> "AppendOnlyTelemetryWriter":
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        self.close()