"""
R3 · Week 5: constraint-violation audit. Scans any simulation log (R1's JSONL, docs/log_schema.md)
against its scenario config and reports violations of the hard rules in the table below.
Week 7 target: zero violations on these checks in every run.

    python -m src.kr.audit logs/run.jsonl --config configs/scenarios/medium_hotspot_s0.yaml
    python -m src.kr.audit logs/*.jsonl --config-dir configs/scenarios --md reports/audit.md

Exit code 0 = no violations, 1 = violations found, 2 = a log could not be audited.

What is checked (per delivered order / per road entered), with R1's exact semantics:
    KR-12   an order is delivered at most once, and not both delivered and failed
    NODE    an order is delivered at its own node, where the vehicle actually is
    KR-11   service starts no earlier than release_time  (start = t_delivered - service_time)
    KR-7    service starts no earlier than tw_start (the vehicle waits instead)
    KR-8    service starts no later than tw_end
    KR-1    initial load + everything a vehicle delivered <= its capacity (R1 never unloads)
    KR-4    no vehicle enters a road while it is closed (start <= t < expiry of a logged closure)
    KR-3    no vehicle enters a road whose class its allowed_road_classes excludes (needs the map)
    KR-6    no large vehicle enters a restricted zone during its hours (kr_zones; needs map)
    KR-5    every road entered exists in that direction in the map (no wrong-way driving; needs map)
    KR-9    service starts no earlier than the vehicle's arrival at the order's node
    PATH    each road entered starts at the node the vehicle is at (log integrity)
Orders created during the run (dyn_*) carry no window in the log, so only KR-12 applies to them.

NOT checked (say so when reporting "zero violations"):
    KR-10, KR-10a  back at the depot by shift_end: R1's Vehicle has no shift_end and none of the
                   committed scenario files sets one, so there is nothing to check against
    KR-2, KR-13, KR-14, KR-15   allocation / ordering decisions (and KR-13 is soft): the log shows
                   their outcome, not the decision; R3's unit tests cover them
    KR-16          replan after a closure: covered indirectly, since KR-4 catches any vehicle that
                   drives onto a closed road instead of replanning
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .ontology import NO_TW_END, NO_TW_START
from .rules import _road_class

EPS = 0.01  # R1's logger rounds t to 2 decimals, so t - service_time can be off by up to 0.005 s
CHECKS = (
    "KR-12",
    "NODE",
    "KR-11",
    "KR-7",
    "KR-8",
    "KR-9",
    "KR-1",
    "KR-4",
    "KR-5",
    "KR-3",
    "KR-6",
    "PATH",
)


@dataclass
class Violation:
    rule: str
    t: float
    agent_id: str
    detail: str

    def as_dict(self):
        return {"rule": self.rule, "t": self.t, "agent_id": self.agent_id, "detail": self.detail}


@dataclass
class AuditReport:
    run_id: str
    checked: Counter = field(default_factory=Counter)  # how many times each rule was checked
    violations: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # rules that could not be checked, and why

    @property
    def ok(self) -> bool:
        return not self.violations

    def counts(self) -> dict:
        c = Counter(v.rule for v in self.violations)
        return {r: c.get(r, 0) for r in CHECKS}

    def as_dict(self):
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "checked": dict(self.checked),
            "violations_by_rule": self.counts(),
            "violations": [v.as_dict() for v in self.violations],
            "skipped": self.skipped,
        }


def read_log(path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def audit_log(rows: list[dict], cfg: dict, G=None, zones: Optional[Iterable] = None) -> AuditReport:
    """Audit one run. rows: parsed JSONL rows; cfg: the run's scenario config (vehicles, orders).

    G (R2's graph, MultiDiGraph) enables KR-3 and KR-6; zones default to cfg["kr_zones"].
    """
    from .planner_rules import zones_from_config
    from src.sim.run import build_order, build_vehicle

    run_id = rows[0].get("run_id", "?") if rows else cfg.get("run_id", "?")
    rep = AuditReport(run_id)
    orders = {o.id: o for o in (build_order(d) for d in cfg.get("orders", []))}
    vehicles = {v.id: v for v in (build_vehicle(d) for d in cfg.get("vehicles", []))}
    zones = list(zones) if zones is not None else zones_from_config(cfg)
    if G is None:
        rep.skipped.append("KR-3, KR-5, KR-6: no map given")

    def bad(rule, row, detail):
        rep.violations.append(
            Violation(rule, float(row.get("t", 0.0)), row.get("agent_id", "?"), detail)
        )

    position = {vid: v.current_node for vid, v in vehicles.items()}
    arrived: dict = {}  # vid -> time of its last edge_exit (arrival at its current node)
    delivered_load = defaultdict(float)
    delivered_by: dict = {}
    failed: set = set()
    closures = defaultdict(list)  # (u, v, key) -> [(start, expiry)]

    for row in rows:
        ev, t, vid, p = (
            row.get("event"),
            float(row.get("t", 0.0)),
            row.get("agent_id"),
            row.get("payload", {}) or {},
        )

        if ev == "disruption" and p.get("type") == "closure":
            closures[tuple(p["edge"])].append((t, float(p["expiry"])))

        elif ev == "edge_enter":
            u, v, key = p["edge"]
            edge = (u, v, key)
            if vid in position:
                rep.checked["PATH"] += 1
                if position[vid] is not None and position[vid] != u:
                    bad("PATH", row, f"entered {u}->{v} while at node {position[vid]}")
            rep.checked["KR-4"] += 1
            for start, expiry in closures.get(edge, []):
                if start <= t < expiry:
                    bad(
                        "KR-4",
                        row,
                        f"entered closed road {u}->{v} (key {key}), closed "
                        f"{start:.0f}-{expiry:.0f}s",
                    )
            veh = vehicles.get(vid)
            if G is not None:
                rep.checked["KR-5"] += 1
                if not G.has_edge(u, v, key):
                    bad("KR-5", row, f"road {u}->{v} (key {key}) does not exist in that direction")
            if G is not None and veh is not None and G.has_edge(u, v, key):
                from .ontology import allowed_classes_of, class_of

                rep.checked["KR-3"] += 1
                cls = _road_class(G[u][v][key])
                if cls not in allowed_classes_of(veh):
                    bad("KR-3", row, f"road class '{cls}' not allowed for {vid} on {u}->{v}")
                vclass = class_of(veh)
                for z in zones:
                    rep.checked["KR-6"] += 1
                    if v in z.nodes and z.restricts(vclass, t):
                        bad("KR-6", row, f"{vclass} vehicle {vid} entered zone {z.id} at node {v}")

        elif ev == "edge_exit":
            position[vid] = p.get("node")
            arrived[vid] = t

        elif ev == "order_failed":
            failed.add(p.get("order_id"))

        elif ev == "order_delivered":
            oid = p.get("order_id")
            rep.checked["KR-12"] += 1
            if oid in delivered_by:  # report once as KR-12; its load is not counted again
                bad("KR-12", row, f"{oid} delivered again (first by {delivered_by[oid]})")
                continue
            delivered_by[oid] = vid
            o = orders.get(oid)
            demand = float(p.get("demand", o.demand if o else 0.0))
            if vid in vehicles:
                rep.checked["KR-1"] += 1
                veh = vehicles[vid]
                delivered_load[vid] += demand
                if veh.load + delivered_load[vid] > veh.capacity + EPS:
                    bad(
                        "KR-1",
                        row,
                        f"{vid} has delivered {veh.load + delivered_load[vid]:g} "
                        f"> capacity {veh.capacity:g}",
                    )
            if o is None:
                continue  # dyn_* order: nothing more is known about it
            rep.checked["NODE"] += 1
            if p.get("node") != o.node or (vid in position and position[vid] != o.node):
                bad(
                    "NODE",
                    row,
                    f"{oid} (node {o.node}) delivered at node "
                    f"{p.get('node')}, vehicle at {position.get(vid)}",
                )
            start = t - o.service_time
            if vid in arrived:
                rep.checked["KR-9"] += 1
                if start < arrived[vid] - EPS:
                    bad(
                        "KR-9",
                        row,
                        f"{oid} service started {start:.0f}s, before the vehicle "
                        f"arrived at {arrived[vid]:.0f}s",
                    )
            rep.checked["KR-11"] += 1
            if start < o.release_time - EPS:
                bad(
                    "KR-11",
                    row,
                    f"{oid} service started {start:.0f}s before release " f"{o.release_time:.0f}s",
                )
            if (o.tw_start, o.tw_end) != (NO_TW_START, NO_TW_END):
                rep.checked["KR-7"] += 1
                rep.checked["KR-8"] += 1
                if start < o.tw_start - EPS:
                    bad(
                        "KR-7",
                        row,
                        f"{oid} service started {start:.0f}s before tw_start " f"{o.tw_start:.0f}s",
                    )
                if start > o.tw_end + EPS:
                    bad(
                        "KR-8",
                        row,
                        f"{oid} service started {start:.0f}s after tw_end " f"{o.tw_end:.0f}s",
                    )

    for oid in sorted(failed & set(delivered_by)):
        bad("KR-12", {"t": 0.0, "agent_id": delivered_by[oid]}, f"{oid} both delivered and failed")
    return rep


# ---------------------------------------------------------------- finding the config and the map
def find_config(rows: list[dict], config_dir: Path) -> Optional[Path]:
    """<scenario>_s<seed>.yaml, else any YAML whose run_id equals the log's run_id."""
    if not rows:
        return None
    first = rows[0]
    cand = config_dir / f"{first.get('scenario')}_s{first.get('seed')}.yaml"
    if cand.exists():
        return cand
    import yaml

    for p in sorted(config_dir.glob("*.yaml")):
        try:
            if (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("run_id") == first.get(
                "run_id"
            ):
                return p
        except yaml.YAMLError:
            continue
    return None


def load_graph_for(cfg: dict):
    """R2's graph named by the config's map_path (data/processed/<name>.graphml), or None."""
    mp = cfg.get("map_path")
    if not mp:
        return None
    from src.world.graph_loader import load_road_graph

    return load_road_graph(Path(mp).stem)


def markdown(reports: list[AuditReport]) -> str:
    lines = ["| Run | " + " | ".join(CHECKS) + " | Total |", "|---" * (len(CHECKS) + 2) + "|"]
    for r in reports:
        c = r.counts()
        lines.append(
            f"| {r.run_id} | " + " | ".join(str(c[k]) for k in CHECKS) + f" | {len(r.violations)} |"
        )
    total = Counter()
    for r in reports:
        total.update(r.counts())
    lines.append(
        "| **all runs** | "
        + " | ".join(str(total.get(k, 0)) for k in CHECKS)
        + f" | {sum(total.values())} |"
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description="Constraint-violation audit for simulation logs (R3).")
    ap.add_argument("logs", nargs="+", help="JSONL logs written by R1's simulator")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--config", help="scenario YAML for every log")
    g.add_argument("--config-dir", help="find each log's YAML by <scenario>_s<seed> or run_id")
    ap.add_argument("--no-map", action="store_true", help="skip KR-3 / KR-6 (no map loading)")
    ap.add_argument("--json", help="write the full report (all violations) here")
    ap.add_argument("--md", help="write the per-run table here")
    a = ap.parse_args(argv)

    reports, graphs, status = [], {}, 0
    for log in a.logs:
        rows = read_log(log)
        cfg_path = Path(a.config) if a.config else find_config(rows, Path(a.config_dir))
        if cfg_path is None or not cfg_path.exists():
            print(f"{log}: no scenario config found, not audited", file=sys.stderr)
            status = 2
            continue
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        G = None
        if not a.no_map and cfg.get("map_path"):
            if cfg["map_path"] not in graphs:
                graphs[cfg["map_path"]] = load_graph_for(cfg)
            G = graphs[cfg["map_path"]]
        rep = audit_log(rows, cfg, G)
        reports.append(rep)
        verdict = "OK" if rep.ok else f"{len(rep.violations)} VIOLATIONS"
        print(f"{log}: {verdict}  (checked: {dict(rep.checked)})")
        for v in rep.violations[:10]:
            print(f"    {v.rule} t={v.t:.0f} {v.agent_id}: {v.detail}")
        if not rep.ok:
            status = max(status, 1)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(
            json.dumps([r.as_dict() for r in reports], indent=1), encoding="utf-8"
        )
    if a.md:
        Path(a.md).parent.mkdir(parents=True, exist_ok=True)
        Path(a.md).write_text(
            "# Constraint-violation audit\n\n" + markdown(reports) + "\n", encoding="utf-8"
        )
    print(markdown(reports))
    return status


if __name__ == "__main__":
    sys.exit(main())
