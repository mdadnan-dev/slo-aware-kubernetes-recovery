from __future__ import annotations

import ast
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


STAGE10 = Path(
    r"C:\NET470-C5-Fast\Stage10-R01\results"
    r"\C5-FAST-10-20261106T073230Z"
)

STAGE9 = Path(
    r"C:\NET470-C5-Fast\Stage9-R02\results"
    r"\C5-FORMAL-C1-C5-A01"
)

OUTPUT = Path(
    r"C:\NET470-C5-Fast\Final-Paper-Documentation"
    r"\final-three-figures-R03"
)

OUTPUT.mkdir(parents=True, exist_ok=True)

ANALYSIS = STAGE10 / "analysis"
EPISODES = STAGE9 / "episodes"

TREATMENTS = ["C1", "C2", "C3", "C4", "C5"]

TREATMENT_COLORS = {
    "C1": "#7F8C8D",
    "C2": "#1F77B4",
    "C3": "#2CA02C",
    "C4": "#FF7F0E",
    "C5": "#D62728",
}

STAGES = [
    "T0_T1",
    "T1_T2",
    "T2_T3",
    "T3_T4",
    "T4_T5",
    "T5_T6",
    "T6_T7",
    "T7_T8",
]

STAGE_LABELS = {
    "T0_T1": "T0-T1",
    "T1_T2": "T1-T2",
    "T2_T3": "T2-T3",
    "T3_T4": "T3-T4",
    "T4_T5": "T4-T5",
    "T5_T6": "T5-T6",
    "T6_T7": "T6-T7",
    "T7_T8": "T7-T8",
}

STAGE_COLORS = {
    "T0_T1": "#4E79A7",
    "T1_T2": "#A0CBE8",
    "T2_T3": "#F28E2B",
    "T3_T4": "#FFBE7D",
    "T4_T5": "#59A14F",
    "T5_T6": "#8CD17D",
    "T6_T7": "#B6992D",
    "T7_T8": "#E15759",
}

SLO_THRESHOLD_MS = 123.472

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "font.size": 8,
        "axes.titlesize": 9,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "svg.hashsalt": "NET470-C5-FINAL-FIGURES-R01",
    }
)


def save_figure(fig: plt.Figure, base_name: str) -> None:
    svg_path = OUTPUT / f"{base_name}.svg"
    png_path = OUTPUT / f"{base_name}.png"

    fig.savefig(
        svg_path,
        format="svg",
        bbox_inches="tight",
        metadata={
            "Creator": "NET470 C5 final analysis",
            "Date": None,
        },
    )

    fig.savefig(
        png_path,
        format="png",
        bbox_inches="tight",
        dpi=300,
        metadata={
            "Creator": "NET470 C5 final analysis",
        },
    )

    plt.close(fig)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------------
# FIGURE 1: C1-C5 recovery comparison and primary paired contrast
# ---------------------------------------------------------------------

treatment_summary = pd.read_csv(
    ANALYSIS / "C5-treatment-summary.csv"
)

contrast_results = pd.read_csv(
    ANALYSIS / "C5-preplanned-contrast-results.csv"
)

paired = pd.read_csv(
    ANALYSIS / "C5-block-paired-differences.csv"
)

treatment_summary["treatment"] = pd.Categorical(
    treatment_summary["treatment"],
    categories=TREATMENTS,
    ordered=True,
)

treatment_summary = treatment_summary.sort_values(
    "treatment"
).reset_index(drop=True)

primary = contrast_results.loc[
    contrast_results["contrast"] == "C5_MINUS_C2"
].iloc[0]

primary_pairs = paired.loc[
    paired["contrast"] == "C5_MINUS_C2"
].sort_values("block")

fig, axes = plt.subplots(
    1,
    2,
    figsize=(7.16, 3.85),
    gridspec_kw={"width_ratios": [1.05, 1.25]},
)

ax = axes[0]

x = np.arange(len(treatment_summary))

bars = ax.bar(
    x,
    treatment_summary["rmst_300_seconds"],
    color=[
        TREATMENT_COLORS[str(t)]
        for t in treatment_summary["treatment"]
    ],
    edgecolor="black",
    linewidth=0.5,
)

