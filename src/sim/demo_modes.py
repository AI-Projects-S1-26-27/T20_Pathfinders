"""W4 check-in demo: one scenario, same seed, all three conditions.

    python -m src.sim.demo_modes                                  # viz_demo, 3 modes
    python -m src.sim.demo_modes --config configs/sim_default.yaml --modes independent full
    python -m src.sim.demo_modes --csv reports/checkin_modes.csv

Each mode runs through src.sim.run (the same code path as a normal run) and
writes logs/<run_id>_<mode>.jsonl, which R2's replay/multipane viewers read.
The table is computed from those logs only:

* delivered / failed: the run's sim_complete row;
* disruptions: closures + spikes from R1's generator (same seed, so the same
  disruptions in every mode);
* replans / negotiation msgs: R7's replan rows and replan_summary (D7 counts);
* solves / alloc msgs: R4's allocation_complete rows (centralized re-solves);
* planning CPU: replan + coordination CPU (independent/full) or solver CPU
  (centralized), in seconds (D9);
* last delivery: time of the last order_delivered row, from the run start.
"""
import argparse
import csv
import json
import logging
import os
import time
from typing import Any, Dict, List

import yaml

from src.sim.run import MODES, run_from_config

COLUMNS = ["mode", "delivered", "failed", "disruptions", "replans", "negotiation_msgs",
           "solves", "alloc_msgs", "planning_cpu_s", "last_delivery_s", "wall_s"]


def read_log(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def summarize(rows: List[Dict[str, Any]], mode: str, wall_s: float) -> Dict[str, Any]:
    by_event: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_event.setdefault(r["event"], []).append(r)
    done = by_event.get("sim_complete", [{}])[-1].get("payload", {})
    start = by_event["sim_start"][0]["t"]
    summary = (by_event.get("replan_summary") or [{"payload": {}}])[-1]["payload"]
    allocs = [r["payload"] for r in by_event.get("allocation_complete", [])]
    delivered_t = [r["t"] for r in by_event.get("order_delivered", [])]
    cpu = (summary.get("agent_cpu_seconds", 0.0) + summary.get("coordination_cpu_seconds", 0.0)
           + sum(a.get("cpu_seconds", 0.0) for a in allocs))
    return {
        "mode": mode,
        "delivered": done.get("delivered", 0),
        "failed": done.get("failed", 0),
        "disruptions": len(by_event.get("disruption", [])),
        "replans": len(by_event.get("replan", [])),
        "negotiation_msgs": summary.get("negotiation_messages", 0),
        "solves": len(allocs),
        "alloc_msgs": sum(a.get("messages", 0) for a in allocs),
        "planning_cpu_s": round(cpu, 2),
        "last_delivery_s": round(max(delivered_t) - start, 1) if delivered_t else None,
        "wall_s": round(wall_s, 1),
    }


def run_modes(config: Dict[str, Any], modes, out_dir: str = "logs") -> List[Dict[str, Any]]:
    base_id = str(config.get("run_id", "sim"))
    results = []
    for mode in modes:
        cfg = dict(config, mode=mode, run_id=f"{base_id}_{mode}",
                   log_path=os.path.join(out_dir, f"{base_id}_{mode}.jsonl"))
        t0 = time.perf_counter()
        log_path = run_from_config(cfg)
        wall = time.perf_counter() - t0
        results.append({**summarize(read_log(log_path), mode, wall), "log": log_path})
    return results


def print_table(results: List[Dict[str, Any]]) -> None:
    widths = {c: max(len(c), *(len(str(r[c])) for r in results)) for c in COLUMNS}
    print("  ".join(c.ljust(widths[c]) for c in COLUMNS))
    for r in results:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in COLUMNS))
    for r in results:
        print(f"log ({r['mode']}): {r['log']}")


def main() -> None:
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Run one scenario in every mode and compare.")
    ap.add_argument("--config", default="configs/viz_demo.yaml")
    ap.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    ap.add_argument("--seed", type=int, help="override the config's seed")
    ap.add_argument("--rate", type=float,
                    help="override disruption_rate_per_hour (e.g. 300 to force replans)")
    ap.add_argument("--out-dir", default="logs")
    ap.add_argument("--csv", help="also write the table to this CSV file")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if args.seed is not None:
        config["seed"] = args.seed
    if args.rate is not None:
        config["disruption_rate_per_hour"] = args.rate
    os.makedirs(args.out_dir, exist_ok=True)
    results = run_modes(config, args.modes, args.out_dir)
    print_table(results)
    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)


if __name__ == "__main__":
    main()
