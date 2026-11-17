"""W5 sweep launcher: many runs in parallel, seed-controlled and resumable.

    python -m src.sim.launcher configs/sweeps/pilot.yaml
    python -m src.sim.launcher configs/sweeps/pilot.yaml --workers 4
    python -m src.sim.launcher configs/sweeps/pilot.yaml --dry-run     # list runs only

A sweep file names the grid; every combination is one run:

    sweep_id: pilot
    configs: [configs/scenarios/large_*_s0.yaml]   # paths or globs (R1 config format)
    modes: [centralized, independent, full]
    seeds: [9001, 9002, 9003]          # each run's `seed` (world, disruptions, R6 noise)
    rates: [5.0, 10.0]                 # optional: disruption_rate_per_hour
    predictor: [true, false]           # optional: R6 forecast on / off
    overrides: {duration_seconds: 14400.0}   # optional: applied to every config
    out_dir: runs/pilot                # optional, default runs/<sweep_id>
    workers: 4                         # optional, default CPUs - 1

Output in out_dir:

* ``logs/<run_id>.jsonl``: the run's log, written as ``.jsonl.part`` and renamed
  only when the run finished, so a crash never leaves a log that looks complete;
* ``done/<run_id>.json``: summary row + hash of the run's full config. A run
  with a done marker for the same config hash is skipped on the next launch
  (resume). Change anything in its config and it runs again;
* ``failed/<run_id>.txt``: traceback of a run that crashed (retried next launch);
* ``results.csv``: one row per finished run, sorted by run_id.

Seeds: a run is fully determined by its config (incl. seed), never by the
worker it lands on or by the order runs finish in.
"""
import argparse
import copy
import csv
import glob
import hashlib
import itertools
import json
import logging
import multiprocessing
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml

from src.sim.check_log import check_rows
from src.sim.demo_modes import COLUMNS as METRIC_COLUMNS, read_log, summarize
from src.sim.run import MODES

SWEEP_KEYS = {"sweep_id", "configs", "modes", "seeds", "rates", "predictor",
              "overrides", "out_dir", "workers"}
RESULT_COLUMNS = (["run_id", "scenario", "seed", "rate", "predictor", "status"]
                  + METRIC_COLUMNS + ["log_errors", "config_hash", "log"])


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    config_path: str
    mode: str
    seed: int
    rate: Optional[float]          # None: keep the config's rate
    predictor: Optional[bool]      # None: keep the config's setting
    overrides: tuple               # ((key, value), ...) so the spec stays hashable
    out_dir: str


# -- sweep file -> runs -----------------------------------------------------
def load_sweep(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        sweep = yaml.safe_load(f) or {}
    unknown = set(sweep) - SWEEP_KEYS
    if unknown:
        raise ValueError(f"unknown keys in sweep: {sorted(unknown)}; allowed: {sorted(SWEEP_KEYS)}")
    for key in ("sweep_id", "configs", "modes", "seeds"):
        if not sweep.get(key):
            raise ValueError(f"sweep needs {key!r}")
    bad = set(sweep["modes"]) - set(MODES)
    if bad:
        raise ValueError(f"unknown modes {sorted(bad)}; allowed: {MODES}")
    return sweep


def expand_configs(patterns: List[str]) -> List[str]:
    paths: List[str] = []
    for pattern in patterns:
        found = sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern]
        if not found:
            raise FileNotFoundError(f"no config matches {pattern!r}")
        for p in found:
            if not os.path.isfile(p):
                raise FileNotFoundError(p)
            if p not in paths:
                paths.append(p)
    return paths


def make_run_id(config_path: str, mode: str, seed: int, rate: Optional[float],
                predictor: Optional[bool]) -> str:
    parts = [os.path.splitext(os.path.basename(config_path))[0], mode]
    if rate is not None:
        parts.append(f"r{rate:g}")
    parts.append(f"s{seed}")
    if predictor is not None:
        parts.append("pred" if predictor else "nopred")
    return "__".join(parts)


def plan_runs(sweep: Dict[str, Any]) -> List[RunSpec]:
    out_dir = sweep.get("out_dir") or os.path.join("runs", str(sweep["sweep_id"]))
    overrides = tuple(sorted((sweep.get("overrides") or {}).items()))
    rates = [float(r) for r in sweep["rates"]] if sweep.get("rates") else [None]
    preds = list(sweep.get("predictor") or [None])
    specs = []
    for path, mode, seed, rate, pred in itertools.product(
            expand_configs(sweep["configs"]), sweep["modes"], sweep["seeds"], rates, preds):
        if pred is not None and not isinstance(pred, bool):
            raise ValueError(f"predictor values must be true/false, got {pred!r}")
        specs.append(RunSpec(make_run_id(path, mode, int(seed), rate, pred), path, mode,
                             int(seed), rate, pred, overrides, out_dir))
    ids = [s.run_id for s in specs]
    if len(set(ids)) != len(ids):
        raise ValueError("two runs share a run_id (same config file name in two folders?)")
    return specs


