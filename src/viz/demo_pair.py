"""Demo runs for the side-by-side viewer (R2, Weeks 5-7). NOT the project's experiment.

    python -m src.viz.demo_pair [--config configs/viz_demo.yaml] [--rate 120] [--prefix pair]
                                [--centralized [--centralized-timeout 600]]

This is a demo of the viewer (src/viz/multipane.py), nothing more. It runs the same
config, same seed and same disruption rate in each condition:

  (a) logs/<prefix>_noreplan.jsonl     R1's simulator alone, no planner, each order on the
                                       first vehicle whose hand-written route visits it
                                       (run.py's assign_orders). Mode label in the log:
                                       "no_replanning", not one of the plan's modes. With no
                                       planner a vehicle waits at a closed road.
  (b) logs/<prefix>_independent.jsonl  src.sim.run.run_from_config with mode "independent":
                                       R4's contract-net hands out orders, R7's AgentPlanner
                                       replans each vehicle locally (D* Lite).
  (c) logs/<prefix>_full.jsonl         the same with mode "full": (b) plus R5's trigger and
                                       negotiation after every replan.
  (d) logs/<prefix>_centralized.jsonl  only with --centralized: mode "centralized" (R4's
                                       OR-Tools re-solve on every event), run as
                                       `python -m src.sim.run` in a subprocess. It is
                                       stopped after --centralized-timeout seconds
                                       (default 600 = 10 min). The log is written to a
                                       .part file and renamed only if the run finishes, so
                                       a stopped run leaves no log behind.

(b), (c) and (d) go through src/sim/run.py unchanged; only run_id, mode,
disruption_rate_per_hour and log_path are overridden. (a) builds the environment here,
copying run_from_config's "route" allocation without the planner, because run.py has no
no-planner mode.

--centralized is meant for the 10-vehicle configs/viz_demo.yaml: R1's notes measure
~1.5-2 min at 10/h there and > 20 min for 20 vehicles (src/sim/README.md, open issue 2).

--prefix (default "pair") names the files and run_ids, so a second set (for example the
20-vehicle config, --prefix pair20) does not overwrite the first.

The default rate, 120 disruptions per hour, is a STRESS setting chosen so the panes show
something (closures, replans) within a few minutes. It is not an experiment rate; the
experiment's rates are set in the plan, not here.

Same seed does not mean the same disruptions in every run: the closure guard (D11) looks
at where vehicles still need to go, and the planners change that, so the random stream can
drift apart after the first rejected closure. The script prints how many disruption rows
each log shares with the first one, and how many all logs share.
"""
import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.sim.environment import DEFAULT_DURATION_S, DeliverySimEnvironment
from src.sim.run import assign_orders, build_order, build_vehicle, run_from_config
from src.sim.world import DEFAULT_MAP_PATH, World

DEFAULT_RATE = 120.0
DEFAULT_PREFIX = "pair"
CENTRALIZED_TIMEOUT_S = 600.0
RUNS = (
    # (file / run_id suffix after "<prefix>_", mode label in the log)
    ("noreplan", "no_replanning"),
    ("independent", "independent"),
    ("full", "full"),
)
CENTRALIZED = ("centralized", "centralized")


def run_config(config: Dict[str, Any], run_id: str, mode: str, rate: float,
               log_path: Path) -> Dict[str, Any]:
    """The config with only run_id, mode, rate and log_path replaced."""
    cfg = copy.deepcopy(config)
    cfg.update(run_id=run_id, mode=mode, disruption_rate_per_hour=float(rate),
               log_path=str(log_path))
    return cfg


def run_noreplan(cfg: Dict[str, Any]) -> Path:
    """R1's simulator alone: run_from_config's "route" allocation, no AgentPlanner."""
    world = World.from_defaults(cfg.get("map_path", DEFAULT_MAP_PATH),
                                seed=int(cfg.get("seed", 42)))
    env = DeliverySimEnvironment(world, cfg)
    vehicles = [build_vehicle(v) for v in cfg.get("vehicles", [])]
    orders = [build_order(o) for o in cfg.get("orders", [])]
    for order in orders:
        env.add_order(order)
    assignment = assign_orders(vehicles, orders)
    for vehicle in vehicles:
        env.add_vehicle(vehicle, assignment[vehicle.id])
    given = {o.id for mine in assignment.values() for o in mine}
    env.unassigned = [o for o in orders if o.id not in given]
    env.run(float(cfg.get("duration_seconds", DEFAULT_DURATION_S)))
    env.logger.close()
    return Path(env.log_path)