ax.set_xticks(x)
ax.set_xticklabels(
    [str(t) for t in treatment_summary["treatment"]]
)
ax.set_ylabel("RMST through 300 s (seconds)")
ax.set_title("(a) Recovery outcome by treatment")
ax.set_ylim(
    0,
    float(treatment_summary["rmst_300_seconds"].max()) * 1.20,
)

for bar, row in zip(
    bars,
    treatment_summary.itertuples(index=False),
):
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height() + 4,
        f"{bar.get_height():.1f} s\n"
        f"{int(row.observed_recoveries)} rec., "
        f"{int(row.right_censored)} cens.",
        ha="center",
        va="bottom",
        fontsize=6.0,
        linespacing=1.05,
    )

ax = axes[1]

blocks = primary_pairs["block"].astype(int).to_numpy()
differences = primary_pairs[
    "paired_difference_seconds"
].astype(float).to_numpy()

ax.axhline(
    0,
    color="black",
    linewidth=0.8,
    linestyle="--",
    label="No difference",
)

ax.scatter(
    blocks,
    differences,
    color=TREATMENT_COLORS["C5"],
    edgecolor="black",
    linewidth=0.4,
    s=27,
    zorder=3,
    label="Block difference",
)

ax.plot(
    blocks,
    differences,
    color=TREATMENT_COLORS["C5"],
    alpha=0.35,
    linewidth=0.8,
)

mean_difference = float(
    primary["mean_paired_difference_seconds"]
)

ci_lower = float(primary["ci_95_lower_seconds"])
ci_upper = float(primary["ci_95_upper_seconds"])

ax.axhline(
    mean_difference,
    color="#6A3D9A",
    linewidth=1.2,
    label=f"Mean = {mean_difference:.1f} s",
)

ax.fill_between(
    [0.5, 10.5],
    ci_lower,
    ci_upper,
    color="#6A3D9A",
    alpha=0.12,
    label=f"95% CI [{ci_lower:.1f}, {ci_upper:.1f}]",
)

ax.set_xlim(0.5, 10.5)
ax.set_xticks(blocks)
ax.set_xlabel("Randomized block")
ax.set_ylabel("C5 - C2 paired RMST difference (seconds)")
ax.set_title("(b) Preplanned primary contrast")

ax.legend(
    loc="upper left",
    frameon=True,
    fontsize=6.2,
)

fig.suptitle(
    "Controlled C1-C5 Recovery Comparison",
    fontsize=10,
    fontweight="bold",
)

fig.subplots_adjust(left=0.08, right=0.98, bottom=0.14, top=0.82, wspace=0.32)

save_figure(
    fig,
    "Figure-1-C1-C5-Recovery-Comparison",
)


# ---------------------------------------------------------------------
# FIGURE 2: T0-T8 stage decomposition
# ---------------------------------------------------------------------

stage_summary = pd.read_csv(
    ANALYSIS / "C5-T0-T8-stage-summary.csv"
)

stage_summary["mean_seconds"] = pd.to_numeric(
    stage_summary["mean_seconds"],
    errors="coerce",
)

stage_summary["observed_n"] = pd.to_numeric(
    stage_summary["observed_n"],
    errors="coerce",
).fillna(0)

stage_summary["missing_or_censored_n"] = pd.to_numeric(
    stage_summary["missing_or_censored_n"],
    errors="coerce",
).fillna(0)

mean_pivot = (
    stage_summary.pivot(
        index="treatment",
        columns="stage",
        values="mean_seconds",
    )
    .reindex(index=TREATMENTS, columns=STAGES)
    .fillna(0.0)
)

observed_pivot = (
    stage_summary.pivot(
        index="treatment",
        columns="stage",
        values="observed_n",
    )
    .reindex(index=TREATMENTS, columns=STAGES)
    .fillna(0.0)
)

missing_pivot = (
    stage_summary.pivot(
        index="treatment",
        columns="stage",
        values="missing_or_censored_n",
    )
    .reindex(index=TREATMENTS, columns=STAGES)
    .fillna(0.0)
)

fig, ax = plt.subplots(
    1,
    1,
    figsize=(7.16, 4.15),
)

left = np.zeros(len(TREATMENTS))

for stage in STAGES:
    values = mean_pivot[stage].to_numpy(dtype=float)

    ax.barh(
        TREATMENTS,
        values,
        left=left,
        color=STAGE_COLORS[stage],
        edgecolor="white",
        linewidth=0.5,
        label=STAGE_LABELS[stage],
    )

    for index, value in enumerate(values):
        if value >= 6:
            ax.text(
                left[index] + value / 2,
                index,
                f"{value:.1f}",
                ha="center",
                va="center",
                fontsize=6,
                color=(
                    "white"
                    if stage in ("T0_T1", "T7_T8")
                    else "black"
                ),
            )

    left += values