def resolve_config(spec: RunSpec) -> Dict[str, Any]:
    """The exact config the run uses (log_path is set by the worker)."""
    with open(spec.config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config = copy.deepcopy(config)
    config.update(dict(spec.overrides))
    config.update(run_id=spec.run_id, mode=spec.mode, seed=spec.seed)
    if spec.rate is not None:
        config["disruption_rate_per_hour"] = spec.rate
    if spec.predictor is not None:
        config["predictor"] = spec.predictor
    config.pop("log_path", None)
    return config


def config_hash(config: Dict[str, Any]) -> str:
    blob = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# -- one run (in a worker process) ------------------------------------------
def _paths(spec: RunSpec) -> Dict[str, str]:
    return {
        "log": os.path.join(spec.out_dir, "logs", f"{spec.run_id}.jsonl"),
        "done": os.path.join(spec.out_dir, "done", f"{spec.run_id}.json"),
        "failed": os.path.join(spec.out_dir, "failed", f"{spec.run_id}.txt"),
    }


def _write_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def is_done(spec: RunSpec) -> bool:
    path = _paths(spec)["done"]
    if not (os.path.exists(path) and os.path.exists(_paths(spec)["log"])):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            marker = json.load(f)
    except (OSError, ValueError):
        return False
    return marker.get("config_hash") == config_hash(resolve_config(spec))


def execute(spec: RunSpec) -> Dict[str, Any]:
    """Run one spec; never raises (a crash becomes a failed/ file)."""
    from src.sim.run import run_from_config     # import inside the worker process

    paths = _paths(spec)
    base = {"run_id": spec.run_id, "scenario": os.path.basename(spec.config_path),
            "seed": spec.seed, "rate": spec.rate, "predictor": spec.predictor}
    t0 = time.perf_counter()
    try:
        config = resolve_config(spec)
        digest = config_hash(config)
        config["log_path"] = paths["log"] + ".part"
        part = run_from_config(config)
        os.replace(part, paths["log"])
        rows = read_log(paths["log"])
        metrics = summarize(rows, spec.mode, time.perf_counter() - t0)
        # W6: R1's log checker; errors are kept in the done marker, counted in results.csv
        log_errors, _ = check_rows(rows, config)
        row = {**base, **metrics, "status": "done", "log_errors": len(log_errors),
               "config_hash": digest, "log": paths["log"]}
        if log_errors:
            row["log_error_list"] = log_errors[:20]
        _write_atomic(paths["done"], json.dumps(row, indent=1))
        if os.path.exists(paths["failed"]):
            os.remove(paths["failed"])
        return row
    except Exception:
        _write_atomic(paths["failed"], traceback.format_exc())
        return {**base, "mode": spec.mode, "status": "failed",
                "wall_s": round(time.perf_counter() - t0, 1), "log": paths["failed"]}


def _quiet_worker() -> None:
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")


# -- the sweep --------------------------------------------------------------
def run_sweep(specs: List[RunSpec], workers: int = 1, echo=print) -> List[Dict[str, Any]]:
    todo = [s for s in specs if not is_done(s)]
    echo(f"{len(specs)} runs, {len(specs) - len(todo)} already done, {len(todo)} to run "
         f"on {workers} worker(s)")
    results = []
    if workers <= 1:
        for i, spec in enumerate(todo, 1):
            row = execute(spec)
            results.append(row)
            echo(f"[{i}/{len(todo)}] {row['status']:6} {row.get('wall_s', '?'):>6}s {spec.run_id}")
    elif todo:
        ctx = multiprocessing.get_context("spawn")    # same behaviour on Windows and Linux
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx,
                                 initializer=_quiet_worker) as pool:
            futures = {pool.submit(execute, s): s for s in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                results.append(row)
                echo(f"[{i}/{len(todo)}] {row['status']:6} {row.get('wall_s', '?'):>6}s "
                     f"{futures[fut].run_id}")
    return results


def collect_results(specs: List[RunSpec]) -> List[Dict[str, Any]]:
    rows = []
    for spec in specs:
        if is_done(spec):
            with open(_paths(spec)["done"], encoding="utf-8") as f:
                rows.append(json.load(f))
    return sorted(rows, key=lambda r: r["run_id"])


def write_results(rows: List[Dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    _quiet_worker()
    ap = argparse.ArgumentParser(description="Run a sweep of simulations in parallel.")
    ap.add_argument("sweep", help="sweep YAML file")
    ap.add_argument("--workers", type=int, help="override the sweep's workers")
    ap.add_argument("--dry-run", action="store_true", help="list the runs, run nothing")
    args = ap.parse_args()

    sweep = load_sweep(args.sweep)
    specs = plan_runs(sweep)
    workers = args.workers or int(sweep.get("workers") or max(1, (os.cpu_count() or 2) - 1))
    if args.dry_run:
        for s in specs:
            print(("done  " if is_done(s) else "todo  ") + s.run_id)
        print(f"{len(specs)} runs")
        return
    results = run_sweep(specs, workers)
    out_dir = specs[0].out_dir
    rows = collect_results(specs)
    write_results(rows, os.path.join(out_dir, "results.csv"))
    failed = [r for r in results if r["status"] == "failed"]
    print(f"{len(rows)}/{len(specs)} runs done; results: {os.path.join(out_dir, 'results.csv')}")
    flagged = [r["run_id"] for r in rows if r.get("log_errors")]
    if flagged:
        print(f"{len(flagged)} run(s) finished with log errors (log_errors column; details in "
              f"{out_dir}/done/<run_id>.json, or python -m src.sim.check_log <log>):")
        for run_id in flagged:
            print("  " + run_id)
    if failed:
        print(f"{len(failed)} failed this launch (tracebacks in {out_dir}/failed/; "
              "launch again to retry):")
        for r in failed:
            print("  " + r["run_id"])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