def run_centralized(cfg: Dict[str, Any], timeout_s: float) -> Tuple[Optional[Path], str]:
    """`python -m src.sim.run` on cfg in a subprocess, stopped after timeout_s.

    Returns (log path, "") if it finished, else (None, reason). The log goes to
    <log_path>.part and is renamed only on success; a stopped run's partial log is
    deleted, never presented as a log.
    """
    final = Path(cfg["log_path"])
    part = final.with_name(final.name + ".part")
    if part.exists():
        part.unlink()
    cfg = dict(cfg, log_path=str(part))
    fd, cfg_path = tempfile.mkstemp(suffix=".yaml", prefix="demo_pair_central_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        try:
            proc = subprocess.run([sys.executable, "-m", "src.sim.run", "--config", cfg_path],
                                  capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            reason = (f"stopped after {timeout_s:g} s ({timeout_s / 60:g} min) without "
                      f"finishing")
            if part.exists():
                with open(part, encoding="utf-8") as f:
                    rows = sum(1 for line in f if line.strip())
                last = _last_t(part)
                reason += (f"; its partial log ({rows} rows, sim clock reached "
                           f"{_clock(last)}) was deleted")
                part.unlink()
            return None, reason
        if proc.returncode != 0:
            if part.exists():
                part.unlink()
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
            return None, f"exit code {proc.returncode}: {tail[0]}"
        os.replace(part, final)
        return final, ""
    finally:
        os.unlink(cfg_path)


def _last_t(path: Path) -> Optional[float]:
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    last = float(json.loads(line)["t"])
                except (ValueError, KeyError):
                    pass          # a row cut off by the kill
    return last


def summarize(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    count: Dict[str, int] = {}
    for r in rows:
        count[r["event"]] = count.get(r["event"], 0) + 1
    done = [r["t"] for r in rows if r["event"] in ("order_delivered", "order_failed")]
    return {
        "rows": rows,
        "delivered": count.get("order_delivered", 0),
        "failed": count.get("order_failed", 0),
        "blocked_wait": count.get("blocked_wait", 0),
        "replan": count.get("replan", 0),
        "disruptions": count.get("disruption", 0),
        "finish_t": max(done) if done else None,
    }


def _clock(t) -> str:
    if t is None:
        return "n/a"
    s = int(round(t))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def disruption_keys(rows: List[dict]) -> List[tuple]:
    return [(r["t"], r["payload"]["type"], tuple(r["payload"]["edge"]))
            for r in rows if r["event"] == "disruption"]


def shared_prefix(*seqs) -> int:
    """How many leading items all sequences have in common."""
    n = 0
    for items in zip(*seqs):
        if any(x != items[0] for x in items[1:]):
            break
        n += 1
    return n


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Demo runs for the multipane viewer.")
    parser.add_argument("--config", default="configs/viz_demo.yaml")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE,
                        help="disruptions per hour (default 120, a stress setting)")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX,
                        help='file/run_id prefix: logs/<prefix>_noreplan.jsonl etc. '
                             '(default "pair")')
    parser.add_argument("--centralized", action="store_true",
                        help="also run mode centralized (R4), stopped after "
                             "--centralized-timeout seconds")
    parser.add_argument("--centralized-timeout", type=float, default=CENTRALIZED_TIMEOUT_S,
                        help="seconds before the centralized run is stopped (default 600)")
    args = parser.parse_args(argv)

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"DEMO of the viewer, not an experiment. config {args.config}, "
          f"seed {config.get('seed')}, rate {args.rate:g}/h (stress setting)")
    runs = list(RUNS) + ([CENTRALIZED] if args.centralized else [])
    summaries: Dict[str, Dict[str, Any]] = {}
    for suffix, mode in runs:
        run_id = f"{args.prefix}_{suffix}"
        cfg = run_config(config, run_id, mode, args.rate, log_dir / f"{run_id}.jsonl")
        t0 = time.perf_counter()
        if mode == "no_replanning":
            path = run_noreplan(cfg)
        elif mode == "centralized":
            path, reason = run_centralized(cfg, args.centralized_timeout)
            if path is None:
                print(f"{run_id:19s} mode={mode:14s} NO LOG: {reason} "
                      f"({time.perf_counter() - t0:.1f} s wall)")
                continue
        else:
            path = Path(run_from_config(cfg))
        s = summarize(path)
        summaries[run_id] = s
        print(f"{run_id:19s} mode={mode:14s} delivered {s['delivered']:3d}  "
              f"failed {s['failed']:3d}  blocked_wait {s['blocked_wait']:4d}  "
              f"replan {s['replan']:5d}  disruptions {s['disruptions']:4d}  "
              f"last order resolved {_clock(s['finish_t'])}  "
              f"({time.perf_counter() - t0:.1f} s wall) -> {path}")

    keys = {run_id: disruption_keys(s["rows"]) for run_id, s in summaries.items()}
    ids = list(keys)
    first = ids[0]
    for run_id in ids[1:]:
        same = shared_prefix(keys[first], keys[run_id])
        print(f"disruption rows shared by {first} and {run_id}: the first {same} of "
              f"{len(keys[first])} / {len(keys[run_id])}")
    everyone = shared_prefix(*keys.values())
    sizes = " / ".join(str(len(k)) for k in keys.values())
    print(f"disruption rows shared by all {len(ids)} logs: the first {everyone} of {sizes}"
          + ("" if all(everyone == len(k) for k in keys.values()) else
             " (the streams drift apart after that; see the docstring)"))


if __name__ == "__main__":
    main()