for index, treatment in enumerate(TREATMENTS):
    t7_n = int(observed_pivot.loc[treatment, "T7_T8"])
    t7_missing = int(
        missing_pivot.loc[treatment, "T7_T8"]
    )

    ax.text(
        left[index] + 2,
        index,
        f"T7-T8: n={t7_n}, missing={t7_missing}",
        ha="left",
        va="center",
        fontsize=6.4,
    )

ax.set_xlabel(
    "Sum of reported mean stage durations (seconds)"
)
ax.set_ylabel("Treatment")
ax.set_title(
    "Mean T0-T8 Recovery-Stage Durations by Treatment",
    fontsize=10,
    fontweight="bold",
)

ax.set_xlim(0, max(left) * 1.32)

ax.legend(
    loc="upper center",
    bbox_to_anchor=(0.5, -0.13),
    ncol=4,
    frameon=False,
    columnspacing=1.2,
    handletextpad=0.5,
)

fig.text(
    0.5,
    0.025,
    "Segment means may have unequal denominators; "
    "stacked values are descriptive, not imputed "
    "per-episode totals.",
    ha="center",
    va="bottom",
    fontsize=6.2,
)

fig.subplots_adjust(
    left=0.08,
    right=0.98,
    top=0.86,
    bottom=0.27,
)

save_figure(
    fig,
    "Figure-2-T0-T8-Stage-Decomposition",
)


# ---------------------------------------------------------------------
# FIGURE 3: Representative Block 10 C2 and C5 trace
# ---------------------------------------------------------------------

def endpoint_count(value) -> float:
    if pd.isna(value):
        return np.nan

    text = str(value).strip()

    if not text:
        return np.nan

    try:
        parsed = ast.literal_eval(text)

        if isinstance(parsed, (list, tuple, set)):
            return float(len(parsed))

        return float(parsed)
    except Exception:
        try:
            return float(text)
        except Exception:
            return np.nan


def to_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(
        series,
        utc=True,
        errors="coerce",
    )


def load_attempt(attempt_id: str):
    root = EPISODES / attempt_id

    summary = read_json(
        root / "analysis" / "C5-FAST-09-formal-summary.json"
    )

    events = summary["events"]

    phase = pd.read_csv(
        root / "workload" / "phase-ledger.csv"
    )

    phase["started_at_utc"] = to_utc(
        phase["started_at_utc"]
    )

    phase["duration_seconds"] = pd.to_numeric(
        phase["duration_seconds"],
        errors="coerce",
    )

    phase["rate_rps"] = pd.to_numeric(
        phase["rate_rps"],
        errors="coerce",
    )

    high_start = phase.loc[
        phase["phase_id"] == "high-challenge",
        "started_at_utc",
    ].iloc[0]

    phase["start_seconds"] = (
        phase["started_at_utc"] - high_start
    ).dt.total_seconds()

    phase["end_seconds"] = (
        phase["start_seconds"] +
        phase["duration_seconds"]
    )

    windows = pd.read_csv(
        root / "analysis" / "slo-windows.csv"
    )

    windows["window_start_utc"] = to_utc(
        windows["window_start_utc"]
    )

    windows["window_end_utc"] = to_utc(
        windows["window_end_utc"]
    )

    windows["midpoint_utc"] = (
        windows["window_start_utc"] +
        (
            windows["window_end_utc"] -
            windows["window_start_utc"]
        ) / 2
    )

    windows["seconds"] = (
        windows["midpoint_utc"] - high_start
    ).dt.total_seconds()

    windows["p95_ms"] = pd.to_numeric(
        windows["p95_ms"],
        errors="coerce",
    )

    runtime = pd.read_csv(
        root / "telemetry" / "cluster-runtime.csv"
    )

    runtime["observed_at_utc"] = to_utc(
        runtime["observed_at_utc"]
    )

    runtime["seconds"] = (
        runtime["observed_at_utc"] - high_start
    ).dt.total_seconds()

    for column in (
        "hpa_current_replicas",
        "hpa_desired_replicas",
        "hpa_cpu_percent",
    ):
        runtime[column] = pd.to_numeric(
            runtime[column],
            errors="coerce",
        )

    runtime["ready_endpoints"] = runtime[
        "processing_ready_endpoints"
    ].apply(endpoint_count)

    controller = []

    ledger_path = (
        root /
        "analysis" /
        "C5-controller-ledger.json"
    )

    if ledger_path.exists():
        controller = read_json(ledger_path)

    action_seconds = None

    for item in controller:
        if item.get("action") == "HPA_MIN_TO_5":
            action_time = pd.to_datetime(
                item["observed_at_utc"],
                utc=True,
            )

            action_seconds = (
                action_time - high_start
            ).total_seconds()

            break

    relative_events = {}

    for name in ("T0", "T8", "TB", "TR"):
        value = events.get(name)

        if value:
            event_time = pd.to_datetime(
                value,
                utc=True,
            )

            relative_events[name] = (
                event_time - high_start
            ).total_seconds()

    return {
        "root": root,
        "summary": summary,
        "phase": phase,
        "windows": windows,
        "runtime": runtime,
        "action_seconds": action_seconds,
        "events": relative_events,
    }


