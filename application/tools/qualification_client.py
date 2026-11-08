from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4

import httpx

from telemetry.run_recorders import (
    RequestResultRecorder,
    WorkloadPhaseRecorder,
)


def utc_text() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def send_one(
    url: str,
    run_id: str,
    work_units: int,
    timeout: float,
    sequence: int,
) -> dict[str, object]:
    request_id = str(uuid4())
    started_at = utc_text()
    start_clock = perf_counter()

    row: dict[str, object] = {
        "sequence": sequence,
        "run_id": run_id,
        "request_id": request_id,
        "client_started_at_utc": started_at,
        "client_completed_at_utc": "",
        "client_elapsed_ms": "",
        "http_status": "",
        "outcome": "error",
        "error": "",
        "response_json": "",
    }

    try:
        with httpx.Client(
            timeout=timeout,
            trust_env=False,
        ) as client:
            response = client.post(
                f"{url.rstrip('/')}/v1/jobs",
                json={
                    "run_id": run_id,
                    "request_id": request_id,
                    "submitted_at_utc": started_at,
                    "work_units": work_units,
                    "payload": "qualification",
                },
            )

        row["http_status"] = response.status_code
        response.raise_for_status()
        body = response.json()

        if (
            str(body.get("request_id")) != request_id
            or body.get("run_id") != run_id
        ):
            raise ValueError(
                "response identity does not match the request"
            )

        row["outcome"] = "completed"
        row["response_json"] = json.dumps(
            body,
            separators=(",", ":"),
            sort_keys=True,
        )
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        row["client_completed_at_utc"] = utc_text()
        row["client_elapsed_ms"] = round(
            (perf_counter() - start_clock) * 1000,
            3,
        )

    return row


def build_request_result_payload(
    row: Mapping[str, Any],
    *,
    phase_id: str,
    episode_id: str,
) -> dict[str, Any]:
    outcome = str(row.get("outcome", ""))
    failure_reason = str(row.get("error", "") or "")
    processing_pod_uid = ""
    processing_received_at_utc = ""

    if outcome == "completed":
        try:
            body = json.loads(
                str(row["response_json"])
            )
            processing_received_at_utc = str(
                body["timestamps"][
                    "processing_received_at_utc"
                ]
            )
            processing_pod_uid = str(
                body["identities"]["processing"]["pod_uid"]
            )
        except (
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "completed response lacks processing telemetry"
            ) from exc

        if (
            not processing_received_at_utc
            or not processing_pod_uid
        ):
            raise ValueError(
                "completed response has empty processing telemetry"
            )
    elif not failure_reason:
        failure_reason = (
            f"request did not complete: outcome={outcome}"
        )

    return {
        "request_id": str(row["request_id"]),
        "phase_id": phase_id,
        "episode_id": episode_id,
        "processing_pod_uid": processing_pod_uid,
        "scheduled_time_utc": str(
            row["client_started_at_utc"]
        ),
        "client_started_at_utc": str(
            row["client_started_at_utc"]
        ),
        "client_completed_at_utc": str(
            row["client_completed_at_utc"]
        ),
        "processing_received_at_utc": (
            processing_received_at_utc
        ),
        "sequence": int(row["sequence"]),
        "outcome": outcome,
        "http_status": row.get("http_status", ""),
        "client_elapsed_ms": row.get(
            "client_elapsed_ms",
            "",
        ),
        "failure_reason": failure_reason,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Qualification-only client for the "
            "NET 470 V3 application"
        )
    )

    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8001",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--work-units",
        type=int,
        default=25_000,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--telemetry-root",
        type=Path,
    )
    parser.add_argument(
        "--phase-id",
        default="qualification",
    )
    parser.add_argument(
        "--phase-sequence",
        type=int,
        default=1,
    )
    parser.add_argument(
        "--episode-id",
        default="qualification-episode-001",
    )
    parser.add_argument(
        "--dataset-class",
        default="qualification",
    )
    parser.add_argument(
        "--source-version",
        default="qualification-client-v3.1",
    )
    parser.add_argument(
        "--workload-writer-instance-id",
        default="qualification-workload-writer-01",
    )
    parser.add_argument(
        "--result-writer-instance-id",
        default="qualification-result-writer-01",
    )

    args = parser.parse_args()

    if not args.run_id.startswith("V3-Q-"):
        parser.error(
            "qualification client run IDs must start with V3-Q-"
        )

    if (
        args.requests < 1
        or args.concurrency < 1
        or args.concurrency > args.requests
    ):
        parser.error(
            "requests must be positive and concurrency "
            "must be between 1 and requests"
        )

    if (
        args.work_units < 1
        or args.work_units > 5_000_000
    ):
        parser.error(
            "work-units must be between 1 and 5000000"
        )

    if args.phase_sequence < 1:
        parser.error(
            "phase-sequence must be positive"
        )

    if args.telemetry_root is not None:
        required_text = (
            ("phase-id", args.phase_id),
            ("episode-id", args.episode_id),
            ("dataset-class", args.dataset_class),
            ("source-version", args.source_version),
            (
                "workload-writer-instance-id",
                args.workload_writer_instance_id,
            ),
            (
                "result-writer-instance-id",
                args.result_writer_instance_id,
            ),
        )

        for name, value in required_text:
            if not isinstance(value, str) or not value.strip():
                parser.error(
                    f"{name} must not be empty "
                    "when telemetry is enabled"
                )

    return args


