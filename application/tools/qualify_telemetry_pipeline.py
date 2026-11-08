"""Offline qualification of the complete NET 470 telemetry pipeline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

if __package__ in (None, ""):
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parents[1]),
    )

from telemetry.canonical_timeline_joiner import (
    CanonicalTimelineJoiner,
)
from telemetry.slo_window_analyzer import (
    SloWindowAnalyzer,
    SloWindowPolicy,
)
from telemetry.writers import AppendOnlyTelemetryWriter


BASE_TIME = datetime(
    2026,
    1,
    1,
    12,
    0,
    0,
    tzinfo=timezone.utc,
)

RAW_STREAM_WRITERS = {
    "workload_phase": "workload_generator",
    "request_results": "qualification_client",
    "resource_metrics": "metrics_watcher",
    "hpa_state": "hpa_watcher",
    "pod_lifecycle": "pod_watcher",
    "application_events": "application_log_collector",
}

STREAM_FORMATS = {
    "workload_phase": "jsonl",
    "request_results": "csv",
    "resource_metrics": "csv",
    "hpa_state": "jsonl",
    "pod_lifecycle": "jsonl",
    "application_events": "jsonl",
    "slo_windows": "csv",
    "canonical_timeline": "csv",
}


class PipelineQualificationError(RuntimeError):
    """Raised when the synthetic pipeline is not reproducible."""


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )


def at(seconds: int) -> str:
    return utc_text(BASE_TIME + timedelta(seconds=seconds))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest().upper()


def count_records(path: Path) -> int:
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            return sum(
                1
                for line in handle
                if line.strip()
            )

    if path.suffix == ".csv":
        with path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as handle:
            return sum(1 for _ in csv.DictReader(handle))

    raise PipelineQualificationError(
        f"unsupported telemetry file: {path}"
    )


def write_stream(
    root: Path,
    *,
    stream_id: str,
    run_id: str,
    writer_instance_id: str,
    records: Iterable[Mapping[str, Any]],
) -> Path:
    writer_name = RAW_STREAM_WRITERS[stream_id]

    with AppendOnlyTelemetryWriter(
        root,
        stream_id=stream_id,
        run_id=run_id,
        writer_name=writer_name,
        writer_instance_id=writer_instance_id,
    ) as writer:
        for record in records:
            writer.append(record)

        return writer.data_path


def qualify_pipeline(
    output_root: str | Path,
    *,
    run_id: str,
    episode_id: str = "g3-13-synthetic-episode",
) -> dict[str, Any]:
    root = Path(output_root)
    run_directory = root / run_id

    if run_directory.exists():
        raise PipelineQualificationError(
            f"run directory already exists: {run_directory}"
        )

    request_id = "00000000-0000-0000-0000-000000000013"
    deployment_uid = "synthetic-deployment-uid"
    replicaset_uid = "synthetic-replicaset-uid"
    pod_uid = "synthetic-processing-pod-uid"
    hpa_uid = "synthetic-hpa-uid"

    data_paths: dict[str, Path] = {}

    data_paths["workload_phase"] = write_stream(
        root,
        stream_id="workload_phase",
        run_id=run_id,
        writer_instance_id="workload-generator-g3-13",
        records=[
            {
                "run_id": run_id,
                "phase_id": "synthetic-high-phase",
                "sequence": 1,
                "scheduled_start_utc": at(0),
                "actual_start_utc": at(0),
                "written_at_utc": at(1),
                "writer_instance_id": (
                    "workload-generator-g3-13"
                ),
                "source_version": "g3-13-v1",
                "dataset_class": "SYNTHETIC_QUALIFICATION",
            }
        ],
    )

    data_paths["request_results"] = write_stream(
        root,
        stream_id="request_results",
        run_id=run_id,
        writer_instance_id="qualification-client-g3-13",
        records=[
            {
                "run_id": run_id,
                "request_id": request_id,
                "phase_id": "synthetic-high-phase",
                "episode_id": episode_id,
                "processing_pod_uid": pod_uid,
                "scheduled_time_utc": at(31),
                "client_started_at_utc": at(31),
                "client_completed_at_utc": at(35),
                "processing_received_at_utc": at(32),
                "written_at_utc": at(36),
                "writer_instance_id": (
                    "qualification-client-g3-13"
                ),
                "source_version": "g3-13-v1",
                "dataset_class": "SYNTHETIC_QUALIFICATION",
                "outcome": "completed",
                "failure_reason": "",
            }
        ],
    )

    data_paths["resource_metrics"] = write_stream(
        root,
        stream_id="resource_metrics",
        run_id=run_id,
        writer_instance_id="metrics-watcher-g3-13",
        records=[
            {
                "run_id": run_id,
                "episode_id": episode_id,
                "deployment_uid": deployment_uid,
                "pod_uid": pod_uid,
                "sample_time_utc": at(5),
                "written_at_utc": at(6),
                "writer_instance_id": "metrics-watcher-g3-13",
                "source_version": "g3-13-v1",
                "metrics_api_resource_version": (
                    "SYNTHETIC-RV-1"
                ),
                "cpu_usage_nanocores": 420000000,
                "cpu_request_nanocores": 350000000,
                "dataset_class": "SYNTHETIC_QUALIFICATION",
            }
        ],
    )

    data_paths["hpa_state"] = write_stream(
        root,
        stream_id="hpa_state",
        run_id=run_id,
        writer_instance_id="hpa-watcher-g3-13",
        records=[
            {
                "run_id": run_id,
                "episode_id": episode_id,
                "hpa_uid": hpa_uid,
                "deployment_uid": deployment_uid,
                "resource_version": "SYNTHETIC-RV-2",
                "observed_at_utc": at(15),
                "writer_instance_id": "hpa-watcher-g3-13",
                "source_version": "g3-13-v1",
                "current_replicas": 1,
                "desired_replicas": 2,
                "scale_up_requested": True,
                "dataset_class": "SYNTHETIC_QUALIFICATION",
            }
        ],
    )

    lifecycle_records = []

    for event_type, event_seconds, resource_version in (
        ("T3", 16, "SYNTHETIC-RV-3"),
        ("T4", 17, "SYNTHETIC-RV-4"),
        ("T5", 18, "SYNTHETIC-RV-5"),
        ("T6", 19, "SYNTHETIC-RV-6"),
    ):
        lifecycle_records.append(
            {
                "run_id": run_id,
                "episode_id": episode_id,
                "pod_uid": pod_uid,
                "replicaset_uid": replicaset_uid,
                "deployment_uid": deployment_uid,
                "event_type": event_type,
                "event_time_utc": at(event_seconds),
                "observed_at_utc": at(event_seconds + 1),
                "writer_instance_id": "pod-watcher-g3-13",
                "source_version": "g3-13-v1",
                "resource_version": resource_version,
                "dataset_class": "SYNTHETIC_QUALIFICATION",
            }
        )

    data_paths["pod_lifecycle"] = write_stream(
        root,
        stream_id="pod_lifecycle",
        run_id=run_id,
        writer_instance_id="pod-watcher-g3-13",
        records=lifecycle_records,
    )

    data_paths["application_events"] = write_stream(
        root,
        stream_id="application_events",
        run_id=run_id,
        writer_instance_id="application-log-collector-g3-13",
        records=[
            {
                "run_id": run_id,
                "request_id": request_id,
                "pod_uid": pod_uid,
                "service": "processing",
                "event": "processing_received",
                "timestamp_utc": at(32),
                "collected_at_utc": at(33),
                "writer_instance_id": (
                    "application-log-collector-g3-13"
                ),
                "source_version": "g3-13-v1",
                "image_version": "synthetic-image",
                "dataset_class": "SYNTHETIC_QUALIFICATION",
            }
        ],
    )

    raw_hashes = {
        stream_id: sha256_file(path)
        for stream_id, path in sorted(data_paths.items())
    }

    raw_input_hashes = ";".join(
        f"{stream_id}={digest}"
        for stream_id, digest in raw_hashes.items()
    )

    policy = SloWindowPolicy(
        policy_id="G3-13-SYNTHETIC-ONLY",
        latency_threshold_ms=100.0,
        minimum_success_ratio=1.0,
        minimum_request_count=1,
    )

    with SloWindowAnalyzer(
        root,
        run_id=run_id,
        episode_id=episode_id,
        writer_instance_id="window-analyzer-g3-13",
        analyzer_version="g3-13-v1",
        input_hashes=raw_input_hashes,
        policy=policy,
        recovery_eligible_after_utc=at(32),
    ) as analyzer:
        for sequence, start_seconds, latency in (
            (1, 0, 150.0),
            (2, 10, 150.0),
            (3, 20, 150.0),
            (4, 30, 50.0),
            (5, 40, 50.0),
            (6, 50, 50.0),
        ):
            analyzer.record_window(
                window_sequence=sequence,
                window_start_utc=at(start_seconds),
                window_end_utc=at(start_seconds + 10),
                complete=True,
                request_count=1,
                successful_request_count=1,
                p95_latency_ms=latency,
            )

        slo_summary = analyzer.summary()
        data_paths["slo_windows"] = analyzer.writer.data_path

    if slo_summary["outcome"] != "RECOVERED":
        raise PipelineQualificationError(
            "synthetic SLO timeline did not recover"
        )

    if (
        slo_summary["first_bad_window_start_utc"]
        != at(0)
    ):
        raise PipelineQualificationError(
            "synthetic T0 is incorrect"
        )

    if (
        slo_summary["breach_confirmed_at_utc"]
        != at(30)
    ):
        raise PipelineQualificationError(
            "synthetic breach confirmation is incorrect"
        )

    if (
        slo_summary["second_good_window_end_utc"]
        != at(60)
    ):
        raise PipelineQualificationError(
            "synthetic T8 is incorrect"
        )

    all_input_hashes = ";".join(
        f"{stream_id}={sha256_file(path)}"
        for stream_id, path in sorted(data_paths.items())
    )

    with CanonicalTimelineJoiner(
        root,
        run_id=run_id,
        episode_id=episode_id,
        expected_pod_uids=[pod_uid],
        writer_instance_id="timeline-joiner-g3-13",
        joiner_version="g3-13-v1",
        input_hashes=all_input_hashes,
    ) as timeline:
        timeline.record_observed(
            event_id="T0",
            event_time_utc=at(0),
            observed_at_utc=at(30),
            source_stream="slo_windows",
            source_record_key=f"{run_id}/window/1",
            breach_confirmation_at_utc=at(30),
        )
        timeline.record_observed(
            event_id="T1",
            event_time_utc=at(5),
            observed_at_utc=at(6),
            source_stream="resource_metrics",
            source_record_key=f"{run_id}/metric/5",
        )
        timeline.record_observed(
            event_id="T2",
            event_time_utc=at(15),
            observed_at_utc=at(15),
            source_stream="hpa_state",
            source_record_key=f"{run_id}/hpa/SYNTHETIC-RV-2",
        )
        timeline.record_observed(
            event_id="T3",
            event_time_utc=at(16),
            observed_at_utc=at(17),
            source_stream="pod_lifecycle",
            source_record_key=f"{pod_uid}/T3",
            pod_uid=pod_uid,
        )
        timeline.record_observed(
            event_id="T4",
            event_time_utc=at(17),
            observed_at_utc=at(18),
            source_stream="pod_lifecycle",
            source_record_key=f"{pod_uid}/T4",
            pod_uid=pod_uid,
        )
        timeline.record_observed(
            event_id="T5",
            event_time_utc=at(18),
            observed_at_utc=at(19),
            source_stream="pod_lifecycle",
            source_record_key=f"{pod_uid}/T5",
            pod_uid=pod_uid,
        )
        timeline.record_observed(
            event_id="T6",
            event_time_utc=at(19),
            observed_at_utc=at(20),
            source_stream="pod_lifecycle",
            source_record_key=f"{pod_uid}/T6",
            pod_uid=pod_uid,
        )
        timeline.record_observed(
            event_id="T7",
            event_time_utc=at(32),
            observed_at_utc=at(33),
            source_stream=(
                "application_events+request_results"
            ),
            source_record_key=f"{request_id}/{pod_uid}",
            pod_uid=pod_uid,
            request_id=request_id,
        )
        timeline.record_observed(
            event_id="T8",
            event_time_utc=at(60),
            observed_at_utc=at(60),
            source_stream="slo_windows",
            source_record_key=f"{run_id}/window/6",
        )

        timeline_summary = timeline.validate_timeline()
        data_paths["canonical_timeline"] = (
            timeline.writer.data_path
        )

    expected_counts = {
        "workload_phase": 1,
        "request_results": 1,
        "resource_metrics": 1,
        "hpa_state": 1,
        "pod_lifecycle": 4,
        "application_events": 1,
        "slo_windows": 6,
        "canonical_timeline": 9,
    }

    actual_counts = {
        stream_id: count_records(path)
        for stream_id, path in sorted(data_paths.items())
    }

    if set(actual_counts) != set(STREAM_FORMATS):
        raise PipelineQualificationError(
            "synthetic stream set is incomplete"
        )

    if actual_counts != expected_counts:
        raise PipelineQualificationError(
            "synthetic record counts are incorrect: "
            f"{actual_counts}"
        )

    manifest_rows = []

    for stream_id, path in sorted(data_paths.items()):
        manifest_rows.append(
            {
                "stream_id": stream_id,
                "relative_path": str(
                    path.relative_to(root)
                ),
                "record_count": actual_counts[stream_id],
                "sha256": sha256_file(path),
            }
        )

    manifest = {
        "run_id": run_id,
        "episode_id": episode_id,
        "dataset_class": "SYNTHETIC_QUALIFICATION",
        "synthetic_only": True,
        "live_hpa_created": False,
        "stream_count": len(data_paths),
        "streams": manifest_rows,
        "slo_summary": slo_summary,
        "timeline_summary": timeline_summary,
    }

    manifest_path = run_directory / "qualification-manifest.json"

    with manifest_path.open(
        "x",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(
            manifest,
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    return {
        "run_id": run_id,
        "episode_id": episode_id,
        "run_directory": str(run_directory),
        "manifest_path": str(manifest_path),
        "stream_count": len(data_paths),
        "record_counts": actual_counts,
        "timeline_status": timeline_summary["status"],
        "timeline_record_count": (
            timeline_summary["record_count"]
        ),
        "t0_utc": (
            slo_summary["first_bad_window_start_utc"]
        ),
        "breach_confirmed_at_utc": (
            slo_summary["breach_confirmed_at_utc"]
        ),
        "t8_utc": (
            slo_summary["second_good_window_end_utc"]
        ),
        "recovery_duration_seconds": (
            slo_summary["recovery_duration_seconds"]
        ),
        "synthetic_only": True,
        "live_hpa_created": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create an offline synthetic qualification bundle "
            "for all eight NET 470 telemetry streams."
        )
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--run-id",
        required=True,
    )
    parser.add_argument(
        "--episode-id",
        default="g3-13-synthetic-episode",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    result = qualify_pipeline(
        args.output_root,
        run_id=args.run_id,
        episode_id=args.episode_id,
    )

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