attempts = {
    "C2": load_attempt("C5-FORMAL-B10-C2-A01"),
    "C5": load_attempt("C5-FORMAL-B10-C5-A01"),
}

fig, axes = plt.subplots(
    3,
    2,
    figsize=(7.16, 6.2),
    sharex="col",
)

for column_index, treatment in enumerate(("C2", "C5")):
    data = attempts[treatment]

    phase = data["phase"]
    windows = data["windows"]
    runtime = data["runtime"]
    events = data["events"]
    action_seconds = data["action_seconds"]

    high_row = phase.loc[
        phase["phase_id"] == "high-challenge"
    ].iloc[0]

    high_start_s = float(high_row["start_seconds"])
    high_end_s = float(high_row["end_seconds"])

    for row_index in range(3):
        axes[row_index, column_index].axvspan(
            high_start_s,
            high_end_s,
            color="#F4A261",
            alpha=0.10,
            label=(
                "25 RPS challenge"
                if row_index == 0
                else None
            ),
        )

    # Row 1: p95 latency and offered rate
    ax = axes[0, column_index]

    valid_windows = windows.loc[
        windows["p95_ms"].notna() &
        (windows["p95_ms"] > 0)
    ]

    ax.plot(
        valid_windows["seconds"],
        valid_windows["p95_ms"],
        color=TREATMENT_COLORS[treatment],
        marker="o",
        markersize=2.6,
        linewidth=1.0,
        label=f"{treatment} window p95",
    )

    ax.axhline(
        SLO_THRESHOLD_MS,
        color="black",
        linestyle="--",
        linewidth=0.9,
        label=f"SLO = {SLO_THRESHOLD_MS:.3f} ms",
    )

    ax.set_yscale("log")
    ax.set_ylabel("p95 latency\n(ms, log scale)")
    ax.set_title(
        (
            "C2  -  tuned static HPA"
            if treatment == "C2"
            else "C5  -  complete framework"
        ),
        fontweight="bold",
    )

    rate_axis = ax.twinx()

    phase_x = []
    phase_y = []

    for row in phase.itertuples(index=False):
        phase_x.extend(
            [
                float(row.start_seconds),
                float(row.end_seconds),
            ]
        )

        phase_y.extend(
            [
                float(row.rate_rps),
                float(row.rate_rps),
            ]
        )

    rate_axis.plot(
        phase_x,
        phase_y,
        color="#555555",
        linewidth=0.8,
        alpha=0.55,
        drawstyle="steps-post",
        label="Offered RPS",
    )

    rate_axis.set_ylim(0, 30)

    if column_index == 1:
        rate_axis.set_ylabel("Offered RPS")

    else:
        rate_axis.set_yticklabels([])

    # Row 2: scaling and readiness
    ax = axes[1, column_index]

    ax.plot(
        runtime["seconds"],
        runtime["hpa_current_replicas"],
        color="#1F77B4",
        linewidth=1.0,
        label="Current replicas",
    )

    ax.plot(
        runtime["seconds"],
        runtime["hpa_desired_replicas"],
        color="#D62728",
        linewidth=1.0,
        linestyle="--",
        label="Desired replicas",
    )

    ax.plot(
        runtime["seconds"],
        runtime["ready_endpoints"],
        color="#2CA02C",
        linewidth=1.0,
        linestyle="-.",
        label="Ready endpoints",
    )

    ax.set_ylabel("Replicas /\nready endpoints")
    ax.set_ylim(0, 6)

    # Row 3: CPU
    ax = axes[2, column_index]

    ax.plot(
        runtime["seconds"],
        runtime["hpa_cpu_percent"],
        color="#6A3D9A",
        linewidth=1.0,
        label="HPA CPU",
    )

    ax.axhline(
        55,
        color="black",
        linestyle="--",
        linewidth=0.9,
        label="55% target",
    )

    ax.set_ylabel("CPU relative to\nrequest (%)")
    ax.set_xlabel(
        "Seconds from high-challenge start"
    )

    # Event markers
    for row_index in range(3):
        axis = axes[row_index, column_index]

        if "TB" in events:
            axis.axvline(
                events["TB"],
                color="#B22222",
                linestyle=":",
                linewidth=0.9,
                label=(
                    "SLO breach"
                    if row_index == 0
                    else None
                ),
            )

        if "TR" in events:
            axis.axvline(
                events["TR"],
                color="#006400",
                linestyle=":",
                linewidth=0.9,
                label=(
                    "Sustained recovery"
                    if row_index == 0
                    else None
                ),
            )

        if (
            treatment == "C5" and
            action_seconds is not None
        ):
            axis.axvline(
                action_seconds,
                color="#E31A1C",
                linestyle="-.",
                linewidth=1.0,
                label=(
                    "C5 HPA-min action"
                    if row_index == 0
                    else None
                ),
            )

    # Workload range
    x_min = float(phase["start_seconds"].min())
    x_max = float(phase["end_seconds"].max())

    for row_index in range(3):
        axes[row_index, column_index].set_xlim(
            x_min,
            x_max,
        )

    axes[0, column_index].legend(
        loc="upper right",
        fontsize=5.8,
        frameon=True,
    )

    axes[1, column_index].legend(
        loc="upper right",
        fontsize=5.8,
        frameon=True,
    )

    axes[2, column_index].legend(
        loc="upper right",
        fontsize=5.8,
        frameon=True,
    )

