from __future__ import annotations

import argparse
import csv
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Mapping, Sequence

from telemetry.run_recorders import RequestResultRecorder, WorkloadPhaseRecorder
from tools.qualification_client import (
    build_request_result_payload,
    send_one,
)


MAX_SCHEDULED_REQUESTS = 1_000_000


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_scheduled_offsets(
    rate_rps: float,
    duration_seconds: float,
) -> tuple[float, ...]:
    if not math.isfinite(rate_rps) or rate_rps <= 0:
        raise ValueError("rate-rps must be finite and positive")
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration-seconds must be finite and positive")

    rate = Decimal(str(rate_rps))
    duration = Decimal(str(duration_seconds))
    count = int((rate * duration).to_integral_value(rounding=ROUND_CEILING))

    if count < 1 or count > MAX_SCHEDULED_REQUESTS:
        raise ValueError(
            "scheduled request count must be between 1 and "
            f"{MAX_SCHEDULED_REQUESTS}"
        )

    offsets = tuple(
        float(Decimal(index) / rate)
        for index in range(count)
        if Decimal(index) / rate < duration
    )

    if len(offsets) != count:
        raise ValueError("schedule construction violated the phase interval")

    return offsets


def build_calibration_request_payload(
    row: Mapping[str, Any],
    *,
    phase_id: str,
    episode_id: str,
) -> dict[str, Any]:
    payload = build_request_result_payload(
        row,
        phase_id=phase_id,
        episode_id=episode_id,
    )
    payload["scheduled_time_utc"] = str(row["scheduled_time_utc"])
    return payload


def send_scheduled(
    url: str,
    run_id: str,
    work_units: int,
    timeout: float,
    sequence: int,
    *,
    scheduled_time_utc: str,
    scheduled_offset_seconds: float,
    target_clock: float,
) -> dict[str, object]:
    dispatch_clock = perf_counter()
    row = send_one(
        url,
        run_id,
        work_units,
        timeout,
        sequence,
    )
    row["scheduled_time_utc"] = scheduled_time_utc
    row["scheduled_offset_seconds"] = round(
        scheduled_offset_seconds,
        9,
    )
    row["schedule_lateness_ms"] = round(
        max(0.0, dispatch_clock - target_clock) * 1000,
        3,
    )
    return row


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Open-loop controlled-rate calibration client for "
            "the NET 470 V3 application"
        )
    )
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--rate-rps", type=float, required=True)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--work-units", type=int, default=25_000)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--telemetry-root", type=Path, required=True)
    parser.add_argument("--phase-id", default="baseline-low-probe")
    parser.add_argument("--phase-sequence", type=int, default=1)
    parser.add_argument("--episode-id", default="baseline-episode-001")
    parser.add_argument("--dataset-class", default="V3-K")
    parser.add_argument("--source-version", default="calibration-client-v1.0")
    parser.add_argument(
        "--workload-writer-instance-id",
        default="calibration-workload-writer-01",
    )
    parser.add_argument(
        "--result-writer-instance-id",
        default="calibration-result-writer-01",
    )
    args = parser.parse_args(argv)

    if not args.run_id.startswith("V3-K-"):
        parser.error("calibration client run IDs must start with V3-K-")

    try:
        request_count = len(
            build_scheduled_offsets(args.rate_rps, args.duration_seconds)
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.concurrency < 1 or args.concurrency > request_count:
        parser.error(
            "concurrency must be between 1 and the scheduled request count"
        )
    if args.work_units < 1 or args.work_units > 5_000_000:
        parser.error("work-units must be between 1 and 5000000")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error("timeout-seconds must be finite and positive")
    if args.phase_sequence < 1:
        parser.error("phase-sequence must be positive")

    required_text = (
        ("phase-id", args.phase_id),
        ("episode-id", args.episode_id),
        ("dataset-class", args.dataset_class),
        ("source-version", args.source_version),
        (
            "workload-writer-instance-id",
            args.workload_writer_instance_id,
        ),
        ("result-writer-instance-id", args.result_writer_instance_id),
    )
    for name, value in required_text:
        if not isinstance(value, str) or not value.strip():
            parser.error(f"{name} must not be empty")

    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    offsets = build_scheduled_offsets(args.rate_rps, args.duration_seconds)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    phase_wall_start = datetime.now(timezone.utc)
    phase_clock_start = perf_counter()
    phase_start_utc = format_utc(phase_wall_start)
    telemetry_paths: dict[str, str] = {}

    with WorkloadPhaseRecorder(
        args.telemetry_root,
        run_id=args.run_id,
        writer_instance_id=args.workload_writer_instance_id,
        source_version=args.source_version,
        dataset_class=args.dataset_class,
    ) as phase_recorder:
        phase_recorder.record_phase(
            {
                "phase_id": args.phase_id,
                "sequence": args.phase_sequence,
                "scheduled_start_utc": phase_start_utc,
                "actual_start_utc": phase_start_utc,
                "target_requests": len(offsets),
                "target_concurrency": args.concurrency,
                "target_rate_rps": args.rate_rps,
                "duration_seconds": args.duration_seconds,
                "work_units": args.work_units,
            }
        )
        telemetry_paths["workload_phase"] = str(phase_recorder.data_path)

    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = []
        for sequence, offset in enumerate(offsets, start=1):
            target_clock = phase_clock_start + offset
            while True:
                remaining = target_clock - perf_counter()
                if remaining <= 0:
                    break
                sleep(remaining)

            scheduled_time_utc = format_utc(
                phase_wall_start + timedelta(seconds=offset)
            )
            futures.append(
                executor.submit(
                    send_scheduled,
                    args.url,
                    args.run_id,
                    args.work_units,
                    args.timeout_seconds,
                    sequence,
                    scheduled_time_utc=scheduled_time_utc,
                    scheduled_offset_seconds=offset,
                    target_clock=target_clock,
                )
            )

        for future in as_completed(futures):
            rows.append(future.result())

    rows.sort(key=lambda row: int(row["sequence"]))
    fieldnames = list(rows[0].keys())
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with RequestResultRecorder(
        args.telemetry_root,
        run_id=args.run_id,
        writer_instance_id=args.result_writer_instance_id,
        source_version=args.source_version,
        dataset_class=args.dataset_class,
    ) as result_recorder:
        for row in rows:
            result_recorder.record_result(
                build_calibration_request_payload(
                    row,
                    phase_id=args.phase_id,
                    episode_id=args.episode_id,
                )
            )
        telemetry_paths["request_results"] = str(result_recorder.data_path)

    completed = sum(row["outcome"] == "completed" for row in rows)
    scheduled = len(rows)
    failed = scheduled - completed
    delivery = completed / scheduled
    achieved_rps = completed / args.duration_seconds
    summary: dict[str, object] = {
        "scheduled": scheduled,
        "completed": completed,
        "failed": failed,
        "delivery": round(delivery, 9),
        "requested_rate_rps": args.rate_rps,
        "achieved_rate_rps": round(achieved_rps, 9),
        "duration_seconds": args.duration_seconds,
        "output": str(args.out),
        "telemetry": telemetry_paths,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
