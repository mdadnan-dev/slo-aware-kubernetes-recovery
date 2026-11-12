#!/usr/bin/env python3
"""Resumable five-level C1-C5 NET470 stress evaluation.

Runs one randomized five-treatment block per invocation by default. The frozen
Stage 9 package is copied and parameterized by workload level; source files are
never edited.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib.util
import json
import math
import os
import random
import shutil
import sys
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "NET470-FIVE-LEVEL-C1-C5-STRESS-R01"
CORE_SHA256 = "6410C56F18AB0DA8F8429CF5671B58C0B5DA7747A1528B95B98BFB98604788C5"
RUNNER_SHA256 = "C69521EE002A5E9DFDA4613012C9D54B338BB0F2F2E0422116748AFBC321E71D"
LEVELS = (5, 10, 15, 20, 25)
TREATMENTS = ("C1", "C2", "C3", "C4", "C5")
HORIZON_SECONDS = 300.0
LIVE_ACK = "RUN_NET470_FINAL_C1_C5_STRESS_BLOCK"
OLD_PHASE_LINE = 'PHASES = [(1, "low-precondition", 5, 60), (2, "high-challenge", 25, 180), (3, "low-recovery", 5, 300), (4, "post-observation", 5, 30)]'
OLD_EXPECTED_LINE = "EXPECTED = 6450"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest().upper()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_requests(rate: int) -> int:
    return 5 * 60 + rate * 180 + 5 * 300 + 5 * 30


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def elapsed_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    value = (parse_utc(end) - parse_utc(start)).total_seconds()
    return round(value, 6) if value >= 0 else None


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    selected = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=selected, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_schedule(protocol_root: Path) -> list[dict]:
    path = protocol_root / "config" / "NET470-stress-randomized-schedule.csv"
    rows = read_csv(path)
    require(len(rows) == 125, f"SCHEDULE_COUNT:{len(rows)}")
    for row in rows:
        for key in ("global_sequence", "workload_block_order", "challenge_rps", "block_within_workload", "position_within_block", "expected_requests"):
            row[key] = int(row[key])
    require({row["challenge_rps"] for row in rows} == set(LEVELS), "SCHEDULE_LEVELS")
    require({row["treatment"] for row in rows} == set(TREATMENTS), "SCHEDULE_TREATMENTS")
    return rows


def prepare_runtime_package(source: Path, runtime_root: Path, rate: int) -> Path:
    destination = runtime_root / f"rps-{rate:02d}"
    core = destination / "C5-Stage9-Episode-Core.py"
    marker = destination / "NET470-STRESS-RUNTIME.json"
    if marker.is_file() and core.is_file():
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        require(metadata.get("challenge_rps") == rate, "RUNTIME_RATE_MISMATCH")
        require(metadata.get("source_core_sha256") == CORE_SHA256, "RUNTIME_SOURCE_HASH_MISMATCH")
        return destination
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    require(core.is_file(), f"CORE_MISSING:{core}")
    require(sha256(core) == CORE_SHA256, f"CORE_HASH_MISMATCH:{sha256(core)}")
    text = core.read_text(encoding="utf-8")
    require(text.count(OLD_PHASE_LINE) == 1, "EXPECTED_ONE_PHASE_LINE")
    require(text.count(OLD_EXPECTED_LINE) == 1, "EXPECTED_ONE_EXPECTED_LINE")
    text = text.replace(
        OLD_PHASE_LINE,
        f'PHASES = [(1, "low-precondition", 5, 60), (2, "high-challenge", {rate}, 180), (3, "low-recovery", 5, 300), (4, "post-observation", 5, 30)]',
    ).replace(OLD_EXPECTED_LINE, f"EXPECTED = {expected_requests(rate)}")
    core.write_text(text, encoding="utf-8")
    marker.write_text(json.dumps({
        "schema": "NET470-STRESS-RUNTIME-PACKAGE-V1",
        "challenge_rps": rate,
        "expected_requests": expected_requests(rate),
        "source_core_sha256": CORE_SHA256,
        "source_package": str(source),
        "source_package_modified": False,
    }, indent=2) + "\n", encoding="utf-8")
    return destination


def attempt_number(path: Path) -> int:
    return int(path.name.rsplit("-A", 1)[1])


def accepted(output_root: Path) -> dict[tuple[int, int, str], dict]:
    selected: dict[tuple[int, int, str], dict] = {}
    paths = sorted(output_root.glob("rps-*/episodes/NET470-STRESS-R*-B*-*-A*/analysis/NET470-stress-episode-summary.json"))
    for path in paths:
        row = json.loads(path.read_text(encoding="utf-8"))
        if not row.get("scientifically_admissible"):
            continue
        key = (int(row["challenge_rps"]), int(row["block_within_workload"]), row["treatment"])
        if key not in selected or int(row["attempt_number"]) < int(selected[key]["attempt_number"]):
            selected[key] = row
    return selected


def resource_metrics(attempt: Path) -> dict:
    rows = read_csv(attempt / "telemetry" / "cluster-runtime.csv")
    clean = [row for row in rows if not row.get("sampler_error")]
    cpu, desired, ready = [], [], []
    replica_seconds = 0.0
    for row in clean:
        if row.get("hpa_cpu_percent") not in (None, ""):
            cpu.append(float(row["hpa_cpu_percent"]))
        if row.get("hpa_desired_replicas") not in (None, ""):
            desired.append(int(row["hpa_desired_replicas"]))
        endpoints = row.get("processing_ready_endpoints")
        if endpoints not in (None, ""):
            try:
                if str(endpoints).startswith("["):
                    ready.append(len(ast.literal_eval(str(endpoints))))
                else:
                    ready.append(int(endpoints))
            except (ValueError, SyntaxError):
                pass
    for left, right in zip(clean, clean[1:]):
        try:
            elapsed = max(0.0, min(30.0, (parse_utc(right["observed_at_utc"]) - parse_utc(left["observed_at_utc"])).total_seconds()))
            replica_seconds += int(left.get("hpa_current_replicas") or 0) * elapsed
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "mean_cpu_percent": round(sum(cpu) / len(cpu), 6) if cpu else None,
        "peak_cpu_percent": max(cpu, default=None),
        "peak_desired_replicas": max(desired, default=None),
        "peak_ready_endpoints": max(ready, default=None),
        "replica_seconds": round(replica_seconds, 6) if clean else None,
    }


def slo_metrics(attempt: Path) -> dict:
    windows = read_csv(attempt / "analysis" / "slo-windows.csv")
    violating = [row for row in windows if str(row.get("slo_good", "")).lower() not in ("true", "1", "yes")]
    return {
        "slo_windows": len(windows),
        "slo_violating_windows": len(violating),
        "slo_violating_window_percent": round(100.0 * len(violating) / len(windows), 6) if windows else None,
        "slo_violation_duration_seconds": 15 * len(violating),
    }


def throughput_metrics(attempt: Path) -> dict:
    rows = read_csv(attempt / "workload" / "request-results.csv")
    high = [row for row in rows if row.get("phase_id") == "high-challenge"]
    completed = [row for row in high if str(row.get("outcome", "")).lower() == "completed"]
    return {
        "high_challenge_requests": len(high),
        "high_challenge_completed": len(completed),
        "achieved_throughput_rps": round(len(completed) / 180.0, 6),
        "request_success_percent": round(100.0 * len(completed) / len(high), 6) if high else None,
    }


def outcome_from_events(events: dict) -> dict:
    tb, tr = events.get("TB"), events.get("TR")
    if not tb:
        return {"outcome": "NO_QUALIFYING_BREACH", "recovery_observed_within_300": False, "right_censored": False, "recovery_seconds": None, "raw_recovery_seconds": None, "censor_reason": None}
    raw = elapsed_seconds(tb, tr) if tr else None
    if raw is not None and raw <= HORIZON_SECONDS:
        return {"outcome": "BREACH_RECOVERED_WITHIN_300_SECONDS", "recovery_observed_within_300": True, "right_censored": False, "recovery_seconds": raw, "raw_recovery_seconds": raw, "censor_reason": None}
    return {"outcome": "BREACH_RIGHT_CENSORED_AT_300_SECONDS", "recovery_observed_within_300": False, "right_censored": True, "recovery_seconds": HORIZON_SECONDS, "raw_recovery_seconds": raw, "censor_reason": "RECOVERY_NOT_OBSERVED_WITHIN_300_SECONDS"}


def run_episode(args: argparse.Namespace, legacy, source_package: Path, output_root: Path, row: dict, t1t2: float, c5mod, c4mod) -> dict:
    rate, block, treatment = int(row["challenge_rps"]), int(row["block_within_workload"]), row["treatment"]
    runroot = output_root / f"rps-{rate:02d}"
    (runroot / "episodes").mkdir(parents=True, exist_ok=True)
    runtime = prepare_runtime_package(source_package, output_root / "runtime-packages", rate)
    attempt_no = 1
    while (runroot / "episodes" / f"NET470-STRESS-R{rate:02d}-B{block:02d}-{treatment}-A{attempt_no:02d}").exists():
        attempt_no += 1
    attempt = runroot / "episodes" / f"NET470-STRESS-R{rate:02d}-B{block:02d}-{treatment}-A{attempt_no:02d}"
    cpu_target, mode = legacy.treatment_config(treatment)
    original = deepcopy(legacy.spec(args.context, args.namespace))
    original_hash = legacy.canon(original)
    legacy.patch(args.context, args.namespace, {"minReplicas": 1, "maxReplicas": 5, "metrics": [{"type": "Resource", "resource": {"name": "cpu", "target": {"type": "Utilization", "averageUtilization": cpu_target}}}]})
    ledger: list[dict] = []
    stop = threading.Event()
    thread = None
    if mode != "STATIC":
        thread = threading.Thread(target=legacy.controller, args=(args.context, args.namespace, treatment, attempt, stop, ledger, original, t1t2, c5mod, c4mod), daemon=True)
        thread.start()
    env = os.environ.copy()
    env["C5_STAGE6_EPISODE_ID"] = attempt.name
    env["C5_STAGE6_RUN_ID"] = "V3-K-" + attempt.name
    env["C5_FORMAL_CPU_TARGET"] = str(cpu_target)
    print(f"STRESS_EPISODE_START rate={rate} block={block} treatment={treatment} attempt={attempt_no}", flush=True)
    try:
        process = legacy.run_core([sys.executable, str(runtime / "C5-Stage9-Episode-Core.py"), "--root", args.project_root, "--package-root", str(runtime), "--output", str(attempt), "--context", args.context, "--namespace", args.namespace], attempt, runroot, env)
    finally:
        stop.set()
        if thread:
            thread.join(timeout=90)
        legacy.restore(args.context, args.namespace, original)
    final_hash = legacy.canon(legacy.spec(args.context, args.namespace))
    core_path = attempt / "analysis" / "C5-FAST-03-pilot-summary.json"
    if not core_path.is_file():
        raise RuntimeError(f"CORE_SUMMARY_MISSING:{attempt.name}:stdout={process.stdout_path}:stderr={process.stderr_path}")
    core = json.loads(core_path.read_text(encoding="utf-8"))
    events = core.get("events") or {}
    uncertainty = core.get("maximum_clock_mapping_uncertainty_ms")
    base_technical = (
        not process.timed_out and
        int(core.get("request_rows") or -1) == expected_requests(rate) and
        final_hash == original_hash and
        not core.get("technical_failure") and
        uncertainty is not None and float(uncertainty) <= 500
    )
    outcome = outcome_from_events(events)
    stages = {name: elapsed_seconds(events.get(left), events.get(right)) for name, left, right in (
        ("T4_T5", "T4", "T5"), ("T6_T7", "T6", "T7"), ("T7_T8", "T7", "T8")
    )}
    actions = sum(1 for item in ledger if item.get("authorized") and str(item.get("action", "")).startswith("HPA_MIN_TO_"))
    recommendations = sum(1 for item in ledger if str(item.get("action", "")).startswith("RECOMMEND"))
    summary = {
        "schema": "NET470-STRESS-EPISODE-V1",
        "recorded_at_utc": now(),
        "global_sequence": int(row["global_sequence"]),
        "workload_block_order": int(row["workload_block_order"]),
        "challenge_rps": rate,
        "block_within_workload": block,
        "position_within_block": int(row["position_within_block"]),
        "treatment": treatment,
        "attempt_number": attempt_no,
        "execution_attempt_id": attempt.name,
        "scheduled_requests": expected_requests(rate),
        "request_rows": core.get("request_rows"),
        "completed_requests": core.get("completed_requests"),
        "error_requests": core.get("error_requests"),
        **outcome,
        "events": events,
        "focused_stage_durations_seconds": stages,
        **slo_metrics(attempt),
        **resource_metrics(attempt),
        **throughput_metrics(attempt),
        "controller_actions": actions,
        "controller_recommendations": recommendations,
        "hpa_exactly_restored": final_hash == original_hash,
        "maximum_clock_mapping_uncertainty_ms": uncertainty,
        "core_exit_code": process.returncode,
        "core_timed_out": process.timed_out,
        "core_stdout_log": process.stdout_path,
        "core_stderr_log": process.stderr_path,
        "scientifically_admissible": base_technical,
        "selection_rule": "EARLIEST_TECHNICALLY_VALID_ATTEMPT",
        "technical_failure": "" if base_technical else (core.get("technical_failure") or "TECHNICAL_QUALIFICATION_FAILED"),
    }
    analysis = attempt / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "C5-controller-ledger.json").write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    (analysis / "NET470-stress-episode-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"STRESS_EPISODE_END id={attempt.name} admissible={base_technical} outcome={outcome['outcome']} errors={core.get('error_requests')}", flush=True)
    require(base_technical, f"EPISODE_PRESERVED_NOT_ADMISSIBLE:{attempt.name}")
    return summary


def self_test(protocol_root: Path) -> None:
    schedule = load_schedule(protocol_root)
    assert [expected_requests(x) for x in LEVELS] == [2850, 3750, 4650, 5550, 6450]
    assert len(schedule) == 125
    assert len({row["workload_block_order"] for row in schedule}) == 25
    for order in range(1, 26):
        block = [row for row in schedule if row["workload_block_order"] == order]
        assert len(block) == 5 and {row["treatment"] for row in block} == set(TREATMENTS)
    assert outcome_from_events({})["outcome"] == "NO_QUALIFYING_BREACH"
    assert outcome_from_events({"TB": "2026-01-01T00:00:00Z", "TR": "2026-01-01T00:04:00Z"})["outcome"] == "BREACH_RECOVERED_WITHIN_300_SECONDS"
    assert outcome_from_events({"TB": "2026-01-01T00:00:00Z", "TR": "2026-01-01T00:05:01Z"})["outcome"] == "BREACH_RIGHT_CENSORED_AT_300_SECONDS"
    print("NET470_FINAL_STRESS_SELF_TEST=PASS")
    print("PLANNED_EPISODES=125")
    print("WORKLOAD_LEVELS_RPS=5,10,15,20,25")
    print("TREATMENTS=C1,C2,C3,C4,C5")
    print("STRICT_300_SECOND_CENSORING=PASS")
    print("NO_BREACH_CLASSIFICATION=PASS")
    print("DEFAULT_MODE=NO_LIVE_WORKLOAD")


def finalize(output_root: Path, selected: dict[tuple[int, int, str], dict]) -> None:
    rows = [selected[key] for key in sorted(selected)]
    flat = []
    for row in rows:
        stage = row.get("focused_stage_durations_seconds") or {}
        flat.append({**{k: v for k, v in row.items() if k not in ("events", "focused_stage_durations_seconds")},
                     "T4_T5_seconds": stage.get("T4_T5"), "T6_T7_seconds": stage.get("T6_T7"), "T7_T8_seconds": stage.get("T7_T8")})
    dataset = output_root / "NET470-five-level-C1-C5-primary-dataset.csv"
    write_csv(dataset, flat)
    record = {
        "schema": "NET470-FIVE-LEVEL-C1-C5-STRESS-RESULT-V1",
        "recorded_at_utc": now(),
        "status": "VERIFIED_PASS",
        "episodes": len(rows),
        "workload_levels": 5,
        "treatments": 5,
        "blocks_per_level": 5,
        "total_scheduled_requests": sum(int(row["scheduled_requests"]) for row in rows),
        "total_completed_requests": sum(int(row["completed_requests"]) for row in rows),
        "total_error_requests": sum(int(row["error_requests"]) for row in rows),
        "no_breach_episodes": sum(row["outcome"] == "NO_QUALIFYING_BREACH" for row in rows),
        "observed_recovery_episodes": sum(row["outcome"] == "BREACH_RECOVERED_WITHIN_300_SECONDS" for row in rows),
        "right_censored_episodes": sum(bool(row["right_censored"]) for row in rows),
        "hpa_restored_after_every_episode": all(bool(row["hpa_exactly_restored"]) for row in rows),
        "primary_contrast": "C5_MINUS_C2_BY_WORKLOAD_LEVEL",
        "dataset_sha256": sha256(dataset),
    }
    record_path = output_root / "NET470-five-level-C1-C5-final-record.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    freeze = output_root / "NET470-FIVE-LEVEL-C1-C5-FREEZE.txt"
    freeze.write_text(f"SCHEMA=NET470-FIVE-LEVEL-C1-C5-FREEZE-V1\nDATASET_SHA256={sha256(dataset)}\nRECORD_SHA256={sha256(record_path)}\n", encoding="utf-8")
    print("NET470_FINAL_STRESS_EVALUATION=VERIFIED_PASS")
    print(f"EPISODES={len(rows)}")
    print(f"DATASET={dataset}")
    print(f"FREEZE_SHA256={sha256(freeze)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--acknowledge-mutation", default="")
    parser.add_argument("--protocol-root", required=True)
    parser.add_argument("--project-root", default=r"C:\net470-v3")
    parser.add_argument("--stage9-package-root", default=r"C:\NET470-C5-Fast\Stage9-Package-R07")
    parser.add_argument("--stage8-r02-root", required=False)
    parser.add_argument("--stage6-root", required=False)
    parser.add_argument("--output-root", default=r"C:\NET470-C5-Fast\Stress-Evaluation-R01\final-c1-c5")
    parser.add_argument("--context", default="net470-v3")
    parser.add_argument("--namespace", default="net470-v3")
    parser.add_argument("--max-blocks", type=int, default=1)
    args = parser.parse_args()
    protocol_root = Path(args.protocol_root).resolve()
    self_test(protocol_root)
    source_package = Path(args.stage9_package_root).resolve()
    require(sha256(source_package / "C5-Stage9-Episode-Core.py") == CORE_SHA256, "SOURCE_CORE_HASH_MISMATCH")
    require(sha256(source_package / "Invoke-C5-Fast-09-R07.py") == RUNNER_SHA256, "SOURCE_RUNNER_HASH_MISMATCH")
    if args.self_test:
        return 0
    if not args.live:
        print("NET470_FINAL_STRESS_EVALUATION=DRY_RUN_ONLY")
        print("LIVE_WORKLOAD_EXECUTED=NO")
        print(f"Type {LIVE_ACK} to authorize one randomized five-treatment block.")
        return 0
    require(args.acknowledge_mutation == LIVE_ACK, "LIVE_ACKNOWLEDGEMENT_MISMATCH")
    require(args.stage8_r02_root and args.stage6_root, "STAGE8_AND_STAGE6_ROOTS_REQUIRED_FOR_LIVE_RUN")
    legacy = load_module(source_package / "Invoke-C5-Fast-09-R07.py", "net470_stage9_r07")
    legacy.validate_binding(Path(args.stage8_r02_root))
    c4path = Path(args.project_root) / "scripts" / "guide8" / "g8_controller_v2.py"
    require(legacy.sha(c4path) == legacy.C4_CONTROLLER_SHA, "C4_CONTROLLER_HASH_MISMATCH")
    c4mod = legacy.load(c4path, "net470_c4_controller")
    c5mod = legacy.load(source_package / "c5_production_controller_v1.py", "net470_c5_controller")
    t1t2 = legacy.budget(Path(args.stage6_root))
    require(legacy.run(["kubectl", "config", "current-context"]).stdout.strip() == args.context, "CONTEXT_MISMATCH")
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    schedule = load_schedule(protocol_root)
    selected = accepted(output_root)
    pending_orders = []
    for order in range(1, 26):
        block_rows = [row for row in schedule if row["workload_block_order"] == order]
        if any((row["challenge_rps"], row["block_within_workload"], row["treatment"]) not in selected for row in block_rows):
            pending_orders.append(order)
    for order in pending_orders[: max(1, args.max_blocks)]:
        block_rows = sorted([row for row in schedule if row["workload_block_order"] == order], key=lambda row: row["position_within_block"])
        print(f"STRESS_BLOCK_START global_order={order} rate={block_rows[0]['challenge_rps']} block={block_rows[0]['block_within_workload']}", flush=True)
        for row in block_rows:
            key = (row["challenge_rps"], row["block_within_workload"], row["treatment"])
            if key not in accepted(output_root):
                run_episode(args, legacy, source_package, output_root, row, t1t2, c5mod, c4mod)
        print(f"STRESS_BLOCK_COMPLETE global_order={order}", flush=True)
    selected = accepted(output_root)
    completed_orders = 0
    for order in range(1, 26):
        rows = [row for row in schedule if row["workload_block_order"] == order]
        if all((row["challenge_rps"], row["block_within_workload"], row["treatment"]) in selected for row in rows):
            completed_orders += 1
    state = {"schema": "NET470-STRESS-RESUME-STATE-V1", "updated_at_utc": now(), "accepted_episodes": len(selected), "completed_blocks": completed_orders, "remaining_episodes": 125 - len(selected), "next_global_block_order": next((x for x in range(1, 26) if x > completed_orders), None)}
    (output_root / "NET470-stress-resume-state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    if len(selected) == 125:
        finalize(output_root, selected)
    else:
        print("=== NET470 FINAL STRESS PROGRESS ===")
        print("STATUS=IN_PROGRESS")
        print(f"ACCEPTED_EPISODES={len(selected)}")
        print(f"COMPLETED_BLOCKS={completed_orders}")
        print(f"REMAINING_EPISODES={125-len(selected)}")
        print("NEXT_STEP=RERUN_TO_RESUME")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"NET470_FINAL_STRESS_FAILURE={type(exc).__name__}:{exc}", file=sys.stderr)
        raise
