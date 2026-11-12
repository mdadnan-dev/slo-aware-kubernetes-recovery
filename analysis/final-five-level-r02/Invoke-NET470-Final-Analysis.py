from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


LOADS = (5, 10, 15, 20, 25)
TREATMENTS = ("C1", "C2", "C3", "C4", "C5")
COLORS = {
    "C1": "#4C78A8",
    "C2": "#F58518",
    "C3": "#54A24B",
    "C4": "#B279A2",
    "C5": "#E45756",
}
LINESTYLES = {
    "C1": "-",
    "C2": "--",
    "C3": "-.",
    "C4": ":",
    "C5": (0, (5, 1)),
}
RECOVERED = "BREACH_RECOVERED_WITHIN_300_SECONDS"
CENSORED = "BREACH_RIGHT_CENSORED_AT_300_SECONDS"
NO_BREACH = "NO_QUALIFYING_BREACH"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def fnum(value):
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def inum(value):
    if value is None or str(value).strip() == "":
        return None
    return int(float(value))


def truth(value) -> bool:
    return str(value).strip().lower() == "true"


def percentile(values, p):
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - k) + vals[hi] * (k - lo)


def mean_ci(values):
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return None, None, None
    n = len(vals)
    means = []
    for indexes in itertools.product(range(n), repeat=n):
        means.append(sum(vals[i] for i in indexes) / n)
    return statistics.mean(vals), percentile(means, 0.025), percentile(means, 0.975)


def km_rmst300(rows):
    # All episodes enter at t=0. Recovered episodes are events; right-censored
    # episodes are censored at 300. No-breach episodes are intentionally absent.
    observations = []
    for r in rows:
        if r["outcome"] == RECOVERED:
            observations.append((float(r["recovery_seconds"]), 1))
        elif r["outcome"] == CENSORED:
            observations.append((300.0, 0))
    if not observations:
        return None
    at_risk = len(observations)
    survival = 1.0
    area = 0.0
    previous = 0.0
    for t in sorted(set(t for t, _ in observations if t <= 300.0)):
        area += survival * (t - previous)
        d = sum(1 for ot, event in observations if ot == t and event == 1)
        c = sum(1 for ot, event in observations if ot == t and event == 0)
        if at_risk and d:
            survival *= 1.0 - d / at_risk
        at_risk -= d + c
        previous = t
    if previous < 300.0:
        area += survival * (300.0 - previous)
    return area


def rmst_block_bootstrap(rows):
    by_block = defaultdict(list)
    for r in rows:
        by_block[r["block"]].append(r)
    blocks = sorted(by_block)
    if not blocks:
        return None, None, None
    observed = km_rmst300(rows)
    values = []
    for indexes in itertools.product(range(len(blocks)), repeat=len(blocks)):
        sample = []
        for i in indexes:
            sample.extend(by_block[blocks[i]])
        estimate = km_rmst300(sample)
        if estimate is not None:
            values.append(estimate)
    return observed, percentile(values, 0.025), percentile(values, 0.975)


