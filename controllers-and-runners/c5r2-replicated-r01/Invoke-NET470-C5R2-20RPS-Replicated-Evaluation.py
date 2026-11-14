#!/usr/bin/env python3
"""Guarded, resumable C2/C5/C5-R2 20-RPS replicated evaluation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "NET470-C5R2-20RPS-REPLICATED-EVALUATION-R01"
LIVE_ACK = "RUN_NET470_C5R2_20RPS_REPLICATED_EVALUATION"
TREATMENTS = ("C2", "C5", "C5-R2")
EXPECTED_REQUESTS = 5550
PLANNED_EPISODES = 15
PLANNED_BLOCKS = 5
SCHEDULE_SHA = "81BF22C5790086088FB5A3BA4A694E7DAA59FAFD3ABE49A7D517AC9EEAFC3FCF"
RUNTIME_HASHES = {
    "C5-Stage9-Episode-Core.py": "2DA30D576F38695B47EF8C179C309CE7F88B7570D73D48F6826E802840D29E69",
    "c5_production_controller_v1.py": "D0F9FE6463C0326735813DC20F542F6AF73A9C8E53ADA5A8EAA26D97079118A2",
    "C5-Protocol-Generator.py": "D5F65D2C99F6597D8C2D8D41070803B41D5282F0F2F0A39E518010C54A333C72",
    "config/C5-FAST-03-PILOT-CONTRACT.json": "14035BBABCAF8C4A289581F465508BEC6728DBAD7A46DB29A5F727109006C601",
}
STAGE6_BUDGET_SHA = "B9D67440E6C99394B58A1423A6702C9C6161CDEE31B75AEA0102108831A3803D"
LEGACY_RUNNER_SHA = "C69521EE002A5E9DFDA4613012C9D54B338BB0F2F2E0422116748AFBC321E71D"
C5R2_CONTROLLER_SHA = "EC780EA6F493EF2EA411238EA0BA8B69CFC3FA7B68E116C1CDBEF42EA7919737"
SUPPORT_SHA = "4EAD83B2E44A65DEF2297C9606813695655FB54260534B3032F9070E9BADE7F8"
T7_T8_BUDGET_SECONDS = 71.82882


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(0.95 * len(values)) - 1)]


def completed(row: dict) -> bool:
    return str(row.get("outcome", "")).lower() == "completed"


def runtime_windows(rows: list[dict]) -> list[dict]:
    config = {
        "high-challenge": (20, 180),
        "low-recovery": (5, 300),
        "post-observation": (5, 30),
    }
    windows = []
    for phase in ("high-challenge", "low-recovery", "post-observation"):
        phase_rows = [r for r in rows if r.get("phase_id") == phase and r.get("scheduled_time_utc")]
        if not phase_rows:
            continue
        rate, duration = config[phase]
        origin = min(dt(r["scheduled_time_utc"]) for r in phase_rows)
        for index in range(duration // 15):
            start = origin.timestamp() + index * 15
            end = start + 15
            bucket = [r for r in phase_rows if start <= dt(r["scheduled_time_utc"]).timestamp() < end]
            done = [r for r in bucket if completed(r)]
            values = [float(r["client_elapsed_ms"]) for r in done if r.get("client_elapsed_ms") not in (None, "")]
            p95 = percentile95(values)
            windows.append({
                "phase": phase,
                "index": index,
                "start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat().replace("+00:00", "Z"),
                "end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat().replace("+00:00", "Z"),
                "expected": rate * 15,
                "scheduled": len(bucket),
                "completed": len(done),
                "errors": len(bucket) - len(done),
                "p95_ms": p95,
                "complete": len(bucket) == rate * 15,
            })
    return windows


def sample_for_window(samples: list[dict], end_utc: str) -> dict | None:
    clean = [s for s in samples if not s.get("sampler_error") and s.get("observed_at_utc")]
    after = [s for s in clean if dt(s["observed_at_utc"]) >= dt(end_utc)]
    if after:
        return min(after, key=lambda s: dt(s["observed_at_utc"]))
    return clean[-1] if clean else None


def guarded_thread_call(errors: list[str], function, *args) -> None:
    try:
        function(*args)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}:{exc}")
        raise


def c5r2_controller_loop(context: str, namespace: str, attempt: Path, stop: threading.Event,
                         ledger: list[dict], original: dict, legacy, c5r2mod) -> None:
    controller = c5r2mod.Controller()
    processed: set[tuple[str, int]] = set()
    tb_utc: str | None = None
    applied_targets: list[int] = []
    live = attempt / "telemetry" / "canonical" / ("V3-K-" + attempt.name) / "live_request_results.jsonl"
    cluster = attempt / "telemetry" / "live-cluster-samples.jsonl"
    try:
        while not stop.is_set():
            requests = read_jsonl(live)
            samples = read_jsonl(cluster)
            progressed = False
            for window in runtime_windows(requests):
                key = (window["phase"], window["index"])
                if key in processed or not window["complete"]:
                    continue
                sample = sample_for_window(samples, window["end_utc"])
                if sample is None or window["p95_ms"] is None:
                    continue
                desired = int(sample.get("hpa_desired_replicas") or 0)
                current = int(sample.get("hpa_current_replicas") or 0)
                ready = len(sample.get("processing_ready_endpoints") or [])
                elapsed = max(0.0, (dt(window["end_utc"]) - dt(tb_utc)).total_seconds()) if tb_utc else 0.0
                observation = {
                    "phase": window["phase"],
                    "window_complete": window["complete"],
                    "elapsed_recovery_seconds": elapsed,
                    "client_p95_ms": window["p95_ms"],
                    "completed_requests": window["completed"],
                    "client_errors": window["errors"],
                    "current_replicas": current,
                    "desired_replicas": desired,
                    "ready_endpoints": ready,
                }
                before_breach = controller.breach_confirmed
                decision = controller.observe(observation)
                if not before_breach and decision.get("breach_confirmed") and tb_utc is None:
                    tb_utc = window["end_utc"]
                row = {
                    "observed_at_utc": now(),
                    "treatment": "C5-R2",
                    "window": window,
                    "cluster": {"current": current, "desired": desired, "ready": ready},
                    "tb_utc": tb_utc,
                    "elapsed_recovery_seconds": elapsed,
                    **decision,
                }
                action = str(decision.get("action") or "HOLD")
                if decision.get("authorized") and action.startswith("HPA_MIN_TO_"):
                    target = int(decision["target_min_replicas"])
                    require(len(applied_targets) < 2, "C5R2_ACTION_BUDGET_EXCEEDED")
                    require(2 <= target <= 5, f"C5R2_TARGET_OUT_OF_BOUNDS:{target}")
                    require(target not in applied_targets, f"C5R2_DUPLICATE_TARGET:{target}")
                    require(not applied_targets or target > max(applied_targets), f"C5R2_NON_INCREASING_TARGET:{target}")
                    if applied_targets:
                        require(target == 5, f"C5R2_ESCALATION_MUST_TARGET_MAXIMUM:{target}")
                    legacy.patch(context, namespace, {"minReplicas": target})
                    applied_targets.append(target)
                    row["mutation_applied"] = True
                    row["applied_action_number"] = len(applied_targets)
                elif decision.get("authorized") and action == "RESTORE_EXACT_HPA_SPEC":
                    legacy.restore(context, namespace, original)
                    row["restoration_applied"] = True
                ledger.append(row)
                processed.add(key)
                progressed = True
                if decision.get("state") in ("RECOVERED", "CENSORED", "FAIL_SAFE"):
                    return
            stop.wait(0.5 if progressed else 1.0)
    finally:
        legacy.restore(context, namespace, original)
        ledger.append({
            "observed_at_utc": now(), "treatment": "C5-R2",
            "action": "RESTORE_EXACT_HPA_SPEC", "authorized": True,
            "reason": "finally", "restoration_applied": True,
        })


def validate_sources(args, package: Path) -> tuple[Path, Path, Path]:
    runtime = Path(args.runtime_r20).resolve()
    stage6_budget = Path(args.stage6_budget).resolve()
    for relative, expected in RUNTIME_HASHES.items():
        path = runtime / Path(relative)
        require(path.is_file(), f"RUNTIME_FILE_MISSING:{path}")
        require(sha(path) == expected, f"RUNTIME_HASH_MISMATCH:{relative}:{sha(path)}")
    schedule = package / "NET470-C5R2-20RPS-replicated-schedule.csv"
    require(schedule.is_file(), f"PILOT_SCHEDULE_MISSING:{schedule}")
    require(sha(schedule) == SCHEDULE_SHA, f"PILOT_SCHEDULE_HASH_MISMATCH:{sha(schedule)}")
    require(stage6_budget.is_file() and sha(stage6_budget) == STAGE6_BUDGET_SHA, "STAGE6_BUDGET_HASH_MISMATCH")
    with stage6_budget.open(encoding="utf-8-sig", newline="") as handle:
        budget_rows = list(csv.DictReader(handle))
    t7_t8 = [float(row["budget_seconds_median_plus_3mad"]) for row in budget_rows if row.get("stage") == "T7_T8"]
    require(t7_t8 == [T7_T8_BUDGET_SECONDS], f"T7_T8_BUDGET_MISMATCH:{t7_t8}")
    require(sha(package / "c5r2_controller_v1.py") == C5R2_CONTROLLER_SHA, "C5R2_CONTROLLER_HASH_MISMATCH")
    require(sha(package / "net470_final_stress_support.py") == SUPPORT_SHA, "SUPPORT_HASH_MISMATCH")
    legacy_path = Path(args.stage9_package_root).resolve() / "Invoke-C5-Fast-09-R07.py"
    require(legacy_path.is_file() and sha(legacy_path) == LEGACY_RUNNER_SHA, "LEGACY_RUNNER_HASH_MISMATCH")
    return runtime, schedule, legacy_path


def load_schedule(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    require(len(rows) == PLANNED_EPISODES, "PILOT_SCHEDULE_COUNT")
    expected = [
        (1, 1, "C2"), (1, 2, "C5"), (1, 3, "C5-R2"),
        (2, 1, "C5"), (2, 2, "C5-R2"), (2, 3, "C2"),
        (3, 1, "C5-R2"), (3, 2, "C2"), (3, 3, "C5"),
        (4, 1, "C2"), (4, 2, "C5-R2"), (4, 3, "C5"),
        (5, 1, "C5"), (5, 2, "C2"), (5, 3, "C5-R2"),
    ]
    observed = [(int(r["block"]), int(r["position"]), r["treatment"]) for r in rows]
    require(observed == expected, f"PILOT_ORDER_MISMATCH:{observed}")
    require([int(r["global_order"]) for r in rows] == list(range(1, 16)), "GLOBAL_ORDER_MISMATCH")
    require(all(int(r["challenge_rps"]) == 20 for r in rows), "PILOT_RPS_MISMATCH")
    require(all(float(r["slo_p95_ms"]) == 123.472 for r in rows), "PILOT_SLO_MISMATCH")
    require(all(int(r["stable_windows_required"]) == 8 for r in rows), "PILOT_STABILITY_RULE_MISMATCH")
    require(all(int(r["recovery_horizon_seconds"]) == 300 for r in rows), "PILOT_HORIZON_MISMATCH")
    require(all(float(r["t7_t8_budget_seconds"]) == T7_T8_BUDGET_SECONDS for r in rows), "PILOT_T7_T8_BUDGET_MISMATCH")
    require(all(int(r["hpa_max"]) == 5 for r in rows), "PILOT_HPA_MAX_MISMATCH")
    return rows


def self_test(package: Path) -> None:
    support = load(package / "net470_final_stress_support.py", "net470_support_test")
    c5r2 = load(package / "c5r2_controller_v1.py", "net470_c5r2_test")
    require(c5r2.self_test(), "C5R2_CONTROLLER_SELF_TEST")
    require(support.expected_requests(20) == EXPECTED_REQUESTS, "EXPECTED_REQUEST_TEST")
    assert support.outcome_from_events({})["outcome"] == "NO_QUALIFYING_BREACH"
    assert support.outcome_from_events({"TB": "2026-01-01T00:00:00Z", "TR": "2026-01-01T00:05:01Z"})["right_censored"]
    synthetic = []
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(300):
        synthetic.append({"phase_id": "high-challenge", "scheduled_time_utc": datetime.fromtimestamp(origin.timestamp()+i/20, timezone.utc).isoformat().replace("+00:00", "Z"), "outcome": "completed", "client_elapsed_ms": 500})
    first = runtime_windows(synthetic)[0]
    require(first["complete"] and first["expected"] == 300, "WINDOW_CONTRACT_TEST")
    schedule = load_schedule(package / "NET470-C5R2-20RPS-replicated-schedule.csv")
    require(len(schedule) == PLANNED_EPISODES, "SCHEDULE_SELF_TEST")
    print("NET470_C5R2_20RPS_REPLICATED_SELF_TEST=PASS")
    print(f"PLANNED_EPISODES={PLANNED_EPISODES}")
    print(f"PLANNED_BLOCKS={PLANNED_BLOCKS}")
    print("WORKLOAD_RPS=20")
    print("TREATMENTS=C2,C5,C5-R2")
    print(f"FROZEN_T7_T8_BUDGET_SECONDS={T7_T8_BUDGET_SECONDS}")
    print("INITIAL_FLOOR_ACTIONS_MAXIMUM=1")
    print("GUARDED_ESCALATIONS_MAXIMUM=1")
    print("STRICT_300_SECOND_CENSORING=PASS")
    print("EXACT_HPA_RESTORATION=PASS")
    print("DEFAULT_MODE=NO_LIVE_WORKLOAD")


def accepted(output: Path) -> dict[tuple[int, str], dict]:
    chosen: dict[tuple[int, str], dict] = {}
    for path in sorted(output.glob("episodes/*/analysis/NET470-C5R2-replicated-episode-summary.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        key = (int(row["block"]), row["treatment"])
        if row.get("scientifically_admissible") and key not in chosen:
            chosen[key] = row
    return chosen


def write_dataset(output: Path, chosen: dict[tuple[int, str], dict]) -> Path:
    rows = sorted(chosen.values(), key=lambda row: int(row["global_order"]))
    dataset = output / "NET470-C5R2-20RPS-replicated-dataset.csv"
    fields = [
        "schema", "recorded_at_utc", "global_order", "block", "position",
        "treatment", "attempt_number", "execution_attempt_id", "challenge_rps",
        "scheduled_requests", "request_rows", "completed_requests", "error_requests",
        "outcome", "recovery_observed_within_300", "right_censored",
        "recovery_seconds", "raw_recovery_seconds", "censor_reason",
        "slo_windows", "slo_violating_windows", "slo_violating_window_percent",
        "slo_violation_duration_seconds", "mean_cpu_percent", "peak_cpu_percent",
        "peak_desired_replicas", "peak_ready_endpoints", "replica_seconds",
        "high_challenge_requests", "high_challenge_completed",
        "achieved_throughput_rps", "request_success_percent", "controller_actions",
        "T4_T5_seconds", "T6_T7_seconds", "T7_T8_seconds",
        "hpa_exactly_restored", "maximum_clock_mapping_uncertainty_ms",
        "controller_runtime_error_count", "controller_thread_stopped",
        "core_exit_code", "core_timed_out", "scientifically_admissible",
        "selection_rule", "technical_failure",
    ]
    with dataset.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            stages = row.get("focused_stage_durations_seconds") or {}
            flat = {name: row.get(name) for name in fields}
            flat["T4_T5_seconds"] = stages.get("T4_T5")
            flat["T6_T7_seconds"] = stages.get("T6_T7")
            flat["T7_T8_seconds"] = stages.get("T7_T8")
            flat["controller_runtime_error_count"] = len(row.get("controller_runtime_errors") or [])
            writer.writerow(flat)
    return dataset


def run_episode(args, package: Path, runtime: Path, legacy, support, c5r2mod,
                c5mod, treatment: str, block: int, position: int,
                global_order: int, t1t2: float, output: Path) -> dict:
    attempt_no = 1
    tag = treatment.replace("-", "")
    while (output / "episodes" / f"NET470-C5R2-R20-B{block:02d}-{tag}-A{attempt_no:02d}").exists():
        attempt_no += 1
    attempt = output / "episodes" / f"NET470-C5R2-R20-B{block:02d}-{tag}-A{attempt_no:02d}"
    original = deepcopy(legacy.spec(args.context, args.namespace))
    original_hash = legacy.canon(original)
    require(int(original.get("minReplicas") or 1) == 1, "BASELINE_MIN_NOT_ONE")
    require(int(original.get("maxReplicas") or 0) == 5, "BASELINE_MAX_NOT_FIVE")
    metrics = original.get("metrics") or []
    cpu = [m.get("resource", {}).get("target", {}).get("averageUtilization") for m in metrics if m.get("type") == "Resource" and m.get("resource", {}).get("name") == "cpu"]
    require(cpu == [55], f"BASELINE_CPU_TARGET_NOT_55:{cpu}")
    ledger: list[dict] = []
    controller_errors: list[str] = []
    stop = threading.Event()
    thread = None
    controller_thread_stopped = True
    if treatment == "C5":
        thread = threading.Thread(target=guarded_thread_call, args=(controller_errors, legacy.controller, args.context, args.namespace, "C5", attempt, stop, ledger, original, t1t2, c5mod, None), daemon=True)
    elif treatment == "C5-R2":
        thread = threading.Thread(target=guarded_thread_call, args=(controller_errors, c5r2_controller_loop, args.context, args.namespace, attempt, stop, ledger, original, legacy, c5r2mod), daemon=True)
    if thread:
        thread.start()
    env = os.environ.copy()
    env["C5_STAGE6_EPISODE_ID"] = attempt.name
    env["C5_STAGE6_RUN_ID"] = "V3-K-" + attempt.name
    env["C5_FORMAL_CPU_TARGET"] = "55"
    print(f"C5R2_REPLICATED_EPISODE_START global_order={global_order} block={block} position={position} treatment={treatment} attempt={attempt_no}", flush=True)
    runroot = output
    try:
        process = legacy.run_core([sys.executable, str(runtime / "C5-Stage9-Episode-Core.py"), "--root", args.project_root, "--package-root", str(runtime), "--output", str(attempt), "--context", args.context, "--namespace", args.namespace], attempt, runroot, env)
    finally:
        stop.set()
        if thread:
            thread.join(timeout=90)
            controller_thread_stopped = not thread.is_alive()
        legacy.restore(args.context, args.namespace, original)
    final_hash = legacy.canon(legacy.spec(args.context, args.namespace))
    core_path = attempt / "analysis" / "C5-FAST-03-pilot-summary.json"
    require(core_path.is_file(), f"CORE_SUMMARY_MISSING:{attempt.name}:{process.stdout_path}:{process.stderr_path}")
    core = json.loads(core_path.read_text(encoding="utf-8"))
    events = core.get("events") or {}
    uncertainty = core.get("maximum_clock_mapping_uncertainty_ms")
    base = (not process.timed_out and int(core.get("request_rows") or -1) == EXPECTED_REQUESTS and final_hash == original_hash and not core.get("technical_failure") and uncertainty is not None and float(uncertainty) <= 500 and not controller_errors and controller_thread_stopped)
    outcome = support.outcome_from_events(events)
    stages = {name: support.elapsed_seconds(events.get(left), events.get(right)) for name, left, right in (("T4_T5", "T4", "T5"), ("T6_T7", "T6", "T7"), ("T7_T8", "T7", "T8"))}
    actions = sum(bool(x.get("authorized")) and str(x.get("action", "")).startswith("HPA_MIN_TO_") for x in ledger)
    summary = {
        "schema": "NET470-C5R2-20RPS-REPLICATED-EPISODE-V1", "recorded_at_utc": now(),
        "global_order": global_order, "block": block, "position": position,
        "treatment": treatment, "attempt_number": attempt_no,
        "execution_attempt_id": attempt.name, "challenge_rps": 20,
        "scheduled_requests": EXPECTED_REQUESTS, "request_rows": core.get("request_rows"),
        "completed_requests": core.get("completed_requests"), "error_requests": core.get("error_requests"),
        **outcome, "events": events, "focused_stage_durations_seconds": stages,
        **support.slo_metrics(attempt), **support.resource_metrics(attempt), **support.throughput_metrics(attempt),
        "controller_actions": int(actions), "hpa_exactly_restored": final_hash == original_hash,
        "controller_runtime_errors": list(controller_errors),
        "controller_thread_stopped": controller_thread_stopped,
        "maximum_clock_mapping_uncertainty_ms": uncertainty, "core_exit_code": process.returncode,
        "core_timed_out": process.timed_out, "core_stdout_log": process.stdout_path,
        "core_stderr_log": process.stderr_path, "scientifically_admissible": base,
        "selection_rule": "EARLIEST_TECHNICALLY_VALID_ATTEMPT",
        "technical_failure": "" if base else (core.get("technical_failure") or "TECHNICAL_QUALIFICATION_FAILED"),
    }
    analysis = attempt / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "C5R2-controller-ledger.json").write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    (analysis / "NET470-C5R2-replicated-episode-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"C5R2_REPLICATED_EPISODE_END id={attempt.name} admissible={base} outcome={outcome['outcome']} errors={core.get('error_requests')} actions={actions}", flush=True)
    require(base, f"EPISODE_PRESERVED_NOT_ADMISSIBLE:{attempt.name}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--acknowledge-mutation", default="")
    parser.add_argument("--runtime-r20")
    parser.add_argument("--stage6-budget")
    parser.add_argument("--stage6-root")
    parser.add_argument("--stage8-r02-root")
    parser.add_argument("--stage9-package-root")
    parser.add_argument("--project-root", default=r"C:\net470-v3")
    parser.add_argument("--output-root", default=r"C:\NET470-C5-Fast\Stress-Evaluation-R01\C5-R-Evaluation-R01\replicated-20rps-c5r2-r01")
    parser.add_argument("--context", default="net470-v3")
    parser.add_argument("--namespace", default="net470-v3")
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    self_test(package)
    if args.self_test:
        return 0
    required = (args.runtime_r20, args.stage6_budget, args.stage9_package_root)
    require(all(required), "BINDING_ARGUMENTS_REQUIRED")
    runtime, schedule_path, legacy_path = validate_sources(args, package)
    rows = load_schedule(schedule_path)
    print("NET470_C5R2_20RPS_REPLICATED_SOURCE_BINDING=PASS")
    print("RUNTIME_R20_SHA256=PASS")
    print("PILOT_SCHEDULE_SHA256=PASS")
    print("STAGE6_BUDGET_SHA256=PASS")
    if not args.live:
        print("NET470_C5R2_20RPS_REPLICATED_EVALUATION=DRY_RUN_ONLY")
        print("DEFAULT_MODE=NO_LIVE_WORKLOAD")
        print("KUBERNETES_QUERIES=0")
        print("KUBERNETES_MODIFICATIONS=0")
        print("LIVE_WORKLOAD_EXECUTED=NO")
        print(f"Type {LIVE_ACK} to authorize the fifteen-episode replicated evaluation.")
        return 0
    require(args.acknowledge_mutation == LIVE_ACK, "LIVE_ACKNOWLEDGEMENT_MISMATCH")
    require(args.stage6_root and args.stage8_r02_root, "LIVE_EVIDENCE_ROOTS_REQUIRED")
    legacy = load(legacy_path, "net470_legacy_r07")
    support = load(package / "net470_final_stress_support.py", "net470_stress_support")
    c5r2mod = load(package / "c5r2_controller_v1.py", "net470_c5r2_live")
    legacy.validate_binding(Path(args.stage8_r02_root))
    c5mod = load(runtime / "c5_production_controller_v1.py", "net470_c5_original")
    t1t2 = legacy.budget(Path(args.stage6_root))
    require(abs(float(t1t2) - 3.07655) < 0.000001, f"T1_T2_BUDGET_MISMATCH:{t1t2}")
    require(legacy.run(["kubectl", "config", "current-context"]).stdout.strip() == args.context, "CONTEXT_MISMATCH")
    output = Path(args.output_root).resolve()
    (output / "episodes").mkdir(parents=True, exist_ok=True)
    done = accepted(output)
    for row in rows:
        treatment = row["treatment"]
        block = int(row["block"])
        key = (block, treatment)
        if key not in done:
            run_episode(args, package, runtime, legacy, support, c5r2mod, c5mod,
                        treatment, block, int(row["position"]), int(row["global_order"]),
                        t1t2, output)
            done = accepted(output)
            progress = {
                "schema": SCHEMA, "updated_at_utc": now(), "status": "IN_PROGRESS",
                "accepted_episodes": len(done), "planned_episodes": PLANNED_EPISODES,
                "remaining_episodes": PLANNED_EPISODES - len(done),
            }
            (output / "NET470-C5R2-20RPS-replicated-state.json").write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")
    state = {
        "schema": SCHEMA, "updated_at_utc": now(), "status": "VERIFIED_PASS" if len(done) == PLANNED_EPISODES else "IN_PROGRESS",
        "accepted_episodes": len(done), "planned_episodes": PLANNED_EPISODES,
        "completed_blocks": sum(all((block, treatment) in done for treatment in TREATMENTS) for block in range(1, PLANNED_BLOCKS + 1)),
        "remaining_episodes": PLANNED_EPISODES - len(done), "treatments": list(TREATMENTS),
        "remaining_design_cells": [f"B{int(row['block']):02d}-{row['treatment']}" for row in rows if (int(row["block"]), row["treatment"]) not in done],
        "live_workload_executed": True,
    }
    (output / "NET470-C5R2-20RPS-replicated-state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    if len(done) == PLANNED_EPISODES:
        dataset = write_dataset(output, done)
        freeze = {
            "schema": SCHEMA, "status": "VERIFIED_PASS", "episodes": len(done),
            "blocks": PLANNED_BLOCKS, "dataset": str(dataset), "dataset_sha256": sha(dataset),
            "schedule_sha256": SCHEDULE_SHA, "c5r2_controller_sha256": C5R2_CONTROLLER_SHA,
            "primary_125_episode_dataset_modified": False,
        }
        (output / "NET470-C5R2-20RPS-REPLICATED-FREEZE.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    print("NET470_C5R2_20RPS_REPLICATED_EVALUATION=" + state["status"])
    print(f"ACCEPTED_EPISODES={len(done)}")
    print(f"COMPLETED_BLOCKS={state['completed_blocks']}")
    print(f"REMAINING_EPISODES={state['remaining_episodes']}")
    print(f"OUTPUT={output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"NET470_C5R2_20RPS_REPLICATED_FAILURE={type(exc).__name__}:{exc}", file=sys.stderr)
        raise
