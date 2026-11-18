"""Script-generated R8 figures F6, F7 and F9."""
from pathlib import Path
from collections import defaultdict
import math


def make_plots(rows, out_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Install matplotlib to generate R8 figures") from exc

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    modes = ["centralized", "independent", "full"]
    labels = {"centralized": "Centralized", "independent": "Independent", "full": "Full"}
    rates = sorted({float(r["rate"]) for r in rows if r.get("rate") is not None})
    scenarios = sorted({str(r["scenario"]) for r in rows})

    def cell_values(metric, mode, rate):
        return [float(r[metric]) for r in rows
                if r.get("mode") == mode and float(r.get("rate", -1)) == rate
                and r.get(metric) is not None and math.isfinite(float(r[metric]))]

    def mean_ci(vals):
        if not vals:
            return None, None, None
        import statistics, random
        mean = statistics.mean(vals)
        if len(vals) < 2:
            return mean, None, None
        rnd = random.Random(20261118)
        boots = sorted(statistics.mean([vals[rnd.randrange(len(vals))] for _ in vals])
                       for _ in range(2000))
        return mean, boots[49], boots[1949]

    physical = [
        ("delivery_time_total_s", "Total delivery time (s)"),
        ("waiting_idle_s", "Waiting / idle time (s)"),
        ("congestion_mean_overload", "Mean overload above edge capacity"),
        ("jain_workload_fairness", "Jain workload fairness"),
        ("on_time_rate", "On-time rate"),
    ]
    for scenario in scenarios:
        fig, axes = plt.subplots(len(physical), 1, figsize=(8, 3.1 * len(physical)), squeeze=False)
        for ax, (metric, title) in zip(axes[:, 0], physical):
            for mode in modes:
                ys, lows, highs, xs = [], [], [], []
                for rate in rates:
                    vals = [float(r[metric]) for r in rows
                            if r.get("scenario") == scenario and r.get("mode") == mode
                            and float(r.get("rate", -1)) == rate and r.get(metric) is not None]
                    m, lo, hi = mean_ci(vals)
                    if m is not None:
                        xs.append(rate); ys.append(m)
                        lows.append(m-lo if lo is not None else 0)
                        highs.append(hi-m if hi is not None else 0)
                if xs:
                    ax.errorbar(xs, ys, yerr=[lows, highs], marker="o", capsize=3,
                                label=labels[mode])
            ax.set_title(title); ax.set_xlabel("Disruptions per simulated hour")
            ax.grid(True, alpha=.25)
        axes[0, 0].legend()
        fig.suptitle(f"F6 — Physical metrics: {scenario}")
        fig.tight_layout()
        fig.savefig(out / f"F6_physical_{scenario}.png", dpi=180)
        plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(8, 8))
    for mode in modes:
        for metric, label, marker in [
            ("cpu_disruption_mean_s", "CPU mean (s)", "o"),
            ("messages_total", "Messages total", "s"),
        ]:
            xs, ys, lo, hi = [], [], [], []
            for rate in rates:
                vals = [float(r[metric]) for r in rows if r.get("mode") == mode
                        and float(r.get("rate", -1)) == rate and r.get(metric) is not None]
                m, l, h = mean_ci(vals)
                if m is not None:
                    xs.append(rate); ys.append(m); lo.append(m-l if l is not None else 0)
                    hi.append(h-m if h is not None else 0)
            if xs:
                ax = axes[0] if metric.startswith("cpu") else axes[1]
                ax.errorbar(xs, ys, yerr=[lo, hi], marker=marker, capsize=3, label=labels[mode])
    axes[0].set_title("CPU time per disruption event")
    axes[1].set_title("Messages by event type (total)")
    for ax in axes:
        ax.set_xlabel("Disruptions per simulated hour"); ax.grid(True, alpha=.25); ax.legend()
    fig.suptitle("F7 — Computational degradation")
    fig.tight_layout(); fig.savefig(out / "F7_computational.png", dpi=180); plt.close(fig)

    # F9 at the highest configured rate; scenario means, with no hidden selection.
    if rates:
        rate = max(rates)
        metrics = [("delivery_time_total_s", "Delivery time (s)"),
                   ("cpu_disruption_mean_s", "CPU / event (s)"),
                   ("messages_total", "Messages")]
        fig, axes = plt.subplots(1, 3, figsize=(12, 4))
        for ax, (metric, title) in zip(axes, metrics):
            means = []
            for mode in modes:
                vals = [float(r[metric]) for r in rows if r.get("mode") == mode
                        and float(r.get("rate", -1)) == rate and r.get(metric) is not None]
                means.append(sum(vals)/len(vals) if vals else 0)
            ax.bar([labels[m] for m in modes], means)
            ax.set_title(title); ax.tick_params(axis="x", rotation=20); ax.grid(axis="y", alpha=.2)
        fig.suptitle(f"F9 — Fixed operating point: {rate:g} disruptions/hour")
        fig.tight_layout(); fig.savefig(out / "F9_fixed_operating_point.png", dpi=180)
        plt.close(fig)
