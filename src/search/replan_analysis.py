"""Replan rate and latency vs disruption rate (R7, Week 7).

Reads simulation logs (one JSON object per line, docs/log_schema.md) written
with R7's AgentPlanner, and reports per run and per (mode, disruption rate):

* replans per hour: route switches caused by disruptions (``replan`` rows with
  trigger ``closure`` / ``traffic_spike``), also per vehicle-hour, plus every
  route change (unreachable drops, negotiation detours, new orders) and
  hysteresis keeps (``replan_kept``);
* replan latency: ``wall_seconds`` of those rows (median, p95, p99, max). Wall
  time, not ``cpu_seconds``, because on Windows the CPU timer moves in 15.6 ms
  steps (Week 6); ``cpu_seconds`` is summarised too, for Linux/Mac runs;
* per-rate means over seeds with a 95% confidence interval (Student t).

    python -m src.search.replan_analysis "logs/*.jsonl"            # analyse logs
    python -m src.search.replan_analysis --pilot                    # make small logs first

Writes reports/replan_runs.csv, reports/replan_summary.csv and
reports/figures/F_replan_rate_latency.png (unless --no-files).

``--pilot`` runs R3's scenario configs (configs/scenarios/) through R1's
run_from_config at D11's rates. The main experiment uses R8's frozen sweep;
point this script at those logs instead.
"""

import argparse
import csv
import glob
import json
import math
import os
import statistics
import tempfile
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

DISRUPTION_TRIGGERS = ("closure", "traffic_spike")
# How the planner did the work: a D* Lite repair of the existing legs, or a
# fresh multi-stop plan (new stop list, or stops dropped/reordered).
REPAIR_TRIGGERS = ("closure", "traffic_spike", "negotiation_yield")
REBUILD_TRIGGERS = ("new_order", "unreachable")
KINDS = (("disruption", DISRUPTION_TRIGGERS), ("new_order", ("new_order",)),
         ("negotiation_yield", ("negotiation_yield",)), ("unreachable", ("unreachable",)))
D11_RATES = (0.0, 2.0, 5.0, 10.0, 20.0)
OUT_DIR = "reports"
FIG_PATH = os.path.join("reports", "figures", "F_replan_rate_latency.png")

# t critical values, two-sided 95%, by degrees of freedom (beyond 30: 1.96)
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
        9: 2.262, 10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086, 25: 2.060, 30: 2.042}


def t95(df: int) -> float:
    if df <= 0:
        return float("nan")
    for k in sorted(_T95):
        if df <= k:
            return _T95[k]
    return 1.96


