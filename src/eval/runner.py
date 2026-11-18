"""Analyze completed JSONL runs and generate R8 tables, plots and report.

Usage:
  python -m src.eval.runner --logs runs/r8_main/logs \
      --configs configs/scenarios --out reports/r8 --map data/processed/secunderabad.graphml

A log without a matching scenario config is retained but config-dependent
metrics are marked unavailable; the runner never invents missing values.
"""
from __future__ import annotations
import argparse
import csv
import glob
import json
from pathlib import Path
import re
import yaml

from src.eval.metrics import read_jsonl, compute_run_metrics
from src.eval.statistics import summarize, paired_comparisons
from src.eval.plots import make_plots

METRICS = [
    "delivery_time_total_s", "delivery_time_mean_s", "distance_total_m", "waiting_idle_s",
    "congestion_peak_vehicles", "congestion_mean_overload",
    "jain_workload_fairness", "on_time_rate",
    "priority_weighted_lateness_s", "cpu_disruption_mean_s",
    "cpu_disruption_p95_s", "cpu_centralized_resolve_total_s",
    "cpu_contract_net_allocation_total_s", "messages_total",
]


def find_config(config_dir, scenario, seed):
    patterns = [
        str(Path(config_dir) / f"{scenario}_s{seed}.yaml"),
        str(Path(config_dir) / f"{scenario}_s*.yaml"),
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            # For sweep seeds that differ from the base config seed, use s0.
            preferred = [p for p in matches if p.endswith("_s0.yaml")]
            return preferred[0] if preferred else matches[0]
    return None


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_yaml(path):
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def analyze(log_dir, config_dir, out_dir, map_path=None):
    log_paths = sorted(glob.glob(str(Path(log_dir) / "**" / "*.jsonl"), recursive=True))
    if not log_paths:
        raise FileNotFoundError(f"No .jsonl logs found under {log_dir}")
    rows, failures = [], []
    for log_path in log_paths:
        try:
            raw = read_jsonl(log_path)
            meta = raw[0]
            cfg_path = find_config(config_dir, str(meta.get("scenario")), meta.get("seed"))
            cfg = _load_yaml(cfg_path)
            result = compute_run_metrics(raw, cfg, map_path=map_path)
            result["log_path"] = str(log_path)
            result["config_path"] = cfg_path or ""
            rows.append(result)
        except Exception as exc:
            failures.append({"log_path": str(log_path), "error": str(exc)})
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "run_metrics.csv", rows)
    _write_csv(out / "analysis_failures.csv", failures)
    summaries = summarize(rows, METRICS)
    paired = paired_comparisons(rows, METRICS)
    _write_csv(out / "summary_95ci.csv", summaries)
    _write_csv(out / "paired_seed_comparisons.csv", paired)
    make_plots(rows, out / "figures")
    report = [
        "# R8 Experiment Results (generated)",
        "",
        f"- Logs discovered: {len(log_paths)}",
        f"- Runs analyzed: {len(rows)}",
        f"- Logs with analysis errors: {len(failures)}",
        "- All tables and figures in this directory are regenerated from JSONL logs.",
        "- Do not interpret missing values as zero. Check metric_status columns.",
        "",
        "## Run coverage",
        "",
        "| Scenario | Rate (/h) | Mode | Runs |",
        "|---|---:|---|---:|",
    ]
    from collections import Counter
    coverage = Counter((r["scenario"], r["rate"], r["mode"]) for r in rows)
    for (scenario, rate, mode), n in sorted(coverage.items(), key=lambda x: str(x[0])):
        report.append(f"| {scenario} | {rate} | {mode} | {n} |")
    report += [
        "", "## Metric interpretation",
        "",
        "Use `summary_95ci.csv` for means and 95% bootstrap intervals. "
        "`paired_seed_comparisons.csv` reports paired differences using only common seeds.",
        "The current JSONL schema does not identify predicted conflicts and their later "
        "realized capacity violations, so unresolved-conflict count is intentionally null.",
        "Distance requires the configured GraphML file and a usable edge `length` attribute.",
        "Waiting time uses explicit wait_start/wait_end pairs; see metric_status_waiting. Legacy logs without these events report it unavailable.",
        "",
        "## Limitations and integrity checks",
        "",
        "Failed or incomplete runs remain visible in the log folder and should be reported. "
        "Do not discard failed runs silently. The main sweep must use identical scenario, seed, "
        "predictor and information-feed settings across the three modes.",
        "Pilot seeds must remain disjoint from test seeds. Success thresholds must be frozen "
        "in `docs/DECISIONS.md` before inspecting main-sweep outcomes.",
        "",
        "## Analysis errors",
        "",
    ]
    if failures:
        report.extend([f"- `{x['log_path']}`: {x['error']}" for x in failures])
    else:
        report.append("None.")
    (out / "results_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    manifest = {
        "logs_discovered": len(log_paths), "runs_analyzed": len(rows),
        "analysis_failures": len(failures),
        "metrics": METRICS,
        "bootstrap_seed": 20261118, "bootstrap_replicates": 5000,
        "map_path": map_path,
    }
    (out / "analysis_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--configs", default="configs/scenarios")
    ap.add_argument("--out", default="reports/r8")
    ap.add_argument("--map", default=None)
    args = ap.parse_args()
    print(json.dumps(analyze(args.logs, args.configs, args.out, args.map), indent=2))


if __name__ == "__main__":
    main()