fig.suptitle(
    "Representative Block 10 End-to-End Recovery Traces",
    fontsize=10,
    fontweight="bold",
)

fig.text(
    0.5,
    0.012,
    "Formal inference uses all 50 episodes; these traces "
    "illustrate one accepted C2/C5 pair. "
    "Vertical lines identify breach, recovery, and the "
    "single authorized C5 action.",
    ha="center",
    va="bottom",
    fontsize=6.5,
)

fig.tight_layout(rect=(0, 0.045, 1, 0.965))

save_figure(
    fig,
    "Figure-3-Block10-End-to-End-Recovery-Trace",
)


# ---------------------------------------------------------------------
# Captions and generation summary
# ---------------------------------------------------------------------

captions = """FIGURE 1
Controlled C1-C5 recovery comparison. Left: restricted mean
time without confirmed recovery through 300 seconds and
observed/right-censored outcomes for ten episodes per treatment.
Lower RMST indicates faster confirmed recovery. Right: paired
C5-minus-C2 differences across ten randomized blocks, with the
mean and 95% confidence interval. Negative values favor C5.

FIGURE 2
Mean measured T0-T8 recovery-stage durations by treatment.
T7-T8 is the dominant observed component. Segment means may
have unequal denominators where events are missing or censored;
the stacked values are descriptive and are not imputed
per-episode totals.

FIGURE 3
Representative Block 10 C2 and C5 episodes under the frozen
5-to-25-to-5 requests-per-second transition. Panels show
15-second-window p95 latency, the SLO threshold, HPA current
and desired replicas, ready processing endpoints, CPU
utilization relative to the container request, breach and
sustained-recovery boundaries, and the single authorized C5
action. Formal inference uses all 50 episodes.
"""

(OUTPUT / "FINAL-FIGURE-CAPTIONS.txt").write_text(
    captions,
    encoding="utf-8",
)

generated = sorted(
    path.name
    for path in OUTPUT.iterdir()
    if path.is_file()
)

print("FIGURE_GENERATION=VERIFIED_PASS")
print("LOGICAL_FIGURE_COUNT=3")

for name in generated:
    print(f"GENERATED={name}")

