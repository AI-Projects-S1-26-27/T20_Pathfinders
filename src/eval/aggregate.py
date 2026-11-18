"""R8 aggregation: turn a launcher sweep (logs on disk) into tidy tables.

    python -m src.eval.aggregate configs/sweeps/main.yaml --out reports/r8/main

Reads the sweep file with the launcher's own ``load_sweep`` / ``plan_runs`` / ``resolve_config`` (so
the config used for every metric is exactly the config the run used), reads each finished run's log,
computes the metrics of src/eval/metrics.py and writes:

* ``runs.csv``      one row per run (the raw material of every figure and table);
* ``summary.csv``   per (mode, rate) and metric: n, mean, 95% bootstrap CI over runs;
* ``paired_<a>_vs_<b>.csv``  per rate and metric: mean difference and relative difference of mode a
                    against mode b, paired by (scenario file, seed, rate) with a bootstrap CI;
* ``missing.txt``   runs of the sweep that have no finished log. They are listed, never dropped
                    silently ("did not finish" is a result, pitfall list in the plan).
"""
import argparse
import json
import os
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

from src.eval.metrics import compute_metrics, load_road_info
from src.eval.stats import bootstrap_ci, intervals_overlap, paired_diff_ci, paired_ratio_ci
from src.sim.launcher import _paths, is_done, load_sweep, plan_runs, resolve_config

# the columns the figures and the threshold check use, in report order
KEY_METRICS = ["delivery_time_mean_s", "makespan_s", "weighted_delivery_time_s", "on_time_rate",
               "distance_km", "wait_s", "violations_cap1", "overload_s_cap1", "violations_d5",
               "jain", "planning_cpu_s", "cpu_per_decision_mean_s", "cpu_per_decision_p95_s",
               "messages_total", "negotiation_msgs", "replans", "failed"]
MODE_ORDER = ["centralized", "independent", "full"]


def read_log(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def split_scenario(file_name: str) -> Tuple[str, str, str]:
    """'medium_uniform_s0' -> ('medium', 'uniform', 's0')."""
    parts = file_name.split("_")
    return (parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")


def collect_sweep(sweep_path: str) -> Tuple[pd.DataFrame, List[str]]:
    sweep = load_sweep(sweep_path)
    road = load_road_info()
    rows, missing = [], []
    for spec in plan_runs(sweep):
        if not is_done(spec):
            missing.append(spec.run_id)
            continue
        config = resolve_config(spec)
        metrics = compute_metrics(read_log(_paths(spec)["log"]), config, road)
        scenario_file = os.path.splitext(os.path.basename(spec.config_path))[0]
        size, layout, instance = split_scenario(scenario_file)
        metrics.update(run_id=spec.run_id, sweep_id=sweep["sweep_id"], scenario_file=scenario_file,
                       size=size, layout=layout, instance=instance, run_seed=spec.seed,
                       rate=float(spec.rate) if spec.rate is not None else metrics["rate"],
                       predictor=spec.predictor)
        rows.append(metrics)
    return pd.DataFrame(rows), missing


def cell_summary(df: pd.DataFrame, metrics: Sequence[str] = KEY_METRICS) -> pd.DataFrame:
    out = []
    for (mode, rate), g in df.groupby(["mode", "rate"]):
        for m in metrics:
            mean, lo, hi = bootstrap_ci(g[m].to_numpy(dtype=float), seed=0)
            out.append({"mode": mode, "rate": rate, "metric": m, "n": int(g[m].notna().sum()),
                        "mean": mean, "ci_lo": lo, "ci_hi": hi})
    res = pd.DataFrame(out)
    res["mode_order"] = res["mode"].map({m: i for i, m in enumerate(MODE_ORDER)})
    return res.sort_values(["metric", "rate", "mode_order"]).drop(columns="mode_order").reset_index(drop=True)


def paired(df: pd.DataFrame, a: str, b: str, metrics: Sequence[str] = KEY_METRICS) -> pd.DataFrame:
    """Mode a against mode b, run by run (same scenario file, run seed and rate)."""
    key = ["scenario_file", "run_seed", "rate"]
    out = []
    for rate, g in df.groupby("rate"):
        left = g[g["mode"] == a].set_index(key).sort_index()
        right = g[g["mode"] == b].set_index(key).sort_index()
        common = left.index.intersection(right.index)
        if len(common) == 0:
            continue
        left, right = left.loc[common], right.loc[common]
        for m in metrics:
            av, bv = left[m].to_numpy(dtype=float), right[m].to_numpy(dtype=float)
            d_mean, d_lo, d_hi = paired_diff_ci(av, bv)
            r_mean, r_lo, r_hi = paired_ratio_ci(av, bv)
            a_ci, b_ci = bootstrap_ci(av), bootstrap_ci(bv)
            out.append({
                "a": a, "b": b, "rate": rate, "metric": m, "n_pairs": int(len(common)),
                "a_mean": a_ci[0], "b_mean": b_ci[0],
                "diff_mean": d_mean, "diff_lo": d_lo, "diff_hi": d_hi,
                "rel_mean": r_mean, "rel_lo": r_lo, "rel_hi": r_hi,
                "unpaired_ci_overlap": intervals_overlap(a_ci[1], a_ci[2], b_ci[1], b_ci[2]),
            })
    return pd.DataFrame(out)


def fmt(v: Any, digits: int = 3) -> str:
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        if np.isnan(v):
            return "n/a"
        return f"{v:.{digits}g}" if abs(v) < 1000 else f"{v:,.0f}"
    return str(v)


def to_markdown(df: pd.DataFrame, columns: Sequence[str] = None) -> str:
    cols = list(columns or df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_outputs(df: pd.DataFrame, missing: List[str], out_dir: str, pairs: Sequence[Tuple[str, str]]) -> None:
    os.makedirs(out_dir, exist_ok=True)
    df.sort_values(["rate", "mode", "scenario_file", "run_seed"]).to_csv(
        os.path.join(out_dir, "runs.csv"), index=False)
    summ = cell_summary(df)
    summ.to_csv(os.path.join(out_dir, "summary.csv"), index=False)
    for a, b in pairs:
        if a in set(df["mode"]) and b in set(df["mode"]):
            paired(df, a, b).to_csv(os.path.join(out_dir, f"paired_{a}_vs_{b}.csv"), index=False)
    with open(os.path.join(out_dir, "missing.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(missing) + ("\n" if missing else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate a launcher sweep into tidy tables.")
    ap.add_argument("sweep", help="sweep YAML file (the same one the launcher ran)")
    ap.add_argument("--out", required=True, help="output directory, e.g. reports/r8/main")
    ap.add_argument("--pair", action="append", default=[],
                    help="a:b to write a paired comparison of mode a against mode b (repeatable)")
    args = ap.parse_args()
    default_pairs = [("full", "independent"), ("full", "centralized"), ("independent", "centralized")]
    pairs = [tuple(p.split(":", 1)) for p in args.pair] or default_pairs
    df, missing = collect_sweep(args.sweep)
    if df.empty:
        raise SystemExit(f"no finished runs for {args.sweep}; run the launcher first")
    write_outputs(df, missing, args.out, pairs)
    print(f"{len(df)} runs aggregated, {len(missing)} missing -> {args.out}")


if __name__ == "__main__":
    main()
