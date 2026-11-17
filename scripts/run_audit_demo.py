"""
R3 · Reproducible audit demo: simulate committed scenario files with a simple driver, then audit.

    python scripts/run_audit_demo.py
    python scripts/run_audit_demo.py --scenarios small_clustered_s0 medium_hotspot_s3 --rate 10

For each scenario in configs/scenarios/ it
  1. gives every vehicle a route: orders sorted by (tw_end, priority, id), dealt round-robin to the
     vehicles in config order, each vehicle visiting its share in that order and returning to its
     depot along free-flow shortest paths (R2's graph). This is a stand-in driver, not R4/R7's
     allocation, which is not wired to the scenario files yet;
  2. runs R1's simulator (src.sim.run.run_from_config) with disruption_rate_per_hour = --rate and
     the scenario's own seed, writing the log to logs/audit_demo/<scenario>.jsonl (git-ignored);
  3. audits the log against the unmodified scenario file (src.kr.audit) with the map,
and writes reports/audit_demo.md and reports/audit_demo.json.

Same inputs give the same logs and counts on every run (the driver is deterministic and R1's
simulator is seeded); tests/test_audit.py::test_audit_demo_is_reproducible checks this.
Exit code 0 = every run audited clean, 1 = violations found.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ["small_clustered_s0", "medium_hotspot_s3"]


def drive_routes(cfg: dict, G) -> dict:
    """The stand-in driver: returns cfg with every vehicle's route filled in (cfg is copied)."""
    import networkx as nx

    from src.sim.run import build_order

    cfg = json.loads(json.dumps(cfg))
    orders = sorted(
        (build_order(o) for o in cfg["orders"]), key=lambda o: (o.tw_end, o.priority, o.id)
    )
    vehicles = cfg["vehicles"]
    for i, v in enumerate(vehicles):
        mine = orders[i::len(vehicles)]  # noqa: E203
        seq = [v["depot_node"]] + [o.node for o in mine] + [v["depot_node"]]
        route = [seq[0]]
        for a, b in zip(seq, seq[1:]):
            if a != b:
                route += nx.shortest_path(G, a, b, weight="free_flow_time")[1:]
        v["route"] = route
    # Since R1's #38 the simulator defaults to R4's contract-net, which ignores hand-written
    # routes; the stand-in driver needs W4's "route" allocation to drive the routes above.
    cfg["allocation"] = "route"
    return cfg


def run_one(name: str, rate: float, log_dir: Path, graphs: dict) -> dict:
    import yaml

    from src.kr.audit import audit_log, load_graph_for, read_log
    from src.sim.run import run_from_config

    path = ROOT / "configs" / "scenarios" / f"{name}.yaml"
    scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
    if scenario["map_path"] not in graphs:
        graphs[scenario["map_path"]] = load_graph_for(scenario)
    G = graphs[scenario["map_path"]]
    cfg = drive_routes(scenario, G)
    cfg["disruption_rate_per_hour"] = rate
    log_dir.mkdir(parents=True, exist_ok=True)
    cfg["log_path"] = str(log_dir / f"{name}.jsonl")
    rows = read_log(run_from_config(cfg))
    events = Counter(r["event"] for r in rows)
    rep = audit_log(rows, scenario, G)  # audited against the committed, unmodified file
    return {
        "scenario": name,
        "disruption_rate_per_hour": rate,
        "orders": len(scenario["orders"]),
        "vehicles": len(scenario["vehicles"]),
        "delivered": events["order_delivered"],
        "failed": events["order_failed"],
        "disruptions": events["disruption"],
        "road_entries": events["edge_enter"],
        "checked": dict(rep.checked),
        "violations": rep.counts(),
        "total_violations": len(rep.violations),
        "log": cfg["log_path"],
    }


def report(results: list[dict]) -> str:
    head = (
        "| Scenario | Vehicles | Orders | Delivered | Failed | Disruptions | Road entries "
        "checked | Deliveries checked | Violations |"
    )
    lines = [
        "# Audit demo (scripts/run_audit_demo.py)",
        "",
        "Stand-in driver (orders by deadline, dealt round-robin), R1's simulator, "
        "then `src.kr.audit` with the map. Failed orders are missed windows or the "
        "horizon, which the simulator fails instead of serving late; they are not "
        "violations.",
        "",
        head,
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['scenario']} | {r['vehicles']} | {r['orders']} | {r['delivered']} | "
            f"{r['failed']} | {r['disruptions']} | {r['checked'].get('KR-4', 0)} | "
            f"{r['checked'].get('KR-12', 0)} | {r['total_violations']} |"
        )
    rate = {r["disruption_rate_per_hour"] for r in results}
    lines += [
        "",
        f"Disruption rate: {', '.join(f'{x:g}' for x in sorted(rate))} per hour. "
        "Violations by check:",
        "",
    ]
    keys = list(results[0]["violations"]) if results else []
    lines += ["| Scenario | " + " | ".join(keys) + " |", "|---" * (len(keys) + 1) + "|"]
    for r in results:
        lines.append(
            f"| {r['scenario']} | " + " | ".join(str(r["violations"][k]) for k in keys) + " |"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scenarios", nargs="+", default=DEFAULT, help="names in configs/scenarios/ without .yaml"
    )
    ap.add_argument("--rate", type=float, default=10.0, help="disruptions per hour")
    ap.add_argument("--log-dir", default="logs/audit_demo")
    ap.add_argument("--md", default="reports/audit_demo.md")
    ap.add_argument("--json", default="reports/audit_demo.json")
    a = ap.parse_args(argv)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    graphs: dict = {}
    results = [run_one(n, a.rate, Path(a.log_dir), graphs) for n in a.scenarios]
    for r in results:
        print(
            f"{r['scenario']}: delivered {r['delivered']}/{r['orders']}, "
            f"{r['disruptions']} disruptions, {r['total_violations']} violations"
        )
    for out, text in (
        (a.md, report(results)),
        (
            a.json,
            json.dumps([{k: v for k, v in r.items() if k != "log"} for r in results], indent=1),
        ),
    ):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes((text if text.endswith("\n") else text + "\n").encode("utf-8"))
    return 0 if all(r["total_violations"] == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