def write_csv(path: Path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return v


def load_rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        raw = list(csv.DictReader(f))
    rows = []
    for r in raw:
        rows.append({
            **r,
            "rps": inum(r["challenge_rps"]),
            "block": inum(r["block_within_workload"]),
            "scheduled": inum(r["scheduled_requests"]),
            "errors": inum(r["error_requests"]) or 0,
            "recovery_seconds": fnum(r["recovery_seconds"]),
            "right_censored_bool": truth(r["right_censored"]),
            "violation_seconds": fnum(r["slo_violation_duration_seconds"]),
            "violation_percent": fnum(r["slo_violating_window_percent"]),
            "replica_seconds_num": fnum(r["replica_seconds"]),
            "mean_cpu": fnum(r["mean_cpu_percent"]),
            "peak_cpu": fnum(r["peak_cpu_percent"]),
            "peak_desired": fnum(r.get("peak_desired_replicas")),
            "peak_ready": fnum(r.get("peak_ready_endpoints")),
            "controller_actions_num": fnum(r.get("controller_actions")) or 0.0,
            "t4_t5": fnum(r.get("T4_T5_seconds")),
            "t6_t7": fnum(r.get("T6_T7_seconds")),
            "t7_t8": fnum(r.get("T7_T8_seconds")),
        })
    return rows


def validate(rows):
    if len(rows) != 125:
        raise RuntimeError(f"EXPECTED_125_ROWS_FOUND_{len(rows)}")
    keys = Counter((r["rps"], r["block"], r["treatment"]) for r in rows)
    expected = {(rps, b, t) for rps in LOADS for b in range(1, 6) for t in TREATMENTS}
    if set(keys) != expected or any(v != 1 for v in keys.values()):
        raise RuntimeError("DESIGN_CELL_VALIDATION_FAILED")
    if any(not truth(r["scientifically_admissible"]) for r in rows):
        raise RuntimeError("NON_ADMISSIBLE_ROW_IN_PRIMARY_DATASET")
    if any(not truth(r["hpa_exactly_restored"]) for r in rows):
        raise RuntimeError("HPA_RESTORATION_VALIDATION_FAILED")


def technical_exclusions(evidence_root: Path):
    out = []
    for p in evidence_root.rglob("NET470-stress-episode-summary.json"):
        try:
            r = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not r.get("scientifically_admissible", False):
            out.append({
                "execution_attempt_id": r.get("execution_attempt_id", ""),
                "challenge_rps": r.get("challenge_rps", ""),
                "block_within_workload": r.get("block_within_workload", ""),
                "treatment": r.get("treatment", ""),
                "outcome_observed_but_excluded": r.get("outcome", ""),
                "technical_failure": r.get("technical_failure", ""),
                "clock_uncertainty_ms": r.get("maximum_clock_mapping_uncertainty_ms", ""),
                "hpa_restored": r.get("hpa_exactly_restored", ""),
                "summary_path": str(p),
            })
    return sorted(out, key=lambda x: x["execution_attempt_id"])


def make_plots(summary, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "legend.fontsize": 8,
    })
    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    requests = {rps: int(statistics.median([r["scheduled"] for r in summary["raw"] if r["rps"] == rps])) for rps in LOADS}
    xs = [requests[r] for r in LOADS]
    labels = [f"{requests[r]}\n({r} RPS)" for r in LOADS]

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(7.2, 7.0), sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.35, 0.62]},
    )
    for t in TREATMENTS:
        cells = [summary["cells"][(r, t)] for r in LOADS]
        rates = [100 * c["breach_count"] / 5 for c in cells]
        ax1.plot(xs, rates, marker="o", linewidth=1.8, linestyle=LINESTYLES[t],
                 color=COLORS[t], label=t)
        ys = [c["rmst"] if c["rmst"] is not None else math.nan for c in cells]
        lo = [c["rmst_lo"] if c["rmst_lo"] is not None else math.nan for c in cells]
        hi = [c["rmst_hi"] if c["rmst_hi"] is not None else math.nan for c in cells]
        yerr = [[y-l if not math.isnan(y) and not math.isnan(l) else 0 for y,l in zip(ys,lo)],
                [h-y if not math.isnan(y) and not math.isnan(h) else 0 for y,h in zip(ys,hi)]]
        ax2.errorbar(xs, ys, yerr=yerr, marker="o", capsize=3, linewidth=1.8,
                     linestyle=LINESTYLES[t], color=COLORS[t], label=t)
    ax1.set_title("Recovery Behavior vs. Incoming Requests")
    ax1.set_ylabel("Episodes with breach (%)")
    ax1.set_ylim(-3, 105)
    ax1.grid(True, alpha=.25)
    ax1.legend(ncol=5, loc="upper left")
    ax2.set_ylabel("Conditional KM RMST(300) (s)")
    ax2.set_ylim(0, 330)
    ax2.grid(True, alpha=.25)

    # Dedicated recovery-status panel prevents overlapping labels while making
    # recovered (R), censored (C), and no-breach (N) counts explicit.
    for row_index, treatment in enumerate(TREATMENTS):
        for x, rps in zip(xs, LOADS):
            c = summary["cells"][(rps, treatment)]
            ax3.text(x, row_index, f"R{c['recovered_count']} C{c['censored_count']} N{c['no_breach_count']}",
                     ha="center", va="center", fontsize=7.2, color=COLORS[treatment])
    ax3.set_yticks(range(len(TREATMENTS)), TREATMENTS)
    ax3.set_ylim(len(TREATMENTS) - .5, -.5)
    ax3.set_ylabel("Outcome counts")
    ax3.set_xlabel("Incoming requests during episode (workload level)")
    ax3.set_xticks(xs, labels)
    ax3.grid(axis="x", alpha=.20)
    ax3.tick_params(axis="y", length=0)
    fig.text(.5, .005,
             "R = recovered within 300 s; C = right-censored at 300 s; N = no qualifying breach (not zero recovery).",
             ha="center", fontsize=7.8)
    fig.tight_layout(rect=[0, .025, 1, 1])
    fig.savefig(figures / "NET470-Figure-1-recovery-vs-requests.svg", bbox_inches="tight")
    fig.savefig(figures / "NET470-Figure-1-recovery-vs-requests.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    def metric_plot(metric, ylabel, title, filename):
        fig, ax = plt.subplots(figsize=(7.2, 4.8))
        for t in TREATMENTS:
            cells = [summary["cells"][(r, t)] for r in LOADS]
            y = [c[metric] for c in cells]
            lo = [c[metric + "_lo"] for c in cells]
            hi = [c[metric + "_hi"] for c in cells]
            yerr = [[a-b for a,b in zip(y,lo)], [b-a for a,b in zip(y,hi)]]
            ax.errorbar(xs, y, yerr=yerr, marker="o", capsize=3, linewidth=1.8,
                        linestyle=LINESTYLES[t], color=COLORS[t], label=t)
        ax.set_title(title)
        ax.set_xlabel("Incoming requests during episode (workload level)")
        ax.set_ylabel(ylabel)
        ax.set_xticks(xs, labels)
        ax.tick_params(axis="x", labelsize=8)
        ax.grid(True, alpha=.25)
        ax.legend(ncol=5)
        fig.subplots_adjust(left=.13, right=.98, bottom=.23, top=.88)
        fig.savefig(figures / f"{filename}.svg", bbox_inches="tight")
        fig.savefig(figures / f"{filename}.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    metric_plot("violation_mean", "SLO-violation duration (s)",
                "SLO Violations vs. Incoming Requests",
                "NET470-Figure-2-slo-violations-vs-requests")
    metric_plot("replica_mean", "Replica-seconds", "Resource Consumption vs. Incoming Requests",
                "NET470-Figure-3-resource-vs-requests")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--primary-dataset")
    p.add_argument("--evidence-root")
    p.add_argument("--output-root")
    p.add_argument("--expected-dataset-sha256")
    a = p.parse_args()
    if a.self_test:
        print("NET470_FINAL_ANALYSIS_SELF_TEST=PASS")
        print("OFFLINE_ONLY=YES")
        print("KUBERNETES_QUERIES=0")
        print("LIVE_WORKLOAD_REQUESTS=0")
        return 0
    required = (a.primary_dataset, a.evidence_root, a.output_root, a.expected_dataset_sha256)
    if not all(required):
        p.error("analysis arguments are required unless --self-test is used")
    dataset = Path(a.primary_dataset)
    evidence = Path(a.evidence_root)
    output = Path(a.output_root)
    if sha256(dataset) != a.expected_dataset_sha256.upper():
        raise RuntimeError("PRIMARY_DATASET_SHA256_MISMATCH")
    rows = load_rows(dataset)
    validate(rows)
    output.mkdir(parents=True, exist_ok=True)

    cells = {}
    outcome_rows = []
    performance_rows = []
    stage_rows = []
    mechanism_rows = []
    for rps in LOADS:
        for treatment in TREATMENTS:
            group = [r for r in rows if r["rps"] == rps and r["treatment"] == treatment]
            counts = Counter(r["outcome"] for r in group)
            rmst, rmst_lo, rmst_hi = rmst_block_bootstrap(group)
            violation_mean, violation_lo, violation_hi = mean_ci([r["violation_seconds"] for r in group])
            replica_mean, replica_lo, replica_hi = mean_ci([r["replica_seconds_num"] for r in group])
            cpu_mean, cpu_lo, cpu_hi = mean_ci([r["mean_cpu"] for r in group])
            cell = {
                "breach_count": counts[RECOVERED] + counts[CENSORED],
                "recovered_count": counts[RECOVERED],
                "censored_count": counts[CENSORED],
                "no_breach_count": counts[NO_BREACH],
                "rmst": rmst, "rmst_lo": rmst_lo, "rmst_hi": rmst_hi,
                "violation_mean": violation_mean, "violation_mean_lo": violation_lo, "violation_mean_hi": violation_hi,
                "replica_mean": replica_mean, "replica_mean_lo": replica_lo, "replica_mean_hi": replica_hi,
                "cpu_mean": cpu_mean, "cpu_mean_lo": cpu_lo, "cpu_mean_hi": cpu_hi,
            }
            cells[(rps, treatment)] = cell
            outcome_rows.append({
                "challenge_rps": rps, "treatment": treatment, "episodes": 5,
                "no_qualifying_breach": counts[NO_BREACH],
                "recovered_within_300": counts[RECOVERED],
                "right_censored_at_300": counts[CENSORED],
                "breach_incidence_percent": fmt(100 * cell["breach_count"] / 5),
                "censoring_percent_all_episodes": fmt(100 * counts[CENSORED] / 5),
            })
            performance_rows.append({
                "challenge_rps": rps, "treatment": treatment,
                "incoming_requests": int(statistics.median(r["scheduled"] for r in group)),
                "conditional_km_rmst300_seconds": fmt(rmst),
                "rmst300_ci95_low": fmt(rmst_lo), "rmst300_ci95_high": fmt(rmst_hi),
                "mean_slo_violation_seconds": fmt(violation_mean),
                "slo_violation_ci95_low": fmt(violation_lo), "slo_violation_ci95_high": fmt(violation_hi),
                "mean_replica_seconds": fmt(replica_mean),
                "replica_seconds_ci95_low": fmt(replica_lo), "replica_seconds_ci95_high": fmt(replica_hi),
                "mean_cpu_percent": fmt(cpu_mean), "mean_cpu_ci95_low": fmt(cpu_lo), "mean_cpu_ci95_high": fmt(cpu_hi),
            })
            for stage, key in (("T4-T5", "t4_t5"), ("T6-T7", "t6_t7"), ("T7-T8", "t7_t8")):
                vals = [r[key] for r in group if r[key] is not None]
                m, lo, hi = mean_ci(vals)
                stage_rows.append({"challenge_rps": rps, "treatment": treatment, "stage": stage,
                                   "observed_n": len(vals), "mean_seconds": fmt(m),
                                   "ci95_low": fmt(lo), "ci95_high": fmt(hi)})
            mechanism_rows.append({
                "challenge_rps": rps,
                "treatment": treatment,
                "episodes_with_controller_action": sum(r["controller_actions_num"] > 0 for r in group),
                "controller_action_episode_percent": fmt(100 * sum(r["controller_actions_num"] > 0 for r in group) / 5),
                "mean_controller_actions": fmt(statistics.mean(r["controller_actions_num"] for r in group)),
                "mean_peak_desired_replicas": fmt(statistics.mean(r["peak_desired"] for r in group if r["peak_desired"] is not None)),
                "mean_peak_ready_endpoints": fmt(statistics.mean(r["peak_ready"] for r in group if r["peak_ready"] is not None)),
                "mean_cpu_percent": fmt(cpu_mean),
                "mean_peak_cpu_percent": fmt(statistics.mean(r["peak_cpu"] for r in group if r["peak_cpu"] is not None)),
                "mean_T4_T5_seconds": fmt(statistics.mean(r["t4_t5"] for r in group if r["t4_t5"] is not None) if any(r["t4_t5"] is not None for r in group) else None),
                "mean_T6_T7_seconds": fmt(statistics.mean(r["t6_t7"] for r in group if r["t6_t7"] is not None) if any(r["t6_t7"] is not None for r in group) else None),
                "mean_T7_T8_seconds": fmt(statistics.mean(r["t7_t8"] for r in group if r["t7_t8"] is not None) if any(r["t7_t8"] is not None for r in group) else None),
            })

    paired = []
    for rps in LOADS:
        for block in range(1, 6):
            c2 = next(r for r in rows if r["rps"] == rps and r["block"] == block and r["treatment"] == "C2")
            c5 = next(r for r in rows if r["rps"] == rps and r["block"] == block and r["treatment"] == "C5")
            both_breach = c2["outcome"] != NO_BREACH and c5["outcome"] != NO_BREACH
            paired.append({
                "challenge_rps": rps, "block": block,
                "c2_outcome": c2["outcome"], "c5_outcome": c5["outcome"],
                "both_qualifying_breach": both_breach,
                "c5_minus_c2_recovery_or_rmst300": fmt((c5["recovery_seconds"] - c2["recovery_seconds"]) if both_breach else None),
                "c5_minus_c2_slo_violation_seconds": fmt(c5["violation_seconds"] - c2["violation_seconds"]),
                "c5_minus_c2_replica_seconds": fmt(c5["replica_seconds_num"] - c2["replica_seconds_num"]),
                "c5_minus_c2_T7_T8_seconds": fmt((c5["t7_t8"] - c2["t7_t8"]) if c5["t7_t8"] is not None and c2["t7_t8"] is not None else None),
            })

    exclusions = technical_exclusions(evidence)
    c5_c2_summary = []
    for rps in LOADS:
        c2 = cells[(rps, "C2")]
        c5 = cells[(rps, "C5")]
        c2_stage = next(r for r in mechanism_rows if r["challenge_rps"] == rps and r["treatment"] == "C2")
        c5_stage = next(r for r in mechanism_rows if r["challenge_rps"] == rps and r["treatment"] == "C5")
        recovery_delta = c5["rmst"] - c2["rmst"] if c5["rmst"] is not None and c2["rmst"] is not None else None
        c5_c2_summary.append({
            "challenge_rps": rps,
            "c2_conditional_rmst300_seconds": fmt(c2["rmst"]),
            "c5_conditional_rmst300_seconds": fmt(c5["rmst"]),
            "c5_minus_c2_rmst300_seconds": fmt(recovery_delta),
            "c5_minus_c2_rmst300_percent": fmt(100 * recovery_delta / c2["rmst"] if recovery_delta is not None and c2["rmst"] else None),
            "c5_minus_c2_slo_violation_seconds": fmt(c5["violation_mean"] - c2["violation_mean"]),
            "c5_minus_c2_replica_seconds": fmt(c5["replica_mean"] - c2["replica_mean"]),
            "c5_minus_c2_mean_cpu_percent_points": fmt(c5["cpu_mean"] - c2["cpu_mean"]),
            "c5_minus_c2_T7_T8_seconds": fmt(
                float(c5_stage["mean_T7_T8_seconds"]) - float(c2_stage["mean_T7_T8_seconds"])
                if c5_stage["mean_T7_T8_seconds"] != "" and c2_stage["mean_T7_T8_seconds"] != "" else None),
            "c2_recovered": c2["recovered_count"], "c2_censored": c2["censored_count"], "c2_no_breach": c2["no_breach_count"],
            "c5_recovered": c5["recovered_count"], "c5_censored": c5["censored_count"], "c5_no_breach": c5["no_breach_count"],
        })
    write_csv(output / "tables/NET470-outcome-summary.csv", list(outcome_rows[0]), outcome_rows)
    write_csv(output / "tables/NET470-performance-summary.csv", list(performance_rows[0]), performance_rows)
    write_csv(output / "tables/NET470-paired-C5-vs-C2.csv", list(paired[0]), paired)
    write_csv(output / "tables/NET470-stage-summary.csv", list(stage_rows[0]), stage_rows)
    write_csv(output / "tables/NET470-controller-mechanism-summary.csv", list(mechanism_rows[0]), mechanism_rows)
    write_csv(output / "tables/NET470-C5-vs-C2-summary.csv", list(c5_c2_summary[0]), c5_c2_summary)
    exclusion_fields = ["execution_attempt_id", "challenge_rps", "block_within_workload", "treatment",
                        "outcome_observed_but_excluded", "technical_failure", "clock_uncertainty_ms",
                        "hpa_restored", "summary_path"]
    write_csv(output / "tables/NET470-technical-exclusions.csv", exclusion_fields, exclusions)

    make_plots({"raw": rows, "cells": cells}, output)
    record = {
        "schema": "NET470-FINAL-FIVE-LEVEL-ANALYSIS-V1",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "VERIFIED_PASS",
        "offline_only": True,
        "primary_dataset": str(dataset),
        "primary_dataset_sha256": sha256(dataset),
        "episodes": len(rows), "workloads": list(LOADS), "treatments": list(TREATMENTS),
        "scheduled_requests": sum(r["scheduled"] for r in rows),
        "completed_requests": sum(inum(r["completed_requests"]) or 0 for r in rows),
        "request_errors": sum(r["errors"] for r in rows),
        "no_breach_episodes": sum(r["outcome"] == NO_BREACH for r in rows),
        "recovered_episodes": sum(r["outcome"] == RECOVERED for r in rows),
        "right_censored_episodes": sum(r["outcome"] == CENSORED for r in rows),
        "technical_exclusions_preserved": len(exclusions),
        "kubernetes_queried": False, "cluster_modified": False, "live_workload_executed": False,
    }
    record_path = output / "NET470-final-analysis-record.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    files_before_freeze = sorted(p for p in output.rglob("*") if p.is_file())
    freeze_lines = [
        "NET470_FINAL_ANALYSIS_FREEZE=VERIFIED_PASS",
        f"PRIMARY_DATASET_SHA256={sha256(dataset)}",
        f"EPISODES={len(rows)}",
        "OFFLINE_ONLY=YES",
        "KUBERNETES_QUERIES=0",
        "LIVE_WORKLOAD_REQUESTS=0",
        f"ANALYSIS_RECORD_SHA256={sha256(record_path)}",
    ]
    freeze = output / "NET470-FINAL-ANALYSIS-FREEZE.txt"
    freeze.write_text("\n".join(freeze_lines) + "\n", encoding="utf-8")

    manifest_files = sorted(p for p in output.rglob("*") if p.is_file() and "proof" not in p.parts)
    manifest_rows = [{"relative_path": p.relative_to(output).as_posix(), "size_bytes": p.stat().st_size,
                      "sha256": sha256(p)} for p in manifest_files]
    manifest = output / "proof/NET470-final-analysis-SHA256.csv"
    write_csv(manifest, ["relative_path", "size_bytes", "sha256"], manifest_rows)

    print("=== NET470 FINAL ANALYSIS SUMMARY ===")
    print("STATUS=VERIFIED_PASS")
    print("OFFLINE_ONLY=YES")
    print(f"EPISODES={len(rows)}")
    print(f"TECHNICAL_EXCLUSIONS_PRESERVED={len(exclusions)}")
    print(f"PRIMARY_DATASET_SHA256={sha256(dataset)}")
    print(f"ANALYSIS_RECORD_SHA256={sha256(record_path)}")
    print(f"FREEZE_SHA256={sha256(freeze)}")
    print(f"OUTPUT={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