def pct(xs: Sequence[float], q: float) -> float:
    """Percentile with linear interpolation (q in [0, 1]); nan for no data."""
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def mean_ci(xs: Sequence[float]):
    """(mean, half-width of the 95% CI); half-width nan for fewer than 2 values."""
    xs = [x for x in xs if not math.isnan(x)]
    if not xs:
        return float("nan"), float("nan")
    m = statistics.fmean(xs)
    if len(xs) < 2:
        return m, float("nan")
    return m, t95(len(xs) - 1) * statistics.stdev(xs) / math.sqrt(len(xs))


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------
def summarise_run(path: str) -> Optional[Dict[str, Any]]:
    """Counts and latencies for one log. None if the log has no sim_start
    (not a simulation log) or no replan_summary (no R7 planner attached)."""
    head, start, end = None, None, None
    n_dis, triggers, kept = Counter(), Counter(), 0
    lat_wall, lat_cpu, kept_wall = [], [], []
    lat_by = {"repair": [], "rebuild": []}
    delivered = failed = 0
    fail_reasons = Counter()
    summary = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ev, p = r.get("event"), r.get("payload") or {}
            if head is None:
                head = r
            if ev == "sim_start":
                start = p
            elif ev == "sim_complete":
                end = r
            elif ev == "disruption":
                n_dis[p.get("type")] += 1
            elif ev == "replan":
                trig = p.get("trigger")
                triggers[trig] += 1
                if p.get("route_found", True) and p.get("wall_seconds") is not None:
                    kind = ("repair" if trig in REPAIR_TRIGGERS
                            else "rebuild" if trig in REBUILD_TRIGGERS else None)
                    if kind:
                        lat_by[kind].append(float(p["wall_seconds"]) * 1000)
                if trig in DISRUPTION_TRIGGERS and p.get("route_found", True):
                    if p.get("wall_seconds") is not None:
                        lat_wall.append(float(p["wall_seconds"]))
                    if p.get("cpu_seconds") is not None:
                        lat_cpu.append(float(p["cpu_seconds"]))
            elif ev == "replan_kept":
                kept += 1
                if p.get("wall_seconds") is not None:
                    kept_wall.append(float(p["wall_seconds"]))
            elif ev == "order_delivered":
                delivered += 1
            elif ev == "order_failed":
                failed += 1
                fail_reasons[p.get("reason")] += 1
            elif ev == "replan_summary":
                summary = p
    if start is None or summary is None:
        return None
    hours = float(start.get("duration_seconds", 0.0)) / 3600.0
    n_veh = int(start.get("n_vehicles", 0))
    disruption_replans = sum(triggers[t] for t in DISRUPTION_TRIGGERS)
    per_h = (lambda n: n / hours) if hours > 0 else (lambda n: float("nan"))
    per_vh = (lambda n: n / (hours * n_veh)) if hours > 0 and n_veh else (lambda n: float("nan"))
    return {
        "path": path,
        "run_id": head.get("run_id"),
        "mode": head.get("mode"),
        "scenario": head.get("scenario"),
        "seed": head.get("seed"),
        "rate": float(head.get("rate", 0.0)),
        "hours": hours,
        "n_vehicles": n_veh,
        "disruptions": sum(n_dis.values()),
        "closures": n_dis["closure"],
        "spikes": n_dis["traffic_spike"],
        "disruption_replans": disruption_replans,
        "replans_per_hour": per_h(disruption_replans),
        "replans_per_vehicle_hour": per_vh(disruption_replans),
        "route_changes_per_hour": per_h(sum(triggers.values())),
        "kept_per_hour": per_h(kept),
        "unreachable_replans": triggers["unreachable"],
        "negotiation_yields": triggers["negotiation_yield"],
        "new_order_replans": triggers["new_order"],
        "new_order_per_hour": per_h(triggers["new_order"]),
        "yields_per_hour": per_h(triggers["negotiation_yield"]),
        "repair_wall_ms": lat_by["repair"],
        "rebuild_wall_ms": lat_by["rebuild"],
        "lat_wall_ms": [x * 1000 for x in lat_wall],
        "lat_cpu_ms": [x * 1000 for x in lat_cpu],
        "kept_wall_ms": [x * 1000 for x in kept_wall],
        "delivered": delivered,
        "failed": failed,
        "failed_unreachable": fail_reasons["unreachable"],
        "complete": end is not None,
    }


