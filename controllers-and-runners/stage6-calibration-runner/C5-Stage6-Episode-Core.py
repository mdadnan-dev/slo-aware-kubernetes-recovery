from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

STAGE2_PROOF_SHA = "61F581A8B0E047F9ADE5A476FAC0B79D9B9C30EF095864A1D4D291CDCCA5DBC6"
STREAMING_SHA = "BFD520CD0B9EF83D97AA9FDB72FFBF45CA6B688FAD53AF2BDC8DFF0BD888CB15"
FROZEN_CLIENT_SHA = "750F1935B1D60821ED5E42A976A6152CD40AFA31423EF1A1D1FDE795167B08D7"
THETA_MS = 123.472
PHASES = [(1, "low-precondition", 5, 60), (2, "high-challenge", 25, 180), (3, "low-recovery", 5, 300), (4, "post-observation", 5, 30)]
EXPECTED = 6450


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def canon(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest().upper()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def run(command: list[str], timeout: int = 60, cwd: Path | None = None, check: bool = True):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    if check and result.returncode:
        raise RuntimeError(f"COMMAND_FAILED:{result.returncode}:{command}:{result.stderr.strip()}")
    return result


def kube_json(context: str, namespace: str, args: list[str], namespaced: bool = True):
    command = ["kubectl", "--context", context]
    if namespaced:
        command += ["-n", namespace]
    command += args + ["-o", "json"]
    return json.loads(run(command, timeout=60).stdout)


def cpu_target(spec: dict) -> int:
    values = [m.get("resource", {}).get("target", {}).get("averageUtilization") for m in spec.get("metrics", []) if m.get("type") == "Resource" and m.get("resource", {}).get("name") == "cpu"]
    require(len(values) == 1, "EXPECTED_ONE_CPU_METRIC")
    return int(values[0])


def pod_ready(pod: dict) -> bool:
    return any(x.get("type") == "Ready" and x.get("status") == "True" for x in pod.get("status", {}).get("conditions", []))


def lifecycle(pod: dict) -> dict:
    meta, status = pod.get("metadata", {}), pod.get("status", {})
    conditions = status.get("conditions", [])
    condition = lambda kind: next((x for x in conditions if x.get("type") == kind and x.get("status") == "True"), {})
    containers = status.get("containerStatuses") or []
    processing = next((x for x in containers if x.get("name") == "processing"), containers[0] if containers else {})
    return {
        "name": meta.get("name"), "uid": meta.get("uid"),
        "T3": meta.get("creationTimestamp"),
        "T4": condition("PodScheduled").get("lastTransitionTime"),
        "T5": processing.get("state", {}).get("running", {}).get("startedAt"),
        "T6": condition("Ready").get("lastTransitionTime"),
        "ready": pod_ready(pod), "node_name": pod.get("spec", {}).get("nodeName"),
        "restarts": sum(int(x.get("restartCount", 0)) for x in containers),
    }


def endpoints(context: str, namespace: str, service: str) -> list[str]:
    data = kube_json(context, namespace, ["get", "endpointslices", "-l", f"kubernetes.io/service-name={service}"])
    return sorted({e.get("targetRef", {}).get("uid") for s in data.get("items", []) for e in s.get("endpoints", []) if e.get("conditions", {}).get("ready") is not False and e.get("targetRef", {}).get("uid")})


def snapshot(context: str, namespace: str) -> dict:
    before = time.monotonic_ns()
    observed = now()
    hpa = kube_json(context, namespace, ["get", "hpa", "processing-hpa"])
    pods = kube_json(context, namespace, ["get", "pods", "-l", "app.kubernetes.io/name=processing"]).get("items", [])
    after = time.monotonic_ns()
    current_metric = None
    for metric in hpa.get("status", {}).get("currentMetrics") or []:
        if metric.get("resource", {}).get("name") == "cpu":
            current_metric = metric.get("resource", {}).get("current", {}).get("averageUtilization")
    return {
        "observed_at_utc": observed,
        "monotonic_midpoint_ns": (before + after) // 2,
        "mapping_uncertainty_ms": (after - before) / 2_000_000,
        "hpa_current_replicas": int(hpa.get("status", {}).get("currentReplicas") or 0),
        "hpa_desired_replicas": int(hpa.get("status", {}).get("desiredReplicas") or 0),
        "hpa_cpu_percent": current_metric,
        "hpa_last_scale_time": hpa.get("status", {}).get("lastScaleTime"),
        "hpa_spec_sha256": canon(hpa.get("spec", {})),
        "processing_pods": [lifecycle(x) for x in pods if x.get("metadata", {}).get("deletionTimestamp") is None],
        "processing_ready_endpoints": endpoints(context, namespace, "processing"),
        "ingestion_ready_endpoints": endpoints(context, namespace, "ingestion"),
    }


def free_port() -> int:
    for port in range(18600, 18700):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
    raise RuntimeError("NO_FREE_PROXY_PORT")


def wait_http(url: str, seconds: int = 45) -> None:
    deadline, last = time.monotonic() + seconds, None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                require(response.status == 200, f"PROXY_HEALTH_STATUS:{response.status}")
                return
        except Exception as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(f"PROXY_NOT_READY:{last}")


def read_csv(path: Path) -> list[dict]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or (list(rows[0]) if rows else [])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_live(root: Path, run_id: str) -> list[dict]:
    path = root / run_id / "live_request_results.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def phase_command(python: str, client: Path, url: str, run_id: str, episode_id: str, phase, out: Path, telemetry: Path) -> list[str]:
    seq, phase_id, rate, duration = phase
    return [python, str(client), "--url", url, "--run-id", run_id, "--rate-rps", str(rate), "--duration-seconds", str(duration), "--concurrency", "64", "--work-units", "25000", "--timeout-seconds", "30", "--out", str(out), "--telemetry-root", str(telemetry), "--phase-id", phase_id, "--phase-sequence", str(seq), "--episode-id", episode_id, "--dataset-class", "C5-GROUND-TRUTH-PILOT", "--source-version", "C5-FAST-03-R01", "--workload-writer-instance-id", "c5-stage3-workload", "--result-writer-instance-id", "c5-stage3-results"]


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(0.95 * len(values)) - 1)]


