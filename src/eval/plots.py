"""R8 figures (plan Section 7.4): F6, F7, F9, drawn from the tables of src.eval.aggregate.

    python -m src.eval.plots --main reports/r8/main --small reports/r8/small_three_modes --out reports/figures

Every figure is generated from ``summary.csv`` / ``runs.csv``, never edited by hand. One colour per
mode in every figure (validated with the dataviz palette checker, CVD and normal-vision PASS); the
mode is also told apart by marker shape and the legend, because the aqua is under 3:1 on white.
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

from src.eval.stats import bootstrap_ci  # noqa: E402

COLORS = {"centralized": "#eb6834", "independent": "#2a78d6", "full": "#1baf7a"}
MARKERS = {"centralized": "s", "independent": "o", "full": "^"}
LABELS = {"centralized": "Centralized (OR-Tools)", "independent": "Independent (D* Lite)",
          "full": "Full (D* Lite + trigger)"}
SHORT = {"centralized": "central.", "independent": "indep.", "full": "full"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _style() -> None:
    plt.rcParams.update({
        "font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
        "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.axisbelow": True, "legend.frameon": False, "figure.dpi": 150, "savefig.dpi": 150,
    })


def _curve(ax, summ: pd.DataFrame, metric: str, ylabel: str, log: bool = False) -> None:
    sub = summ[summ["metric"] == metric]
    rates = sorted(sub["rate"].unique())
    pos = {r: i for i, r in enumerate(rates)}
    modes = [m for m in COLORS if m in set(sub["mode"])]
    for k, mode in enumerate(modes):
        s = sub[sub["mode"] == mode].sort_values("rate")
        x = np.array([pos[r] for r in s["rate"]], dtype=float) + (k - (len(modes) - 1) / 2) * 0.06
        y = s["mean"].to_numpy(dtype=float)
        err = np.vstack([y - s["ci_lo"].to_numpy(dtype=float), s["ci_hi"].to_numpy(dtype=float) - y])
        err = np.nan_to_num(err, nan=0.0)
        ax.errorbar(x, y, yerr=err, color=COLORS[mode], marker=MARKERS[mode], markersize=5,
                    linewidth=1.6, capsize=2.5, label=LABELS[mode], markeredgecolor="white",
                    markeredgewidth=0.8)
    ax.set_xticks(range(len(rates)))
    ax.set_xticklabels([f"{r:g}" for r in rates])
    ax.set_xlabel("disruptions per hour (λ)")
    ax.set_ylabel(ylabel)
    if log:
        ax.set_yscale("log")


def fig_degradation(summ: pd.DataFrame, path: str, title: str) -> None:
    """F6: physical metrics against disruption rate, 95% bootstrap CIs over runs."""
    _style()
    fig, axes = plt.subplots(2, 2, figsize=(8.4, 6.0))
    panels = [("delivery_time_mean_s", "mean delivery time per order (s)"),
              ("violations_cap1", "road-capacity violations per run"),
              ("distance_km", "fleet distance per run (km)"),
              ("jain", "Jain fairness of vehicle workload")]
    for ax, (metric, label) in zip(axes.ravel(), panels):
        _curve(ax, summ, metric, label)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, fontsize=8)
    fig.suptitle(title, fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    fig.savefig(path)
    plt.close(fig)


def fig_cost(summ: pd.DataFrame, path: str, title: str) -> None:
    """F7: planning CPU and messages against disruption rate."""
    _style()
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.4))
    panels = [("planning_cpu_s", "planning CPU per run (s)", False),
              ("cpu_per_decision_p95_s", "95th-pct CPU of one replan / re-solve (s)", False),
              ("messages_total", "messages per run (D7)", False)]
    for ax, (metric, label, log) in zip(axes, panels):
        _curve(ax, summ, metric, label, log)
        ax.set_ylim(bottom=0)  # linear from zero: a log axis over a narrow range exaggerates small gaps
    axes[0].legend(loc="lower right", fontsize=8)
    fig.suptitle(title, fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(path)
    plt.close(fig)


def fig_bars(runs: pd.DataFrame, rate: float, path: str, title: str) -> None:
    """F9: the three modes side by side at one operating point (mean, 95% CI over runs)."""
    _style()
    sub = runs[runs["rate"] == rate]
    modes = [m for m in COLORS if m in set(sub["mode"])]
    panels = [("delivery_time_mean_s", "mean delivery time (s)"),
              ("violations_cap1", "capacity violations"),
              ("planning_cpu_s", "planning CPU (s)"),
              ("messages_total", "messages")]
    fig, axes = plt.subplots(1, 4, figsize=(10.5, 3.2))
    for ax, (metric, label) in zip(axes, panels):
        for i, mode in enumerate(modes):
            vals = sub[sub["mode"] == mode][metric].to_numpy(dtype=float)
            mean, lo, hi = bootstrap_ci(vals)
            ax.bar(i, mean, width=0.62, color=COLORS[mode], label=LABELS[mode])
            if not np.isnan(lo):
                ax.errorbar(i, mean, yerr=[[mean - lo], [hi - mean]], color=INK, capsize=3, linewidth=1)
        ax.set_xticks(range(len(modes)))
        ax.set_xticklabels([SHORT.get(m, m) for m in modes], fontsize=8)
        ax.set_ylabel(label)
        ax.grid(axis="x", visible=False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=8)
    fig.suptitle(title, fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.07, 1, 0.92))
    fig.savefig(path)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="Draw F6, F7, F9 from aggregated sweeps.")
    ap.add_argument("--main", required=True, help="aggregate dir of the main sweep (summary.csv, runs.csv)")
    ap.add_argument("--small", help="aggregate dir of the three-mode small sweep")
    ap.add_argument("--out", default="reports/figures")
    ap.add_argument("--bar-rate", type=float, default=10.0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    summ = pd.read_csv(os.path.join(args.main, "summary.csv"))
    n = int(summ["n"].max())
    fig_degradation(summ, os.path.join(args.out, "F6_degradation_physical.png"),
                    f"F6. Physical metrics against disruption rate (independent vs full, {n} runs per point, 95% CI)")
    fig_cost(summ, os.path.join(args.out, "F7_degradation_cost.png"),
             f"F7. Computation and messages against disruption rate ({n} runs per point, 95% CI)")
    runs_dir = args.small or args.main
    runs = pd.read_csv(os.path.join(runs_dir, "runs.csv"))
    at_rate = runs[runs["rate"] == args.bar_rate]
    if at_rate.empty:
        print(f"no runs at rate {args.bar_rate:g}; F9 skipped (pass --small with a sweep that has it)")
        print(f"figures written to {args.out}")
        return
    n9 = int(at_rate.groupby("mode").size().max())
    fig_bars(runs, args.bar_rate, os.path.join(args.out, "F9_bars_operating_point.png"),
             f"F9. The modes at λ = {args.bar_rate:g} per hour ({n9} runs per mode, mean and 95% CI)")
    print(f"figures written to {args.out}")


if __name__ == "__main__":
    main()