def main() -> int:
    args = parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    phase_scheduled_start = utc_text()
    phase_actual_start = utc_text()
    telemetry_paths: dict[str, str] = {}

    if args.telemetry_root is not None:
        with WorkloadPhaseRecorder(
            args.telemetry_root,
            run_id=args.run_id,
            writer_instance_id=(
                args.workload_writer_instance_id
            ),
            source_version=args.source_version,
            dataset_class=args.dataset_class,
        ) as phase_recorder:
            phase_recorder.record_phase(
                {
                    "phase_id": args.phase_id,
                    "sequence": args.phase_sequence,
                    "scheduled_start_utc": (
                        phase_scheduled_start
                    ),
                    "actual_start_utc": phase_actual_start,
                    "target_requests": args.requests,
                    "target_concurrency": args.concurrency,
                    "work_units": args.work_units,
                }
            )
            telemetry_paths["workload_phase"] = str(
                phase_recorder.data_path
            )

    rows: list[dict[str, object]] = []

    with ThreadPoolExecutor(
        max_workers=args.concurrency
    ) as executor:
        futures = [
            executor.submit(
                send_one,
                args.url,
                args.run_id,
                args.work_units,
                args.timeout_seconds,
                sequence,
            )
            for sequence in range(
                1,
                args.requests + 1,
            )
        ]

        for future in as_completed(futures):
            rows.append(future.result())

    rows.sort(key=lambda row: int(row["sequence"]))
    fieldnames = list(rows[0].keys())

    with args.out.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)

    if args.telemetry_root is not None:
        with RequestResultRecorder(
            args.telemetry_root,
            run_id=args.run_id,
            writer_instance_id=(
                args.result_writer_instance_id
            ),
            source_version=args.source_version,
            dataset_class=args.dataset_class,
        ) as result_recorder:
            for row in rows:
                result_recorder.record_result(
                    build_request_result_payload(
                        row,
                        phase_id=args.phase_id,
                        episode_id=args.episode_id,
                    )
                )

            telemetry_paths["request_results"] = str(
                result_recorder.data_path
            )

    completed = sum(
        row["outcome"] == "completed"
        for row in rows
    )

    summary: dict[str, object] = {
        "scheduled": len(rows),
        "completed": completed,
        "failed": len(rows) - completed,
        "output": str(args.out),
    }

    if telemetry_paths:
        summary["telemetry"] = telemetry_paths

    print(json.dumps(summary))

    return 0 if completed == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