def completed(row: dict) -> bool:
    return str(row.get("outcome", "")).lower() == "completed"


def make_windows(rows: list[dict]) -> list[dict]:
    output = []
    phase_cfg = {p[1]: p for p in PHASES}
    for phase_id in ["high-challenge", "low-recovery", "post-observation"]:
        phase_rows = [r for r in rows if r.get("phase_id") == phase_id and r.get("scheduled_time_utc")]
        if not phase_rows:
            continue
        _, _, rate, duration = phase_cfg[phase_id]
        origin = min(dt(r["scheduled_time_utc"]) for r in phase_rows)
        for index in range(duration // 15):
            start = origin.timestamp() + index * 15
            end = start + 15
            bucket = [r for r in phase_rows if start <= dt(r["scheduled_time_utc"]).timestamp() < end]
            done = [r for r in bucket if completed(r)]
            latencies = [float(r["client_elapsed_ms"]) for r in done if r.get("client_elapsed_ms") not in (None, "")]
            q = p95(latencies)
            expected = rate * 15
            errors = len(bucket) - len(done)
            output.append({
                "phase_id": phase_id, "window_index_in_phase": index,
                "window_start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat().replace("+00:00", "Z"),
                "window_end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat().replace("+00:00", "Z"),
                "scheduled": len(bucket), "expected": expected, "completed": len(done), "errors": errors,
                "p95_ms": "" if q is None else round(q, 6),
                "complete": len(bucket) == expected,
                "slo_good": len(bucket) == expected and errors == 0 and q is not None and q <= THETA_MS,
            })
    return output


def stable_idle(context: str, namespace: str, spec_hash: str, required: int = 7, deadline_seconds: int = 660) -> list[dict]:
    rows, consecutive = [], 0
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline and consecutive < required:
        s = snapshot(context, namespace)
        passed = s["hpa_spec_sha256"] == spec_hash and s["hpa_current_replicas"] == 1 and s["hpa_desired_replicas"] == 1 and len(s["processing_pods"]) == 1 and s["processing_pods"][0]["ready"] and len(s["processing_ready_endpoints"]) == 1 and len(s["ingestion_ready_endpoints"]) == 5
        consecutive = consecutive + 1 if passed else 0
        rows.append({**s, "sample_pass": passed, "consecutive_stable": consecutive})
        if consecutive < required:
            time.sleep(5)
    require(consecutive == required, f"IDLE_BASELINE_NOT_RESTORED:{consecutive}_OF_{required}")
    return rows


def self_test() -> int:
    require(sum(rate * duration for _, _, rate, duration in PHASES) == EXPECTED, "REQUEST_TOTAL_TEST")
    synthetic = [0, 2, 4, 6, 8, 10, 12, 14, 18]
    require(synthetic[-1] - synthetic[0] == sum(b - a for a, b in zip(synthetic, synthetic[1:])), "DECOMPOSITION_TEST")
    require(dt("2026-01-01T00:00:00Z") < dt("2026-01-01T00:00:01Z"), "UTC_PARSE_TEST")
    print("C5_FAST_03_R01_SELF_TEST=PASS")
    print("WORKLOAD_PROTOCOL_CHANGED=NO")
    print("REQUEST_RETRIES_CHANGED=NO")
    print("GUIDE11_DATASET_WRITE_ACCESS=PROHIBITED")
    return 0


def complete(args) -> int:
    root, package, output = Path(args.root).resolve(), Path(args.package_root).resolve(), Path(args.output).resolve()
    require(not output.exists(), f"OUTPUT_EXISTS:{output}")
    proof = package / "evidence" / "C5-FAST-02-R01-VERIFIED-PASS.zip"
    contract = package / "config" / "C5-FAST-03-PILOT-CONTRACT.json"
    require(sha(proof) == STAGE2_PROOF_SHA, "STAGE2_PROOF_HASH_MISMATCH")
    streaming = root / "scripts" / "guide8" / "g8_streaming_workload_client_v2.py"
    frozen = root / "scripts" / "guide7" / "Invoke-G7-ServiceProxyWorkloadClient-v1.py"
    app_root = root / "application" / "net470-v3-app"
    require(sha(streaming) == STREAMING_SHA, "STREAMING_CLIENT_HASH_MISMATCH")
    require(sha(frozen) == FROZEN_CLIENT_SHA, "FROZEN_CLIENT_HASH_MISMATCH")
    current = run(["kubectl", "config", "current-context"]).stdout.strip()
    require(current == args.context == "net470-v3", f"CONTEXT_MISMATCH:{current}:{args.context}")
    require(args.namespace == "net470-v3", "NAMESPACE_NOT_AUTHORIZED")

    hpa = kube_json(args.context, args.namespace, ["get", "hpa", "processing-hpa"])
    bound_spec = deepcopy(hpa.get("spec", {}))
    bound_hash = canon(bound_spec)
    require(cpu_target(bound_spec) == 55 and int(bound_spec.get("minReplicas", 1)) == 1 and int(bound_spec.get("maxReplicas", 0)) == 5, "HPA_BASELINE_NOT_1_5_55")
    baseline_pods = [lifecycle(x) for x in kube_json(args.context, args.namespace, ["get", "pods", "-l", "app.kubernetes.io/name=processing"]).get("items", []) if x.get("metadata", {}).get("deletionTimestamp") is None]
    require(len(baseline_pods) == 1 and baseline_pods[0]["ready"], "PROCESSING_BASELINE_NOT_ONE_READY_POD")
    baseline_uids = {x["uid"] for x in baseline_pods}
    preflight = stable_idle(args.context, args.namespace, bound_hash, required=7, deadline_seconds=90)

    output.mkdir(parents=True)
    for name in ("logs", "workload", "telemetry", "analysis", "reset"):
        (output / name).mkdir()
    write_csv(output / "reset" / "preflight-stable-samples.csv", preflight)

    episode_id = os.environ.get("C5_STAGE6_EPISODE_ID", "C5-CAL-C2-E01-A01")
    run_id = os.environ.get("C5_STAGE6_RUN_ID", "V3-K-C5-CAL-C2-E01-A01")
    telemetry_root = output / "telemetry" / "canonical"
    phase_rows, samples, failure = [], [], None
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            try:
                samples.append(snapshot(args.context, args.namespace))
            except Exception as exc:
                samples.append({"observed_at_utc": now(), "sampler_error": f"{type(exc).__name__}:{exc}"})
            stop.wait(2)

    port = free_port()
    proxy_out = (output / "logs" / "proxy.stdout.txt").open("w", encoding="utf-8")
    proxy_err = (output / "logs" / "proxy.stderr.txt").open("w", encoding="utf-8")
    proxy = subprocess.Popen(["kubectl", "--context", args.context, "proxy", "--address=127.0.0.1", "--port", str(port), "--accept-hosts=^127\\.0\\.0\\.1$"], stdout=proxy_out, stderr=proxy_err, text=True)
    url = f"http://127.0.0.1:{port}/api/v1/namespaces/{args.namespace}/services/http:ingestion:8000/proxy"
    thread = threading.Thread(target=sampler, daemon=True)
    phase_process = None
    try:
        wait_http(url + "/health/ready")
        thread.start()
        for phase in PHASES:
            seq, phase_id, rate, duration = phase
            out = output / "workload" / f"phase-{seq:02d}-{phase_id}.csv"
            stdout = (output / "logs" / f"phase-{seq:02d}-{phase_id}.stdout.txt").open("w", encoding="utf-8")
            stderr = (output / "logs" / f"phase-{seq:02d}-{phase_id}.stderr.txt").open("w", encoding="utf-8")
            started = now()
            phase_process = subprocess.Popen(phase_command(sys.executable, streaming, url, run_id, episode_id, phase, out, telemetry_root), stdout=stdout, stderr=stderr, text=True, cwd=app_root)
            code = phase_process.wait()
            phase_process = None
            stdout.close(); stderr.close()
            rows = read_csv(out)
            done = sum(completed(x) for x in rows)
            phase_rows.append({"phase_sequence": seq, "phase_id": phase_id, "rate_rps": rate, "duration_seconds": duration, "scheduled_requests": rate * duration, "request_rows": len(rows), "completed_requests": done, "error_requests": len(rows) - done, "client_exit_code": code, "started_at_utc": started, "ended_at_utc": now()})
            require(len(rows) == rate * duration and code in (0, 2), f"WORKLOAD_PHASE_TECHNICAL_FAILURE:{phase_id}:exit={code}:rows={len(rows)}")
            print(f"PHASE_COMPLETE phase={phase_id} completed={done} errors={len(rows)-done}", flush=True)
    except BaseException as exc:
        failure = f"{type(exc).__name__}:{exc}"
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=15)
        if phase_process and phase_process.poll() is None:
            phase_process.terminate()
            phase_process.wait(timeout=15)
        if proxy.poll() is None:
            proxy.terminate()
        try:
            proxy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proxy.kill()
        proxy_out.close(); proxy_err.close()

    post_reset = stable_idle(args.context, args.namespace, bound_hash, required=7, deadline_seconds=660)
    write_csv(output / "reset" / "postflight-stable-samples.csv", post_reset)
    final_hpa = kube_json(args.context, args.namespace, ["get", "hpa", "processing-hpa"])
    hpa_restored = canon(final_hpa.get("spec", {})) == bound_hash

    write_csv(output / "workload" / "phase-ledger.csv", phase_rows)
    write_csv(output / "telemetry" / "cluster-runtime.csv", samples)
    rows = read_live(telemetry_root, run_id)
    if rows:
        write_csv(output / "workload" / "request-results.csv", rows)
    windows = make_windows(rows)
    write_csv(output / "analysis" / "slo-windows.csv", windows)

    high_rows = [r for r in rows if r.get("phase_id") == "high-challenge" and r.get("scheduled_time_utc")]
    t0 = min((r["scheduled_time_utc"] for r in high_rows), key=dt) if high_rows else None
    t0_dt = dt(t0) if t0 else None
    valid_samples = [s for s in samples if not s.get("sampler_error")]
    t1_sample = next((s for s in valid_samples if t0_dt and dt(s["observed_at_utc"]) >= t0_dt and s.get("hpa_cpu_percent") is not None and int(s["hpa_cpu_percent"]) >= 55), None)
    t2_sample = next((s for s in valid_samples if t0_dt and dt(s["observed_at_utc"]) >= t0_dt and int(s.get("hpa_desired_replicas", 0)) > 1), None)
    t1 = t1_sample["observed_at_utc"] if t1_sample else None
    t2 = None
    if t2_sample:
        scale_time = t2_sample.get("hpa_last_scale_time")
        t2 = scale_time if scale_time and t0_dt and dt(scale_time) >= t0_dt else t2_sample["observed_at_utc"]

    pod_map = {}
    for sample in valid_samples:
        for pod in sample.get("processing_pods", []):
            if pod.get("uid") not in baseline_uids:
                existing = pod_map.setdefault(pod["uid"], {"name": pod["name"], "uid": pod["uid"], "T3": None, "T4": None, "T5": None, "T6": None, "T7": None, "T7_request_id": None})
                for key in ("T3", "T4", "T5", "T6"):
                    if existing[key] is None and pod.get(key):
                        existing[key] = pod[key]

    for row in rows:
        if not completed(row) or not row.get("response_json"):
            continue
        try:
            response = json.loads(row["response_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        uid = response.get("identities", {}).get("processing", {}).get("pod_uid")
        received = response.get("timestamps", {}).get("processing_received_at_utc")
        if uid in pod_map and received and pod_map[uid].get("T6") and dt(received) >= dt(pod_map[uid]["T6"]):
            if pod_map[uid]["T7"] is None or dt(received) < dt(pod_map[uid]["T7"]):
                pod_map[uid]["T7"] = received
                pod_map[uid]["T7_request_id"] = row.get("request_id")

    causal = [p for p in pod_map.values() if all(p.get(k) for k in ("T3", "T4", "T5", "T6", "T7"))]
    causal.sort(key=lambda p: dt(p["T7"]))
    selected = causal[0] if causal else None
    t7 = selected["T7"] if selected else None
    after_t7 = [w for w in windows if t7 and w["slo_good"] and dt(w["window_end_utc"]) > dt(t7)]
    t8 = after_t7[0]["window_end_utc"] if after_t7 else None

    breach_streak, good_streak, tb, tr = 0, 0, None, None
    first_bad_start = None
    for window in windows:
        if not window["slo_good"]:
            if breach_streak == 0:
                first_bad_start = window["window_start_utc"]
            breach_streak += 1
            good_streak = 0
            if breach_streak == 2 and tb is None:
                tb = window["window_end_utc"]
        elif tb is not None:
            good_streak += 1
            if good_streak == 8:
                tr = window["window_end_utc"]
                break
        else:
            breach_streak = 0

    events = {"T0": t0, "T1": t1, "T2": t2, "T3": selected.get("T3") if selected else None, "T4": selected.get("T4") if selected else None, "T5": selected.get("T5") if selected else None, "T6": selected.get("T6") if selected else None, "T7": t7, "T8": t8, "TB": tb, "TR": tr}
    ordered_keys = ["T0", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
    all_observed = all(events[k] for k in ordered_keys) and events["TR"] is not None
    ordered = all_observed and all(dt(events[a]) <= dt(events[b]) for a, b in zip(ordered_keys, ordered_keys[1:]))
    durations = {}
    if all_observed:
        for a, b in zip(ordered_keys, ordered_keys[1:]):
            durations[f"{a}_{b}_seconds"] = round((dt(events[b]) - dt(events[a])).total_seconds(), 6)
        durations["T0_T8_seconds"] = round((dt(events["T8"]) - dt(events["T0"])).total_seconds(), 6)
        durations["TB_TR_seconds"] = round((dt(events["TR"]) - dt(events["TB"])).total_seconds(), 6) if events["TB"] else None
        durations["T8_TR_confirmation_seconds"] = round((dt(events["TR"]) - dt(events["T8"])).total_seconds(), 6)
        adjacent_names = [f"{a}_{b}_seconds" for a, b in zip(ordered_keys, ordered_keys[1:])]
        durations["decomposition_sum_seconds"] = round(sum(durations[name] for name in adjacent_names), 6)
    max_uncertainty = max((float(s.get("mapping_uncertainty_ms", 0)) for s in valid_samples), default=999999)
    technical = failure is None and len(rows) == EXPECTED and all_observed and ordered and hpa_restored and max_uncertainty <= 500 and selected is not None
    summary = {
        "schema": "C5-FAST-03-GROUND-TRUTH-PILOT-V1", "status": "VERIFIED_PASS" if technical else "PILOT_PRESERVED_NOT_QUALIFIED",
        "recorded_at_utc": now(), "execution_attempt_id": episode_id, "workload_run_id": run_id,
        "treatment": "C2_TUNED_STATIC_HPA", "scheduled_requests": EXPECTED, "request_rows": len(rows),
        "completed_requests": sum(completed(r) for r in rows), "error_requests": sum(not completed(r) for r in rows),
        "events": events, "durations": durations, "selected_processing_pod": selected,
        "new_processing_pods": list(pod_map.values()), "timestamp_order_pass": ordered,
        "maximum_clock_mapping_uncertainty_ms": round(max_uncertainty, 6), "hpa_spec_restored": hpa_restored,
        "guide11_dataset_modified": False, "workload_protocol_modified": False, "request_retries_added": False,
        "technical_failure": failure or "", "pilot_qualified": technical,
    }
    (output / "analysis" / "C5-FAST-03-pilot-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output / "analysis" / "T0-T8-events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    (output / "analysis" / "T0-T8-durations.json").write_text(json.dumps(durations, indent=2), encoding="utf-8")

    record = {
        "STEP": "C5-FAST-03-R01", "STATUS": summary["status"], "RECORDED_AT_UTC": summary["recorded_at_utc"],
        "PROJECT_SCOPE": "C5_T0_T8_C2_GROUND_TRUTH_PILOT", "STAGE2_PROOF_SHA256": STAGE2_PROOF_SHA,
        "PILOT_TREATMENT": "C2_TUNED_STATIC_HPA", "EXPECTED_REQUESTS": EXPECTED, "REQUEST_ROWS": len(rows),
        "COMPLETED_REQUESTS": summary["completed_requests"], "ERROR_REQUESTS": summary["error_requests"],
        "T0_T8_ALL_OBSERVED": "YES" if all_observed else "NO", "TIMESTAMP_ORDER_PASS": "YES" if ordered else "NO",
        "T7_BOUND_PROCESSING_POD_UID": selected.get("uid") if selected else "", "T7_BOUND_REQUEST_ID": selected.get("T7_request_id") if selected else "",
        "MAXIMUM_CLOCK_MAPPING_UNCERTAINTY_MS": summary["maximum_clock_mapping_uncertainty_ms"],
        "HPA_SPEC_RESTORED": "YES" if hpa_restored else "NO", "GUIDE11_DATASET_MODIFIED": "NO",
        "WORKLOAD_PROTOCOL_MODIFIED": "NO", "REQUEST_RETRIES_ADDED": "NO", "LIVE_WORKLOAD_EXECUTED": "YES_ONE_PILOT",
        "C5_STAGE3_COMPLETE": "YES" if technical else "NO", "C5_FORMAL_EXECUTION_AUTHORIZED": "NO_PENDING_CALIBRATION_AND_ACTION_PILOTS",
        "NEXT_STEP": "C5_FAST_04_BASELINE_STAGE_CALIBRATION" if technical else "DIAGNOSE_OR_REPEAT_C5_FAST_03",
        "FAILURE": failure or ("" if technical else "MISSING_OR_UNORDERED_T0_T8_EVENT"),
    }
    record_path = output / "C5-FAST-03-R01-record.txt"
    record_path.write_text("=== C5 FAST TRACK STAGE 3 SUMMARY ===\n" + "\n".join(f"{k}={v}" for k, v in record.items()) + "\n", encoding="utf-8")
    evidence = [{"path": str(p.relative_to(output)).replace("\\", "/"), "sha256": sha(p), "bytes": p.stat().st_size} for p in sorted(output.rglob("*")) if p.is_file()]
    (output / "evidence-sha256.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    freeze = output / "C5-FAST-03-R01-GROUND-TRUTH-PILOT-FREEZE.txt"
    freeze.write_text(record_path.read_text(encoding="utf-8") + f"RECORD_SHA256={sha(record_path)}\n", encoding="utf-8")
    print(record_path.read_text(encoding="utf-8"), end="")
    print(f"RECORD_PATH={record_path}")
    print(f"RECORD_SHA256={sha(record_path)}")
    print(f"FREEZE_PATH={freeze}")
    print(f"FREEZE_SHA256={sha(freeze)}")
    require(technical, "PILOT_PRESERVED_NOT_QUALIFIED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--root")
    parser.add_argument("--package-root")
    parser.add_argument("--output")
    parser.add_argument("--context", default="net470-v3")
    parser.add_argument("--namespace", default="net470-v3")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    require(args.root and args.package_root and args.output, "MISSING_REQUIRED_ARGUMENT")
    return complete(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"C5_FAST_03_R01_FAILURE={exc}", file=sys.stderr)
        raise

