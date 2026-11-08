#!/usr/bin/env python3
"""C5 Stage 4: offline protocol, calibration, and C5 contract freeze."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

STAGE3_PROOF_SHA = "882D7E6711EACE57182BDC56D2048882EE0C0B69340592A09A56A531D4290C30"
STAGE3_RECORD_SHA = "A7B32843E33201F63B5AC455CD85909FD564825517D8A9F9B1A4DD2D28530E54"
STAGE3_FREEZE_SHA = "E9F30B4B8C27A2AC439FA04848B6C9C115626A80773F6DF30DEF0322CC9B983A"
RANDOMIZATION_SEED = 1505
EXPECTED_REQUESTS = 6450


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def canonical_hash(value) -> str:
    return sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def parse_record(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def validate_stage3_proof(path: Path) -> dict:
    require(path.is_file(), f"STAGE3_PROOF_MISSING:{path}")
    require(sha(path) == STAGE3_PROOF_SHA, "STAGE3_PROOF_HASH_MISMATCH")
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        require(bad is None, f"STAGE3_PROOF_BAD_ENTRY:{bad}")
        names = archive.namelist()
        records = [name for name in names if name.endswith("C5-FAST-03-R02-record.txt")]
        freezes = [name for name in names if name.endswith("C5-FAST-03-R02-OFFLINE-REANALYSIS-FREEZE.txt")]
        manifests = [name for name in names if name.endswith("C5-FAST-03-R02-SHA256.csv")]
        require(len(records) == 1, f"STAGE3_RECORD_COUNT:{len(records)}")
        require(len(freezes) == 1, f"STAGE3_FREEZE_COUNT:{len(freezes)}")
        require(len(manifests) == 1, f"STAGE3_MANIFEST_COUNT:{len(manifests)}")
        record_bytes = archive.read(records[0])
        freeze_bytes = archive.read(freezes[0])
        require(sha_bytes(record_bytes) == STAGE3_RECORD_SHA, "STAGE3_RECORD_HASH_MISMATCH")
        require(sha_bytes(freeze_bytes) == STAGE3_FREEZE_SHA, "STAGE3_FREEZE_HASH_MISMATCH")
        record = parse_record(record_bytes.decode("utf-8-sig"))
    expected = {
        "STEP": "C5-FAST-03-R02",
        "STATUS": "VERIFIED_PASS",
        "REQUEST_ROWS": "6450",
        "COMPLETED_REQUESTS": "6450",
        "ERROR_REQUESTS": "0",
        "T0_T8_ALL_OBSERVED": "YES",
        "TIMESTAMP_ORDER_PASS": "YES_INTERVAL_CENSORED_CAUSAL_TIMELINE",
        "R01_EVIDENCE_MODIFIED": "NO",
        "GUIDE11_DATASET_MODIFIED": "NO",
        "CLUSTER_QUERIED_BY_THIS_STEP": "NO",
        "LIVE_WORKLOAD_EXECUTED_BY_THIS_STEP": "NO",
        "C5_STAGE3_COMPLETE": "YES",
    }
    for key, value in expected.items():
        require(record.get(key) == value, f"STAGE3_RECORD_VALUE_MISMATCH:{key}:{record.get(key)!r}:{value!r}")
    return {"record": record, "archive_entries": len(names)}


def treatment_matrix() -> dict:
    return {
        "schema": "C5-TREATMENT-MATRIX-V1",
        "conditions": [
            {"code": "C1", "name": "REFERENCE_STATIC_HPA", "cpu_target_percent": 60, "diagnosis": False, "action": "STANDARD_HPA"},
            {"code": "C2", "name": "TUNED_STATIC_HPA", "cpu_target_percent": 55, "diagnosis": False, "action": "STANDARD_HPA"},
            {"code": "C3", "name": "DIAGNOSIS_ONLY", "cpu_target_percent": 55, "diagnosis": True, "action": "RECOMMENDATION_ONLY"},
            {"code": "C4", "name": "GUARDED_ADAPTIVE", "cpu_target_percent": 55, "diagnosis": "CPU_SLO_GUARDED", "action": "BOUNDED_HPA_MIN_FLOOR"},
            {"code": "C5", "name": "RECOVERY_STAGE_AWARE_PROJECT_SCOPE_RBAAC", "cpu_target_percent": 55, "diagnosis": "T0_T8_STAGE_BUDGET", "action": "UTILITY_SELECTED_BOUNDED_HPA_MIN_FLOOR"},
        ],
        "common_hpa_max_replicas": 5,
        "common_ingestion_replicas": 5,
        "request_retries": 0,
        "workload": [
            {"phase": "low-precondition", "rps": 5, "seconds": 60, "requests": 300},
            {"phase": "high-challenge", "rps": 25, "seconds": 180, "requests": 4500},
            {"phase": "low-recovery", "rps": 5, "seconds": 300, "requests": 1500},
            {"phase": "post-observation", "rps": 5, "seconds": 30, "requests": 150},
        ],
        "expected_requests_per_episode": EXPECTED_REQUESTS,
        "pilot_data_in_formal_dataset": False,
    }


def boundary_contract() -> dict:
    return {
        "schema": "C5-T0-T8-BOUNDARY-CONTRACT-V2",
        "clock_policy": "DUAL_TIMESTAMP_SOURCE_AND_OBSERVATION_WITH_INTERVAL_CENSORING",
        "boundaries": [
            {"id": "T0", "definition": "first scheduled high-challenge request", "source": "client scheduled_time_utc", "point": True},
            {"id": "T1", "definition": "relevant metric becomes available to HPA", "source": "last negative poll, first positive poll, and HPA action bound", "point": False, "representation": "interval_censored"},
            {"id": "T2", "definition": "HPA scale decision", "source": "HPA status.lastScaleTime plus first desired-replica observation", "point": True},
            {"id": "T3", "definition": "new Processing Pod object created", "source": "Pod metadata.creationTimestamp", "point": True},
            {"id": "T4", "definition": "same Pod scheduled", "source": "PodScheduled lastTransitionTime", "point": True},
            {"id": "T5", "definition": "same Pod container started", "source": "container state.running.startedAt", "point": True},
            {"id": "T6", "definition": "same Pod Ready", "source": "Ready lastTransitionTime", "point": True},
            {"id": "T7", "definition": "same new Pod serves a completed request", "source": "response processing pod UID and processing_received_at_utc", "point": True},
            {"id": "T8", "definition": "end of first complete compliant 15-second window after T7", "source": "phase-relative SLO windows", "point": True},
        ],
        "breach_boundary": {"id": "TB", "definition": "end of second consecutive bad complete window"},
        "sustained_recovery_boundary": {"id": "TR", "definition": "end of eighth consecutive good complete window after TB"},
        "theta_ms": 123.472,
        "window_seconds": 15,
        "right_censor_horizon_seconds": 300,
        "strict_rules": [
            "T3 through T7 must use one Processing Pod UID",
            "source timestamps are never replaced by collector observations",
            "interval-censored boundaries are not converted to fabricated point times",
            "T0-T8 decomposition and TB-TR recovery remain separate endpoints",
        ],
    }


def calibration_protocol() -> dict:
    episodes = [
        {"sequence": i, "calibration_episode_id": f"C5-CAL-C2-E{i:02d}", "treatment": "C2", "expected_requests": EXPECTED_REQUESTS}
        for i in range(1, 11)
    ]
    return {
        "schema": "C5-STAGE-BUDGET-CALIBRATION-PROTOCOL-V1",
        "calibration_treatment": "C2_TUNED_STATIC_HPA",
        "episodes": episodes,
        "accepted_episodes_required": 10,
        "total_planned_requests": 10 * EXPECTED_REQUESTS,
        "formal_dataset_reuse": False,
        "budget_estimator": "median_plus_3_MAD",
        "interval_rule": "use conservative upper duration bound when a component includes T1",
        "zero_mad_rule": "retain observed median and disclose zero dispersion; do not invent variance",
        "acceptance": [
            "6450 request rows",
            "HPA specification restored",
            "no Processing or Ingestion restart increase",
            "minimum four Ingestion ready endpoints",
            "T0-T8 causal compatibility",
            "clock uncertainty no greater than 500 ms",
        ],
        "technical_exclusions_preserved": True,
    }


def controller_contract() -> dict:
    return {
        "schema": "C5-PROJECT-SCOPE-RBAAC-CONTROLLER-CONTRACT-V1",
        "condition": "C5",
        "implemented_scope": "stage-aware diagnosis and utility-gated bounded HPA minReplicas control",
        "claim_boundary": "not a general microservice controller and not full production RBAAC",
        "if_then_else": {
            "if": [
                "active stage exceeds its frozen calibrated budget",
                "supporting evidence confirms the same active stage",
                "the candidate action can still affect the current episode",
                "safety, restart, endpoint, error, warning, resource, and cooldown guards pass",
                "estimated benefit minus weighted resource cost reaches the frozen utility threshold",
            ],
            "then": "select the highest-utility feasible bounded HPA minReplicas candidate; ties choose lower cost then fixed identifier",
            "else": "HOLD and record the exact failed predicate",
        },
        "action_catalog": [
            {"id": "HOLD", "mutation": False},
            {"id": "HPA_MIN_TO_2", "field": "spec.minReplicas", "value": 2},
            {"id": "HPA_MIN_TO_3", "field": "spec.minReplicas", "value": 3},
            {"id": "HPA_MIN_TO_4", "field": "spec.minReplicas", "value": 4},
            {"id": "HPA_MIN_TO_5", "field": "spec.minReplicas", "value": 5},
        ],
        "prohibited_mutations": [
            "Deployment spec.replicas",
            "container images or application code",
            "workload protocol or request retries",
            "HPA maxReplicas or CPU target during a formal episode",
        ],
        "restoration": "exact pre-episode HPA spec restored in finally and verified stable",
        "stage_actionability": {
            "T0_T1": "diagnose; action disabled until metric support exists",
            "T1_T2": "bounded HPA floor candidates may be evaluated",
            "T2_T3": "HOLD for current scale decision; monitor",
            "T3_T4": "HOLD; no scheduler mutation in project scope",
            "T4_T5": "HOLD; no image or runtime mutation in project scope",
            "T5_T6": "HOLD; no probe mutation in project scope",
            "T6_T7": "HOLD; verify traffic activation",
            "T7_T8": "bounded HPA floor candidates may preserve useful capacity if all guards pass",
        },
        "live_action_enabled": False,
        "enablement_requires": ["ten accepted C2 calibration episodes", "offline fitted budget freeze", "matched C2-C5 action pilots", "live read-only binding"],
    }


def formal_schedule() -> list[dict]:
    rng = random.Random(RANDOMIZATION_SEED)
    rows = []
    for block in range(1, 11):
        order = ["C1", "C2", "C3", "C4", "C5"]
        rng.shuffle(order)
        for position, treatment in enumerate(order, 1):
            rows.append({"block": block, "position": position, "treatment": treatment, "planned_episode_id": f"C5-FORMAL-B{block:02d}-{treatment}", "expected_requests": EXPECTED_REQUESTS})
    return rows


def contrasts() -> list[dict]:
    return [
        {"order": 1, "name": "PRIMARY_FULL_STAGE_AWARE_VS_TUNED", "contrast": "C5_MINUS_C2", "direction": "negative_favors_C5"},
        {"order": 2, "name": "FULL_STAGE_AWARE_VS_GUARDED_ADAPTIVE", "contrast": "C5_MINUS_C4", "direction": "negative_favors_C5"},
        {"order": 3, "name": "GUARDED_ADAPTIVE_VS_TUNED_CONTINUITY", "contrast": "C4_MINUS_C2", "direction": "negative_favors_C4"},
        {"order": 4, "name": "DIAGNOSIS_ONLY_VS_TUNED", "contrast": "C3_MINUS_C2", "direction": "negative_favors_C3"},
        {"order": 5, "name": "TUNED_VS_REFERENCE", "contrast": "C2_MINUS_C1", "direction": "negative_favors_C2"},
    ]


def self_test() -> None:
    matrix = treatment_matrix()
    require([x["code"] for x in matrix["conditions"]] == ["C1", "C2", "C3", "C4", "C5"], "SELF_TEST_TREATMENTS")
    require(sum(x["requests"] for x in matrix["workload"]) == EXPECTED_REQUESTS, "SELF_TEST_REQUESTS")
    require(len(boundary_contract()["boundaries"]) == 9, "SELF_TEST_BOUNDARIES")
    require(len(calibration_protocol()["episodes"]) == 10, "SELF_TEST_CALIBRATION")
    schedule = formal_schedule()
    require(len(schedule) == 50, "SELF_TEST_FORMAL_EPISODES")
    for block in range(1, 11):
        require({x["treatment"] for x in schedule if x["block"] == block} == {"C1", "C2", "C3", "C4", "C5"}, f"SELF_TEST_BLOCK:{block}")
    require(len(contrasts()) == 5, "SELF_TEST_CONTRASTS")
    print("C5_CLASSFAST_04_R01_SELF_TEST=PASS")


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def complete(proof: Path, output: Path) -> None:
    validation = validate_stage3_proof(proof)
    output.mkdir(parents=True, exist_ok=False)
    configs = output / "contracts"
    configs.mkdir()
    matrix = treatment_matrix()
    boundaries = boundary_contract()
    calibration = calibration_protocol()
    controller = controller_contract()
    schedule = formal_schedule()
    planned_contrasts = contrasts()
    paths = {
        "treatment": configs / "C5-treatment-matrix-v1.json",
        "boundaries": configs / "C5-T0-T8-boundary-contract-v2.json",
        "calibration": configs / "C5-stage-budget-calibration-protocol-v1.json",
        "controller": configs / "C5-controller-contract-v1.json",
        "schedule": configs / "C5-five-condition-formal-schedule.csv",
        "contrasts": configs / "C5-preplanned-contrasts.csv",
    }
    write_json(paths["treatment"], matrix)
    write_json(paths["boundaries"], boundaries)
    write_json(paths["calibration"], calibration)
    write_json(paths["controller"], controller)
    write_csv(paths["schedule"], schedule)
    write_csv(paths["contrasts"], planned_contrasts)
    record = {
        "STEP": "C5-FAST-04-R01",
        "STATUS": "VERIFIED_PASS",
        "RECORDED_AT_UTC": now(),
        "PROJECT_SCOPE": "C5_FULL_STAGE_AWARE_EVALUATION_PROTOCOL_AND_CALIBRATION_FREEZE",
        "STAGE3_PROOF_SHA256": STAGE3_PROOF_SHA,
        "STAGE3_PROOF_ENTRIES": validation["archive_entries"],
        "STAGE3_RECORD_SHA256": STAGE3_RECORD_SHA,
        "STAGE3_FREEZE_SHA256": STAGE3_FREEZE_SHA,
        "TREATMENTS": 5,
        "TREATMENT_CODES": "C1;C2;C3;C4;C5",
        "C5_CONDITION": "RECOVERY_STAGE_AWARE_PROJECT_SCOPE_RBAAC",
        "T0_T8_BOUNDARIES": 9,
        "T1_SEMANTICS": "INTERVAL_CENSORED",
        "CALIBRATION_TREATMENT": "C2_TUNED_STATIC_HPA",
        "CALIBRATION_ACCEPTED_EPISODES_REQUIRED": 10,
        "CALIBRATION_PLANNED_REQUESTS": 10 * EXPECTED_REQUESTS,
        "BUDGET_ESTIMATOR": "MEDIAN_PLUS_3_MAD",
        "FORMAL_BLOCKS_PLANNED": 10,
        "FORMAL_EPISODES_PLANNED": 50,
        "FORMAL_REQUESTS_PLANNED": 50 * EXPECTED_REQUESTS,
        "RANDOMIZATION_SEED": RANDOMIZATION_SEED,
        "PRIMARY_CONTRAST": "C5_MINUS_C2",
        "MULTIPLICITY_CONTROL": "HOLM_FIVE_PREPLANNED_CONTRASTS",
        "TREATMENT_MATRIX_SHA256": sha(paths["treatment"]),
        "BOUNDARY_CONTRACT_SHA256": sha(paths["boundaries"]),
        "CALIBRATION_PROTOCOL_SHA256": sha(paths["calibration"]),
        "C5_CONTROLLER_CONTRACT_SHA256": sha(paths["controller"]),
        "FORMAL_SCHEDULE_SHA256": sha(paths["schedule"]),
        "PREPLANNED_CONTRASTS_SHA256": sha(paths["contrasts"]),
        "GUIDE11_DATASET_MODIFIED": "NO",
        "CLUSTER_QUERIED_BY_THIS_STEP": "NO",
        "CLUSTER_MODIFIED_BY_THIS_STEP": "NO",
        "LIVE_WORKLOAD_EXECUTED_BY_THIS_STEP": "NO",
        "C5_STAGE4_COMPLETE": "YES",
        "C5_LIVE_CALIBRATION_AUTHORIZED": "NO_PENDING_STAGE5_LIVE_READ_ONLY_BINDING",
        "C5_FORMAL_EXECUTION_AUTHORIZED": "NO",
        "NEXT_STEP": "C5_FAST_05_LIVE_READ_ONLY_BINDING_FOR_C2_STAGE_BUDGET_CALIBRATION",
        "FAILURE": "",
    }
    record_path = output / "C5-FAST-04-R01-record.txt"
    record_path.write_text("=== C5 FAST TRACK STAGE 4 SUMMARY ===\n" + "\n".join(f"{k}={v}" for k, v in record.items()) + "\n", encoding="utf-8")
    freeze_path = output / "C5-FAST-04-R01-C5-PROTOCOL-AND-CALIBRATION-FREEZE.txt"
    freeze_path.write_text(record_path.read_text(encoding="utf-8") + f"RECORD_SHA256={sha(record_path)}\n", encoding="utf-8")
    evidence = [{"path": str(path.relative_to(output)).replace("\\", "/"), "sha256": sha(path), "bytes": path.stat().st_size} for path in sorted(output.rglob("*")) if path.is_file()]
    write_json(output / "evidence-sha256.json", evidence)
    print(record_path.read_text(encoding="utf-8"), end="")
    print(f"RECORD_PATH={record_path}")
    print(f"RECORD_SHA256={sha(record_path)}")
    print(f"FREEZE_PATH={freeze_path}")
    print(f"FREEZE_SHA256={sha(freeze_path)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--stage3-proof")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(bool(args.stage3_proof and args.output), "STAGE3_PROOF_AND_OUTPUT_REQUIRED")
    complete(Path(args.stage3_proof).resolve(), Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"C5_CLASSFAST_04_R01_FAILURE={exc}", file=sys.stderr)
        raise
