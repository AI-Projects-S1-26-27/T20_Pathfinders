"""Sign counts of paired differences (full minus independent), and a by-layout split.

The bootstrap intervals in stats.py say how large the average difference is; this script says how
many of the matched runs point the same way. It reads runs.csv written by aggregate.py:

    python -m src.eval.pair_signs reports/r8/main
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

METRICS = ["delivery_time_mean_s", "violations_cap1", "overload_s_cap1", "messages_total", "planning_cpu_s"]
KEY = ["scenario_file", "run_seed", "rate"]


def paired_deltas(runs: pd.DataFrame, a: str = "full", b: str = "independent") -> pd.DataFrame:
    """One row per matched run: metric(a) - metric(b), keyed by scenario, run seed and rate."""
    left = runs[runs["mode"] == a].set_index(KEY)[METRICS]
    right = runs[runs["mode"] == b].set_index(KEY)[METRICS]
    return (left - right.reindex(left.index)).dropna().reset_index()


def sign_counts(deltas: pd.DataFrame, metric: str) -> dict:
    d = deltas[metric]
    return {"pairs": int(len(d)), "a_lower": int((d < 0).sum()), "a_higher": int((d > 0).sum()),
            "equal": int((d == 0).sum())}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("agg_dir")
    ap.add_argument("--a", default="full")
    ap.add_argument("--b", default="independent")
    args = ap.parse_args()
    runs = pd.read_csv(os.path.join(args.agg_dir, "runs.csv"))
    d = paired_deltas(runs, args.a, args.b)
    print(f"{args.a} minus {args.b}, matched by {KEY}")
    for m in METRICS:
        print(f"  {m}: {sign_counts(d, m)}")
    d["layout"] = d["scenario_file"].str.extract(r"_(clustered|hotspot|uniform)_")
    print("mean difference by layout:")
    print(d.groupby("layout")[["delivery_time_mean_s", "violations_cap1"]].mean().round(2).to_string())


if __name__ == "__main__":
    main()