# ---------------------------------------------------------------------------
# Many runs
# ---------------------------------------------------------------------------
def aggregate(runs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per (mode, rate): mean and 95% CI over runs (seeds and
    scenarios) of the rates; latency percentiles over every replan pooled."""
    groups = defaultdict(list)
    for r in runs:
        groups[(r["mode"], r["rate"])].append(r)
    out = []
    for (mode, rate), rs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        row = {"mode": mode, "rate": rate, "runs": len(rs)}
        for key in ("replans_per_hour", "replans_per_vehicle_hour", "route_changes_per_hour",
                    "kept_per_hour", "new_order_per_hour", "yields_per_hour"):
            m, h = mean_ci([r[key] for r in rs])
            row[key], row[key + "_ci95"] = m, h
        wall = [x for r in rs for x in r["lat_wall_ms"]]
        cpu = [x for r in rs for x in r["lat_cpu_ms"]]
        row["replans"] = len(wall)
        for kind in ("repair", "rebuild"):
            xs = [x for r in rs for x in r[kind + "_wall_ms"]]
            row[f"{kind}_n"] = len(xs)
            for q, label in ((0.5, "p50"), (0.95, "p95"), (0.99, "p99")):
                row[f"{kind}_{label}_ms"] = pct(xs, q)
        for name, xs in (("wall", wall), ("cpu", cpu)):
            for q, label in ((0.5, "p50"), (0.95, "p95"), (0.99, "p99"), (1.0, "max")):
                row[f"{name}_{label}_ms"] = pct(xs, q)
        zeros = sum(1 for x in cpu if x == 0.0)
        row["cpu_zero_share"] = zeros / len(cpu) if cpu else float("nan")
        out.append(row)
    return out


RUN_COLUMNS = ["run_id", "mode", "scenario", "seed", "rate", "hours", "n_vehicles", "disruptions",
               "closures", "spikes", "disruption_replans", "replans_per_hour",
               "replans_per_vehicle_hour", "route_changes_per_hour", "kept_per_hour",
               "unreachable_replans", "negotiation_yields", "new_order_replans",
               "new_order_per_hour", "yields_per_hour",
               "lat_wall_p50_ms", "lat_wall_p95_ms", "delivered", "failed", "failed_unreachable",
               "complete", "path"]


def write_csv(path: str, rows: List[Dict[str, Any]], columns: List[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(columns)
        for r in rows:
            w.writerow([_fmt(r.get(c)) for c in columns])


def _fmt(x):
    if isinstance(x, float):
        return "" if math.isnan(x) else round(x, 6)
    return x


def run_rows(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for r in runs:
        row = dict(r)
        row["lat_wall_p50_ms"] = pct(r["lat_wall_ms"], 0.5)
        row["lat_wall_p95_ms"] = pct(r["lat_wall_ms"], 0.95)
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------
KIND_SERIES = (("replans_per_hour", "closure / spike repair", "#2a78d6"),
               ("new_order_per_hour", "new order (rebuild)", "#eb6834"),
               ("yields_per_hour", "negotiation detour", "#1baf7a"))   # categorical slots 1-3
LAT_COLORS = {"repair": "#2a78d6", "rebuild": "#eb6834"}


def plot(summary: List[Dict[str, Any]], runs: List[Dict[str, Any]], path: str = FIG_PATH) -> str:
    """One panel per mode: route changes per hour by kind vs disruption rate
    (mean, 95% CI over runs); last panel: replan latency by how the planner
    did the work (D* Lite repair vs fresh rebuild), pooled over modes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    modes = [m for m in ("independent", "full") if any(s["mode"] == m for s in summary)]
    modes += sorted({s["mode"] for s in summary} - set(modes))
    rates = sorted({s["rate"] for s in summary})
    xpos = {r: i for i, r in enumerate(rates)}
    fig, axes = plt.subplots(1, len(modes) + 1, figsize=(4.2 * (len(modes) + 1), 3.8))
    ymax = 0.0
    for ax, m in zip(axes, modes):
        rows = sorted((s for s in summary if s["mode"] == m), key=lambda s: s["rate"])
        for key, label, c in KIND_SERIES:
            ys = [s[key] for s in rows]
            es = [0 if math.isnan(s[key + "_ci95"]) else s[key + "_ci95"] for s in rows]
            if not any(y > 0 for y in ys):
                continue
            ax.errorbar([xpos[s["rate"]] for s in rows], ys, yerr=es, color=c, marker="o",
                        ms=5, lw=2, capsize=3, label=label)
            ymax = max(ymax, max(y + e for y, e in zip(ys, es)))
        ax.set_title(f"{m}: route changes per hour", loc="left", fontsize=10)
        ax.set_ylabel("per run-hour (mean, 95% CI)")
        if ax.get_legend_handles_labels()[0]:
            ax.legend(frameon=False, fontsize=8, loc="center right")
    for ax in axes[:len(modes)]:
        ax.set_ylim(0, ymax * 1.1 if ymax > 0 else 1)
    lat = axes[-1]
    off = {"repair": -0.15, "rebuild": 0.15}
    for kind, c in LAT_COLORS.items():
        data = [[x for r in runs if r["rate"] == rate for x in r[kind + "_wall_ms"]]
                for rate in rates]
        keep = [(xpos[rate] + off[kind], d) for rate, d in zip(rates, data) if d]
        if not keep:
            continue
        bp = lat.boxplot([d for _, d in keep], positions=[p for p, _ in keep], widths=0.25,
                         whis=(5, 95), showfliers=False, patch_artist=True)
        for box in bp["boxes"]:
            box.set(facecolor=c, alpha=0.35, edgecolor=c)
        for k in ("whiskers", "caps", "medians"):
            for line in bp[k]:
                line.set(color=c, linewidth=1.5)
        lat.plot([], [], color=c, lw=6, alpha=0.5,
                 label="D* Lite repair" if kind == "repair" else "fresh rebuild")
    lat.set_title("Replan latency, wall time", loc="left", fontsize=10)
    lat.set_ylabel("ms, log scale (median, box 25-75%, 5-95%)")
    lat.set_yscale("log")
    lat.legend(frameon=False, fontsize=8)
    for ax in axes:
        ax.set_xticks(range(len(rates)))
        ax.set_xticklabels([f"{r:g}" for r in rates])
        ax.set_xlim(-0.5, len(rates) - 0.5)
        ax.set_xlabel("disruptions per hour (D11)")
        ax.grid(axis="y", color="#e4e3dd", linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Pilot logs
# ---------------------------------------------------------------------------
def make_pilot_logs(out_dir: str, scenarios: Sequence[str], seeds: Sequence[int],
                    rates: Sequence[float], modes: Sequence[str],
                    planner: Optional[Dict[str, Any]] = None) -> List[str]:
    """Run R3's scenario configs at each rate and mode; returns the log paths."""
    import yaml
    from src.sim.run import run_from_config

    paths = []
    for scen in scenarios:
        for seed in seeds:
            with open(os.path.join("configs", "scenarios", f"{scen}_s{seed}.yaml"),
                      encoding="utf-8") as f:
                base = yaml.safe_load(f)
            for rate in rates:
                for mode in modes:
                    cfg = dict(base)
                    run_id = f"{scen}_s{seed}_r{rate:g}_{mode}"
                    cfg.update(mode=mode, disruption_rate_per_hour=float(rate), run_id=run_id,
                               log_path=os.path.join(out_dir, run_id + ".jsonl"))
                    if planner:
                        cfg["planner"] = dict(planner)
                    paths.append(run_from_config(cfg))
    return paths


def analyse(paths: Iterable[str]):
    runs = [r for r in (summarise_run(p) for p in sorted(paths)) if r is not None]
    return runs, aggregate(runs)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("logs", nargs="*", help='log files or globs, e.g. "logs/*.jsonl"')
    ap.add_argument("--pilot", action="store_true", help="make pilot logs first")
    ap.add_argument("--scenarios", default="small_uniform,small_clustered,small_hotspot")
    ap.add_argument("--seeds", default="7,8,9", help="pilot seeds (keep apart from the main sweep)")
    ap.add_argument("--rates", default=",".join(f"{r:g}" for r in D11_RATES))
    ap.add_argument("--modes", default="independent,full")
    ap.add_argument("--no-files", action="store_true")
    args = ap.parse_args(argv)

    paths = [p for g in args.logs for p in (glob.glob(g) or [g])]
    if args.pilot:
        out = tempfile.mkdtemp(prefix="replan_pilot_")
        paths += make_pilot_logs(out, args.scenarios.split(","),
                                 [int(s) for s in args.seeds.split(",")],
                                 [float(r) for r in args.rates.split(",")],
                                 args.modes.split(","))
        print(f"pilot logs in {out}")
    if not paths:
        ap.error("give log files or --pilot")
    runs, summary = analyse(paths)
    if not runs:
        ap.error("no simulation logs with an R7 replan_summary found")

    print(f"{len(runs)} runs")
    print(f"{'mode':<12}{'rate/h':>7}{'runs':>5}{'disr/h':>8}{'+-95%':>7}{'new/h':>7}"
          f"{'yield/h':>8}{'kept/h':>7}{'repair n':>9}{'p50':>7}{'p95':>7}"
          f"{'rebuild n':>10}{'p50':>7}{'p95':>7}   (latency ms)")
    for s in summary:
        print(f"{str(s['mode']):<12}{s['rate']:>7g}{s['runs']:>5}{s['replans_per_hour']:>8.2f}"
              f"{s['replans_per_hour_ci95']:>7.2f}{s['new_order_per_hour']:>7.2f}"
              f"{s['yields_per_hour']:>8.2f}{s['kept_per_hour']:>7.2f}{s['repair_n']:>9}"
              f"{s['repair_p50_ms']:>7.2f}{s['repair_p95_ms']:>7.2f}{s['rebuild_n']:>10}"
              f"{s['rebuild_p50_ms']:>7.2f}{s['rebuild_p95_ms']:>7.2f}")
    if not args.no_files:
        write_csv(os.path.join(OUT_DIR, "replan_runs.csv"), run_rows(runs), RUN_COLUMNS)
        write_csv(os.path.join(OUT_DIR, "replan_summary.csv"), summary, list(summary[0].keys()))
        print(f"wrote {OUT_DIR}/replan_runs.csv, {OUT_DIR}/replan_summary.csv, "
              f"{plot(summary, runs)}")
    return runs, summary


if __name__ == "__main__":
    main()
