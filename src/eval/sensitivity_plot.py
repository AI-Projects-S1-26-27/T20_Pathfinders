"""Generate F8 from a separately executed R5 tau/delta_t sensitivity CSV.

Required columns: tau, delta_t, messages, unresolved_conflicts, delivery_time.
The script refuses incomplete data instead of fabricating sensitivity results.
"""
import argparse
import csv
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path")
    ap.add_argument("--out", default="reports/r8/figures/F8_sensitivity.png")
    args = ap.parse_args()
    with open(args.csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    required = {"tau", "delta_t", "messages", "unresolved_conflicts", "delivery_time"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"CSV must contain columns: {sorted(required)}")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for tau in sorted({r["tau"] for r in rows}, key=float):
        group = sorted((r for r in rows if r["tau"] == tau), key=lambda r: float(r["delta_t"]))
        x = [float(r["messages"]) for r in group]
        y1 = [float(r["unresolved_conflicts"]) for r in group]
        y2 = [float(r["delivery_time"]) for r in group]
        axes[0].plot(x, y1, marker="o", label=f"tau={tau}")
        axes[1].plot(x, y2, marker="o", label=f"tau={tau}")
    axes[0].set(xlabel="Messages", ylabel="Unresolved conflicts")
    axes[1].set(xlabel="Messages", ylabel="Total delivery time (s)")
    for ax in axes:
        ax.grid(True, alpha=.25)
        ax.legend()
    fig.suptitle("F8 — τ / Δt sensitivity")
    fig.tight_layout()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    main()
