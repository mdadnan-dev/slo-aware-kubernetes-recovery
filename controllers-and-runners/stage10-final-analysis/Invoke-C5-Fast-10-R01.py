#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import statistics
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

STAGE9_RECORD_SHA = "3E94A5F263BE110A7396A393B500795FC02899249F66DE9C73407675417C6A0B"
STAGE9_FREEZE_SHA = "9A60F3F367DA806C07CD731F488B25956EB5CA1BC5B6D9A4A1F180D8B80D5C03"
STAGE8_BINDING_SHA = "64758ED6A36688D4EF5A8701312261B58BB649E64A3B3E64F4BB3C82354D61F3"
TAU = 300.0
TREATMENTS = ["C1", "C2", "C3", "C4", "C5"]
EVENTS = [f"T{i}" for i in range(9)]
CONTRASTS = [
    (1, "PRIMARY_FULL_STAGE_AWARE_VS_TUNED", "C5_MINUS_C2", "C5", "C2"),
    (2, "FULL_STAGE_AWARE_VS_GUARDED_ADAPTIVE", "C5_MINUS_C4", "C5", "C4"),
    (3, "GUARDED_ADAPTIVE_VS_TUNED_CONTINUITY", "C4_MINUS_C2", "C4", "C2"),
    (4, "DIAGNOSIS_ONLY_VS_TUNED", "C3_MINUS_C2", "C3", "C2"),
    (5, "TUNED_VS_REFERENCE", "C2_MINUS_C1", "C2", "C1"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def seconds(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return (parse_time(b) - parse_time(a)).total_seconds()


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    require(bool(rows), f"NO_ROWS_FOR:{path.name}")
    fields = fields or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def mean(values: list[float]) -> float:
    return statistics.fmean(values)


def sample_sd(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def exact_sign_flip_p(values: list[float]) -> float:
    observed = abs(mean(values))
    total = 2 ** len(values)
    extreme = 0
    eps = 1e-12
    for signs in itertools.product((-1.0, 1.0), repeat=len(values)):
        statistic = abs(mean([s * x for s, x in zip(signs, values)]))
        if statistic + eps >= observed:
            extreme += 1
    return extreme / total


def holm_adjust(rows: list[dict]) -> None:
    ordered = sorted(enumerate(rows), key=lambda x: x[1]["exact_sign_flip_p_value"])
    running = 0.0
    m = len(rows)
    adjusted: dict[int, float] = {}
    for rank, (index, row) in enumerate(ordered):
        candidate = min(1.0, (m - rank) * row["exact_sign_flip_p_value"])
        running = max(running, candidate)
        adjusted[index] = running
    for index, row in enumerate(rows):
        row["holm_adjusted_p_value"] = adjusted[index]
        row["holm_reject_at_0_05"] = adjusted[index] <= 0.05


def km_curve(values: list[tuple[float, bool]]) -> tuple[list[dict], float]:
    # bool=True means observed event; False means right-censored.
    at_risk = len(values)
    survival = 1.0
    previous = 0.0
    rmst = 0.0
    curve = [{"time_seconds": 0.0, "survival_probability": 1.0, "at_risk": at_risk,
              "events": 0, "censored": 0}]
    grouped: dict[float, list[bool]] = {}
    for time_value, observed in values:
        grouped.setdefault(min(float(time_value), TAU), []).append(observed)
    for time_value in sorted(grouped):
        rmst += survival * (time_value - previous)
        statuses = grouped[time_value]
        events = sum(statuses)
        censored = len(statuses) - events
        if events and at_risk:
            survival *= (1.0 - events / at_risk)
        curve.append({"time_seconds": time_value, "survival_probability": survival,
                      "at_risk": at_risk, "events": events, "censored": censored})
        at_risk -= len(statuses)
        previous = time_value
    if previous < TAU:
        rmst += survival * (TAU - previous)
    return curve, rmst


def svg_bar(path: Path, title: str, labels: list[str], values: list[float], ylabel: str) -> None:
    width, height = 900, 520
    left, right, top, bottom = 90, 30, 70, 90
    plot_w, plot_h = width - left - right, height - top - bottom
    maximum = max(values) if values else 1.0
    maximum = maximum if maximum > 0 else 1.0
    step = plot_w / len(values)
    bars = []
    for i, (label, value) in enumerate(zip(labels, values)):
        bar_w = step * 0.58
        x = left + i * step + (step - bar_w) / 2
        bar_h = value / maximum * (plot_h * 0.90)
        y = top + plot_h - bar_h
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="#2463a6"/>')
        bars.append(f'<text x="{x + bar_w/2:.1f}" y="{y - 8:.1f}" text-anchor="middle" font-size="14">{value:.2f}</text>')
        bars.append(f'<text x="{x + bar_w/2:.1f}" y="{top + plot_h + 26:.1f}" text-anchor="middle" font-size="14">{label}</text>')
    content = "\n".join(bars)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/>
<text x="{width/2}" y="34" text-anchor="middle" font-family="Arial" font-size="22" font-weight="bold">{title}</text>
<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="black"/>
<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="black"/>
<text x="20" y="{top+plot_h/2}" transform="rotate(-90 20 {top+plot_h/2})" text-anchor="middle" font-family="Arial" font-size="15">{ylabel}</text>
<g font-family="Arial">{content}</g>
</svg>'''
    path.write_text(svg, encoding="utf-8")


def svg_km(path: Path, curves: dict[str, list[dict]]) -> None:
    width, height = 900, 540
    left, right, top, bottom = 90, 40, 60, 80
    pw, ph = width-left-right, height-top-bottom
    colors = {"C1":"#1f77b4", "C2":"#ff7f0e", "C3":"#2ca02c", "C4":"#d62728", "C5":"#9467bd"}
    parts = []
    for treatment, rows in curves.items():
        points = []
        prior_x, prior_y = left, top
        for row in rows:
            x = left + float(row["time_seconds"]) / TAU * pw
            y = top + (1.0-float(row["survival_probability"])) * ph
            points.extend([(prior_x, prior_y), (x, prior_y), (x, y)])
            prior_x, prior_y = x, y
        points.extend([(prior_x, prior_y), (left+pw, prior_y)])
        poly = " ".join(f"{x:.1f},{y:.1f}" for x,y in points)
        parts.append(f'<polyline points="{poly}" fill="none" stroke="{colors[treatment]}" stroke-width="3"/>')
    legends = "".join(f'<line x1="{left+i*140}" y1="{height-28}" x2="{left+25+i*140}" y2="{height-28}" stroke="{colors[t]}" stroke-width="4"/><text x="{left+32+i*140}" y="{height-23}" font-size="14">{t}</text>' for i,t in enumerate(TREATMENTS))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/><text x="{width/2}" y="32" text-anchor="middle" font-family="Arial" font-size="22" font-weight="bold">Recovery survival curves (300-second horizon)</text>
<line x1="{left}" y1="{top}" x2="{left}" y2="{top+ph}" stroke="black"/><line x1="{left}" y1="{top+ph}" x2="{left+pw}" y2="{top+ph}" stroke="black"/>
<text x="{width/2}" y="{height-48}" text-anchor="middle" font-family="Arial" font-size="15">Recovery time (seconds)</text>
<text x="22" y="{top+ph/2}" transform="rotate(-90 22 {top+ph/2})" text-anchor="middle" font-family="Arial" font-size="15">Probability recovery not yet observed</text>
<g font-family="Arial">{''.join(parts)}{legends}</g></svg>'''
    path.write_text(svg, encoding="utf-8")


def validate_and_load(stage9: Path) -> tuple[list[dict], list[dict]]:
    record = stage9 / "C5-FAST-09-R07-record.txt"
    freeze = stage9 / "C5-FAST-09-R07-FORMAL-C1-C5-EXECUTION-FREEZE.txt"
    primary = stage9 / "C5-formal-primary-dataset.csv"
    amendment = stage9 / "C5-FAST-09-R07-selection-amendment.json"
    for path in (record, freeze, primary, amendment):
        require(path.is_file(), f"MISSING_STAGE9_FILE:{path.name}")
    require(sha(record) == STAGE9_RECORD_SHA, "STAGE9_RECORD_HASH_MISMATCH")
    require(sha(freeze) == STAGE9_FREEZE_SHA, "STAGE9_FREEZE_HASH_MISMATCH")
    require("STAGE8_R02_BINDING_SHA256=" + STAGE8_BINDING_SHA in record.read_text(encoding="utf-8"), "STAGE8_BINDING_NOT_BOUND")
    primary_rows = list(csv.DictReader(primary.open(encoding="utf-8-sig")))
    require(len(primary_rows) == 50, f"PRIMARY_DATASET_ROWS:{len(primary_rows)}")
    selected = json.loads(amendment.read_text(encoding="utf-8"))["selected"]
    require(len(selected) == 50, f"AMENDMENT_SELECTED_ROWS:{len(selected)}")
    key_primary = {(int(r["block"]), r["treatment"]): r for r in primary_rows}
    key_selected = {(int(r["block"]), r["treatment"]): r for r in selected}
    require(len(key_primary) == len(key_selected) == 50, "DUPLICATE_BLOCK_TREATMENT")
    require(set(key_primary) == set(key_selected), "PRIMARY_AMENDMENT_KEY_MISMATCH")
    for block in range(1, 11):
        require({t for b,t in key_primary if b == block} == set(TREATMENTS), f"UNBALANCED_BLOCK:{block}")
    normalized = []
    event_rows = []
    for key in sorted(key_selected):
        selected_row = key_selected[key]
        primary_row = key_primary[key]
        require(selected_row["execution_attempt_id"] == primary_row["execution_attempt_id"], f"ATTEMPT_SELECTION_MISMATCH:{key}")
        attempt_id = selected_row["execution_attempt_id"]
        attempt = stage9 / "episodes" / attempt_id
        formal_path = attempt / "analysis" / "C5-FAST-09-formal-summary.json"
        core_path = attempt / "analysis" / "C5-FAST-03-pilot-summary.json"
        request_path = attempt / "workload" / "request-results.csv"
        require(formal_path.is_file() and core_path.is_file() and request_path.is_file(), f"MISSING_SELECTED_ATTEMPT_EVIDENCE:{attempt_id}")
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
        core = json.loads(core_path.read_text(encoding="utf-8"))
        require(int(core.get("request_rows", -1)) == 6450, f"REQUEST_ROWS_INVALID:{attempt_id}")
        require(bool(formal.get("hpa_restored")), f"HPA_NOT_RESTORED:{attempt_id}")
        require(not formal.get("core_timed_out"), f"CORE_TIMED_OUT:{attempt_id}")
        uncertainty = float(core["maximum_clock_mapping_uncertainty_ms"])
        require(uncertainty <= 500, f"CLOCK_UNCERTAINTY_INVALID:{attempt_id}:{uncertainty}")
        request_rows = list(csv.DictReader(request_path.open(encoding="utf-8-sig")))
        completed_count = sum(str(r.get("outcome", "")).lower() == "completed" for r in request_rows)
        error_count = len(request_rows) - completed_count
        require(len(request_rows) == 6450, f"REQUEST_FILE_ROWS_INVALID:{attempt_id}:{len(request_rows)}")
        require(completed_count == int(primary_row["completed_requests"]), f"COMPLETED_REQUEST_MISMATCH:{attempt_id}")
        require(error_count == int(primary_row["error_requests"]), f"ERROR_REQUEST_MISMATCH:{attempt_id}")
        require(completed_count == int(core.get("completed_requests", -1)), f"CORE_COMPLETED_MISMATCH:{attempt_id}")
        require(error_count == int(core.get("error_requests", -1)), f"CORE_ERROR_MISMATCH:{attempt_id}")
        right_censored = str(primary_row["right_censored"]).lower() == "true"
        recovery = min(float(primary_row["recovery_seconds"]), TAU)
        events = core.get("events") or {}
        normalized.append({
            "block": key[0], "treatment": key[1], "execution_attempt_id": attempt_id,
            "outcome": primary_row["outcome"], "right_censored": right_censored,
            "event_observed": not right_censored, "recovery_seconds": recovery,
            "rmst_contribution_300_seconds": recovery,
            "scheduled_requests": int(primary_row["scheduled_requests"]),
            "completed_requests": int(primary_row["completed_requests"]),
            "error_requests": int(primary_row["error_requests"]),
            "maximum_clock_mapping_uncertainty_ms": uncertainty,
            "controller_actions": int(primary_row["controller_actions"]),
            "controller_recommendations": int(primary_row["controller_recommendations"]),
            "source_formal_sha256": sha(formal_path), "source_core_sha256": sha(core_path),
            "source_request_results_sha256": sha(request_path),
        })
        erow = {"block": key[0], "treatment": key[1], "execution_attempt_id": attempt_id}
        for event in EVENTS + ["TB", "TR"]:
            erow[event] = events.get(event) or ""
        event_rows.append(erow)
    return normalized, event_rows


def analyze(stage9: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    analysis = output / "analysis"; figures = output / "figures"; proof = output / "proof"
    analysis.mkdir(); figures.mkdir(); proof.mkdir()
    rows, event_rows = validate_and_load(stage9)
    write_csv(analysis / "C5-normalized-formal-dataset.csv", rows)
    write_csv(analysis / "C5-event-matrix.csv", event_rows)

    treatment_summary = []
    curves: dict[str, list[dict]] = {}
    curve_rows = []
    for treatment in TREATMENTS:
        group = [r for r in rows if r["treatment"] == treatment]
        values = [(r["recovery_seconds"], bool(r["event_observed"])) for r in group]
        curve, rmst = km_curve(values); curves[treatment] = curve
        for item in curve: curve_rows.append({"treatment": treatment, **item})
        treatment_summary.append({
            "treatment": treatment, "episodes": len(group),
            "observed_recoveries": sum(r["event_observed"] for r in group),
            "right_censored": sum(r["right_censored"] for r in group),
            "rmst_300_seconds": round(rmst, 6),
            "mean_completed_requests": round(mean([r["completed_requests"] for r in group]), 3),
            "total_errors": sum(r["error_requests"] for r in group),
            "request_error_rate_percent": round(100.0 * sum(r["error_requests"] for r in group) / sum(r["scheduled_requests"] for r in group), 8),
            "controller_actions": sum(r["controller_actions"] for r in group),
        })
    write_csv(analysis / "C5-treatment-summary.csv", treatment_summary)
    write_csv(analysis / "C5-kaplan-meier-curves.csv", curve_rows)

    by_key = {(r["block"], r["treatment"]): r for r in rows}
    contrast_rows = []
    paired_rows = []
    for order, name, contrast, lhs, rhs in CONTRASTS:
        differences = []
        for block in range(1, 11):
            left = by_key[(block, lhs)]["rmst_contribution_300_seconds"]
            right = by_key[(block, rhs)]["rmst_contribution_300_seconds"]
            difference = left - right
            differences.append(difference)
            paired_rows.append({"contrast_order": order, "contrast": contrast, "block": block,
                                "lhs_treatment": lhs, "lhs_seconds": left, "rhs_treatment": rhs,
                                "rhs_seconds": right, "paired_difference_seconds": difference})
        avg = mean(differences); sd = sample_sd(differences); se = sd / math.sqrt(len(differences))
        margin = 2.2621571627409915 * se
        contrast_rows.append({
            "order": order, "name": name, "contrast": contrast, "lhs": lhs, "rhs": rhs,
            "blocks": len(differences), "mean_paired_difference_seconds": round(avg, 6),
            "sample_sd_seconds": round(sd, 6), "standard_error_seconds": round(se, 6),
            "ci_95_lower_seconds": round(avg-margin, 6), "ci_95_upper_seconds": round(avg+margin, 6),
            "exact_sign_flip_p_value": exact_sign_flip_p(differences),
            "direction": f"negative_favors_{lhs}",
        })
    holm_adjust(contrast_rows)
    write_csv(analysis / "C5-block-paired-differences.csv", paired_rows)
    write_csv(analysis / "C5-preplanned-contrast-results.csv", contrast_rows)

    stage_rows = []
    for event_row in event_rows:
        for i in range(1, 9):
            start, end = f"T{i-1}", f"T{i}"
            duration = seconds(event_row[start], event_row[end])
            stage_rows.append({"block": event_row["block"], "treatment": event_row["treatment"],
                               "execution_attempt_id": event_row["execution_attempt_id"],
                               "stage": f"{start}_{end}", "start_event": start, "end_event": end,
                               "duration_seconds": "" if duration is None else round(duration, 6),
                               "observed": duration is not None})
    write_csv(analysis / "C5-T0-T8-stage-durations.csv", stage_rows)
    stage_summary = []
    for treatment in TREATMENTS:
        for i in range(1, 9):
            stage = f"T{i-1}_T{i}"
            values = [float(r["duration_seconds"]) for r in stage_rows if r["treatment"] == treatment and r["stage"] == stage and r["observed"]]
            stage_summary.append({"treatment": treatment, "stage": stage, "observed_n": len(values),
                                  "missing_or_censored_n": 10-len(values),
                                  "mean_seconds": "" if not values else round(mean(values), 6),
                                  "median_seconds": "" if not values else round(statistics.median(values), 6),
                                  "minimum_seconds": "" if not values else round(min(values), 6),
                                  "maximum_seconds": "" if not values else round(max(values), 6)})
    write_csv(analysis / "C5-T0-T8-stage-summary.csv", stage_summary)

    rmst_map = {r["treatment"]: float(r["rmst_300_seconds"]) for r in treatment_summary}
    error_map = {r["treatment"]: float(r["request_error_rate_percent"]) for r in treatment_summary}
    svg_bar(figures / "C5-RMST-300-by-treatment.svg", "RMST at 300 seconds by treatment", TREATMENTS,
            [rmst_map[t] for t in TREATMENTS], "RMST (seconds; lower is better)")
    svg_bar(figures / "C5-request-error-rate-by-treatment.svg", "Request error rate by treatment", TREATMENTS,
            [error_map[t] for t in TREATMENTS], "Error rate (%)")
    svg_km(figures / "C5-recovery-survival-curves.svg", curves)

    primary = contrast_rows[0]
    conclusion = {
        "schema": "C5-FAST-10-R01-CONCLUSION-V1", "generated_at_utc": now(),
        "primary_contrast": "C5_MINUS_C2", "estimand": "paired block difference in RMST(300)",
        "mean_paired_difference_seconds": primary["mean_paired_difference_seconds"],
        "ci_95_lower_seconds": primary["ci_95_lower_seconds"],
        "ci_95_upper_seconds": primary["ci_95_upper_seconds"],
        "exact_sign_flip_p_value": primary["exact_sign_flip_p_value"],
        "holm_adjusted_p_value": primary["holm_adjusted_p_value"],
        "hypothesis_supported": bool(primary["holm_reject_at_0_05"] and primary["mean_paired_difference_seconds"] < 0),
        "interpretation_rule": "C5 is supported as faster than C2 only if the paired difference is negative and Holm-adjusted p <= 0.05.",
        "formal_episodes": 50, "observed_recoveries": sum(r["event_observed"] for r in rows),
        "right_censored": sum(r["right_censored"] for r in rows),
        "scheduled_requests": sum(r["scheduled_requests"] for r in rows),
        "completed_requests": sum(r["completed_requests"] for r in rows),
        "error_requests": sum(r["error_requests"] for r in rows),
    }
    (analysis / "C5-formal-conclusion.json").write_text(json.dumps(conclusion, indent=2), encoding="utf-8")

    manifest_files = sorted(p for p in output.rglob("*") if p.is_file())
    manifest_rows = [{"relative_path": p.relative_to(output).as_posix(), "bytes": p.stat().st_size, "sha256": sha(p)} for p in manifest_files]
    write_csv(proof / "C5-FAST-10-R01-SHA256.csv", manifest_rows)
    record_values = {
        "STEP":"C5-FAST-10-R01", "STATUS":"VERIFIED_PASS", "RECORDED_AT_UTC":now(),
        "PROJECT_SCOPE":"BALANCED_FORMAL_C1_C5_T0_T8_ANALYSIS_AND_FREEZE",
        "STAGE9_RECORD_SHA256":STAGE9_RECORD_SHA, "STAGE9_FREEZE_SHA256":STAGE9_FREEZE_SHA,
        "FORMAL_EPISODES":50, "TREATMENTS":5, "EPISODES_PER_TREATMENT":10,
        "TOTAL_SCHEDULED_REQUESTS":conclusion["scheduled_requests"],
        "TOTAL_COMPLETED_REQUESTS":conclusion["completed_requests"], "TOTAL_ERROR_REQUESTS":conclusion["error_requests"],
        "OBSERVED_RECOVERY_EPISODES":conclusion["observed_recoveries"], "RIGHT_CENSORED_EPISODES":conclusion["right_censored"],
        "CENSOR_HORIZON_SECONDS":300, "PRIMARY_CONTRAST":"C5_MINUS_C2",
        "PRIMARY_MEAN_PAIRED_DIFFERENCE_SECONDS":primary["mean_paired_difference_seconds"],
        "PRIMARY_95_CI_LOWER":primary["ci_95_lower_seconds"], "PRIMARY_95_CI_UPPER":primary["ci_95_upper_seconds"],
        "PRIMARY_EXACT_SIGN_FLIP_P_VALUE":primary["exact_sign_flip_p_value"],
        "PRIMARY_HOLM_ADJUSTED_P_VALUE":primary["holm_adjusted_p_value"],
        "PRIMARY_HYPOTHESIS_SUPPORTED":"YES" if conclusion["hypothesis_supported"] else "NO",
        "T0_T8_STAGE_ANALYSIS":"COMPLETE_WITH_MISSINGNESS_DISCLOSED",
        "CLUSTER_QUERIED_BY_THIS_STEP":"NO", "CLUSTER_MODIFIED_BY_THIS_STEP":"NO", "LIVE_WORKLOAD_EXECUTED_BY_THIS_STEP":"NO",
        "C5_STAGE10_COMPLETE":"YES", "C5_FULL_EVALUATION_COMPLETE":"YES",
        "NEXT_STEP":"UPDATE_RESEARCH_PAPER_DASHBOARD_AND_FINAL_SUBMISSION", "FAILURE":"",
    }
    record = output / "C5-FAST-10-R01-record.txt"
    record.write_text("=== C5 FAST TRACK STAGE 10 SUMMARY ===\n" + "\n".join(f"{k}={v}" for k,v in record_values.items()) + "\n", encoding="utf-8")
    freeze = output / "C5-FAST-10-R01-FINAL-ANALYSIS-FREEZE.txt"
    freeze.write_text(record.read_text(encoding="utf-8") +
                      f"NORMALIZED_DATASET_SHA256={sha(analysis/'C5-normalized-formal-dataset.csv')}\n"
                      f"CONTRAST_RESULTS_SHA256={sha(analysis/'C5-preplanned-contrast-results.csv')}\n"
                      f"T0_T8_SUMMARY_SHA256={sha(analysis/'C5-T0-T8-stage-summary.csv')}\n"
                      f"CONCLUSION_SHA256={sha(analysis/'C5-formal-conclusion.json')}\n"
                      f"MANIFEST_SHA256={sha(proof/'C5-FAST-10-R01-SHA256.csv')}\n"
                      f"RECORD_SHA256={sha(record)}\n", encoding="utf-8")
    return {"record": record, "freeze": freeze, "conclusion": conclusion,
            "normalized": analysis/"C5-normalized-formal-dataset.csv"}


def self_test() -> None:
    require(abs(exact_sign_flip_p([1.0] * 10) - 2/1024) < 1e-12, "SIGN_FLIP_SELF_TEST")
    rows = [{"exact_sign_flip_p_value": p} for p in [0.01,0.02,0.03,0.2,0.8]]
    holm_adjust(rows)
    require([round(r["holm_adjusted_p_value"], 2) for r in rows] == [0.05,0.08,0.09,0.4,0.8], "HOLM_SELF_TEST")
    curve, rmst = km_curve([(100, True), (300, False)])
    require(abs(rmst - 200.0) < 1e-9 and len(curve) == 3, "KM_RMST_SELF_TEST")
    require(len(CONTRASTS) == 5 and CONTRASTS[0][2] == "C5_MINUS_C2", "CONTRAST_SELF_TEST")
    print("C5_CLASSFAST_10_R01_SELF_TEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--stage9-run-root")
    parser.add_argument("--output-root")
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return 0
    require(bool(args.stage9_run_root and args.output_root), "MISSING_REQUIRED_ARGUMENT")
    output_base = Path(args.output_root).resolve()
    result_dir = output_base / ("C5-FAST-10-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    result = analyze(Path(args.stage9_run_root).resolve(), result_dir)
    proof_zip = output_base / (result_dir.name + "-PROOF.zip")
    with zipfile.ZipFile(proof_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(result_dir.rglob("*")):
            if p.is_file(): zf.write(p, p.relative_to(result_dir.parent))
    print(result["record"].read_text(encoding="utf-8"), end="")
    print(f"RECORD_PATH={result['record']}")
    print(f"RECORD_SHA256={sha(result['record'])}")
    print(f"FREEZE_PATH={result['freeze']}")
    print(f"FREEZE_SHA256={sha(result['freeze'])}")
    print(f"PROOF_ZIP={proof_zip}")
    print(f"PROOF_ZIP_SHA256={sha(proof_zip)}")
    print(f"C5_STAGE10_RESULT={result_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"C5_CLASSFAST_10_R01_FAILURE={exc}", file=sys.stderr)
        raise
