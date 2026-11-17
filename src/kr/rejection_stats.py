"""
R3 · Week 6: rule-rejection statistics — how often the rules block an insertion or force a reroute.

    python -m src.kr.rejection_stats                  # 9 scenarios of seed 0, writes the report
    python -m src.kr.rejection_stats --seeds 0 1 2    # more seeds
    python -m src.kr.rejection_stats --no-sim         # skip part B2 (no simulation)

Part A, insertions (planning time). A stand-alone greedy allocation, not the one the experiments
run: R4's contract-net uses its own capacity checks, and R3's rules are not called in the
experiment runs. For each committed scenario (configs/scenarios/), orders are
taken in (release_time, priority, tw_end, id) order and offered to every vehicle. A vehicle can
take an order if R3's rules accept the vehicle's plan plus the order (csp fast path: KR-1
capacity, KR-11 release, KR-8 windows, KR-10 shift, reachability, exact stop-order search up to
9 stops). Each rejected (order, vehicle) pair is attributed to the rule that blocked it (the
rule engine's violated rules); the order goes to the eligible vehicle whose plan gets the least
extra time. Orders no vehicle can take are KR-15 rejections. Travel times: free-flow shortest
paths on the real map under each vehicle's road rules (R2's TravelTimeMatrix). Planning-time
view: every vehicle plans from its depot at 06:00 and an order released later is served after
its release (KR-7 wait), so KR-11 never blocks here.

Part B, reroutes. B1 (KR-3, static): trips are (order node, hypothetical depot) pairs; depots are
placed on the large-vehicle road network. For each trip: can a large vehicle (no narrow roads)
make it at all, and how much longer is it than a small vehicle's. B2 (KR-4, dynamic): R1's
simulator with R7's planner on the same scenarios, 10 disruptions per hour, run twice: with the
stand-in driver's hand-written routes (scripts/run_audit_demo.py, allocation "route") and with
the simulator's default allocation (R4's contract-net, the one the experiments use). Counted:
closures, replans that changed a route, route checks that kept it, and vehicles that waited at a
closed road.

Deterministic: two runs with the same scenarios, seeds and rate gave byte-identical reports.
tests/test_rejection_stats.py checks part A's counting and attribution, B1 on a small hand-made
graph, and B2's counting (closure_counts) on hand-made log rows.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from .fast_path import NewOrderFilter
from .ontology import SIM_END_S, SIM_START_S, position_of, tw_of
from .rules import run_feasibility_engine

ROOT = Path(__file__).resolve().parents[2]
SIZES = ("small", "medium", "large")
LAYOUTS = ("uniform", "clustered", "hotspot")
RULE_ORDER = ("KR-1", "KR-11", "KR-8", "KR-10a", "KR-10", "REACH", "heuristic")
RULE_NAMES = {
    "KR-1": "capacity",
    "KR-11": "release time",
    "KR-8": "time window end",
    "KR-10a": "shift duration",
    "KR-10": "back by shift end",
    "REACH": "no legal path",
    "heuristic": "above-9-stop heuristic (route may exist)",
}


# ---------------------------------------------------------------- part A: insertions
def plan_end(vehicle, seq, tt, t0=SIM_START_S) -> float:
    """Time the vehicle is back at its depot after serving seq in that order (inf if infeasible)."""
    t, node = t0, position_of(vehicle)
    for o in seq:
        arrival = t + tt(node, o.node, t)
        tw = tw_of(o)
        start = max(arrival, tw[0] if tw else -math.inf, o.release_time)
        if math.isinf(arrival) or (tw and start > tw[1]):
            return math.inf
        t, node = start + o.service_time, o.node
    return t + tt(node, vehicle.depot_node, t)


def blocking_rule(vehicle, plan, order, tt) -> str:
    """The rule that rejects plan + order for this vehicle, as the rule engine reports it."""
    r = run_feasibility_engine(vehicle, list(plan) + [order], tt, SIM_START_S, check_release=False)
    if r["feasible"]:
        return "heuristic"  # a route exists; the >9-stop heuristic missed it
    for rule in RULE_ORDER:
        if rule in r["violated"]:
            return rule
    return r["violated"][0]


def insertion_stats(vehicles, orders, tt_for) -> dict:
    """Greedy allocation with rule checks. tt_for(vehicle) -> travel_time(u, v, t)."""
    by_profile: dict = {}
    for v in vehicles:
        key = tuple(sorted(v.allowed_road_classes or []))
        by_profile.setdefault(key, []).append(v)
    filters = {
        key: NewOrderFilter(vs, tt_for(vs[0]), SIM_START_S, check_release=False)
        for key, vs in by_profile.items()
    }
    tts = {key: tt_for(vs[0]) for key, vs in by_profile.items()}
    vmap = {v.id: v for v in vehicles}

    pairs = Counter()
    order_outcome = Counter()
    unassigned_reason = Counter()
    ordered = sorted(orders, key=lambda o: (o.release_time, o.priority, o.tw_end, o.id))
    for o in ordered:
        candidates = []
        reasons = Counter()
        for key, f in filters.items():
            res = f.check(o)
            for vid, how in res.decided_by.items():
                pairs["checked"] += 1
                if vid in res.eligible:
                    seq = res.sequence[vid]
                    extra = plan_end(vmap[vid], seq, tts[key]) - plan_end(
                        vmap[vid], f.plan[vid], tts[key]
                    )
                    candidates.append((extra, vid, key, res))
                    pairs["accepted"] += 1
                    continue
                rule = (
                    how
                    if how in ("KR-1", "KR-11")
                    else blocking_rule(vmap[vid], f.plan[vid], o, tts[key])
                )
                pairs[rule] += 1
                reasons[rule] += 1
        if candidates:
            _, vid, key, res = min(candidates, key=lambda c: (c[0], c[1]))
            filters[key].assign(vid, o, res)
            order_outcome["assigned"] += 1
        else:
            order_outcome["rejected (KR-15)"] += 1
            unassigned_reason[reasons.most_common(1)[0][0] if reasons else "none"] += 1
    return {
        "pairs": dict(pairs),
        "orders": dict(order_outcome),
        "kr15_main_rule": dict(unassigned_reason),
    }


# ---------------------------------------------------------------- part B1: KR-3 for large vehicles
def road_class_effect(G, nodes, n_depots=3, seed=0) -> dict:
    """Large vs small vehicles on depot -> order trips.

    Depots are placed where a large vehicle can start (nodes nearest the k-means centres within the
    large-vehicle road network, as make_scenarios.py --large-share does); small vehicles start from
    the same depots, so the difference is the road rule alone.
    """
    import networkx as nx
    import pandas as pd

    from src.data.make_scenarios import large_network, pick_depots

    from .planner_rules import LARGE_VEHICLE_CLASSES, RoadRules
    from src.sim.models import Vehicle

    net = large_network(G)
    table = pd.DataFrame(
        [(n, d["x"], d["y"]) for n, d in G.nodes(data=True)], columns=["node", "x", "y"]
    )
    depots = pick_depots(table, n_depots, seed, allowed=net)
    probe = Vehicle("probe", depots[0], 1.0, depots[0], allowed_road_classes=LARGE_VEHICLE_CLASSES)
    bad = RoadRules(G).class_blocked(probe)
    H, S = nx.DiGraph(), nx.DiGraph()
    for u, v, d in G.edges(data=True):
        w = float(d.get("free_flow_time", 1.0))
        if not S.has_edge(u, v) or S[u][v]["w"] > w:
            S.add_edge(u, v, w=w)
        if (u, v) not in bad and (not H.has_edge(u, v) or H[u][v]["w"] > w):
            H.add_edge(u, v, w=w)
    trips = unreachable = 0
    detours = []
    for dep in depots:
        small = nx.single_source_dijkstra_path_length(S, dep, weight="w")
        large = nx.single_source_dijkstra_path_length(H, dep, weight="w")
        for n in nodes:
            if n not in small or n == dep:
                continue
            trips += 1
            if n not in large:
                unreachable += 1
            elif small[n] > 0:
                detours.append(large[n] / small[n] - 1.0)
    detours.sort()
    by_class = Counter(reason.split("'")[1] for reason in bad.values() if "'" in reason)
    return {
        "depots": depots,
        "depots_n": len(depots),
        "order_nodes": len(set(nodes)),
        "large_network_nodes": len(net),
        "graph_nodes": G.number_of_nodes(),
        "depot_order_trips": trips,
        "unreachable_for_large": unreachable,
        "unreachable_share": round(unreachable / trips, 4) if trips else None,
        "reachable_trips": len(detours),
        "reachable_trips_longer": sum(d > 1e-9 for d in detours),
        "median_detour_pct": round(100 * detours[len(detours) // 2], 1) if detours else None,
        "mean_detour_pct": round(100 * sum(detours) / len(detours), 1) if detours else None,
        "mean_detour_when_longer_pct": round(
            100 * sum(d for d in detours if d > 1e-9) / max(1, sum(d > 1e-9 for d in detours)), 1
        ),
        "edges_blocked_for_large": len(bad),
        "edges_blocked_by_class": dict(sorted(by_class.items())),
        "edges_total": len(set(G.edges())),
    }


# ---------------------------------------------------------------- part B2: KR-4 closures in the sim
STAND_IN = "route"  # the stand-in driver's hand-written routes
EXPERIMENT = None  # the simulator's default allocation: R4's contract-net since R1's #38


def closure_counts(rows: list) -> dict:
    """Closure effects in one simulator log (its rows, as dicts)."""
    ev = Counter(x["event"] for x in rows)
    disrupt = Counter(x["payload"].get("type") for x in rows if x["event"] == "disruption")
    replan = Counter(x["payload"].get("trigger") for x in rows if x["event"] == "replan")
    closed = {
        tuple(x["payload"]["edge"][:2])
        for x in rows
        if x["event"] == "disruption" and x["payload"].get("type") == "closure"
    }
    hit = {
        tuple(x["payload"]["edge"][:2])
        for x in rows
        if x["event"] == "replan"
        and x["payload"].get("trigger") == "closure"
        and x["payload"].get("edge")
    }
    kept = Counter(x["payload"].get("trigger") for x in rows if x["event"] == "replan_kept")
    return {
        "closures": disrupt.get("closure", 0),
        # identities, so the report can count distinct closures: within one seed every
        # scenario sees the same disruption timeline
        "closure_ids": sorted(f"{e[0]}-{e[1]}" for e in closed),
        "changed_closure_ids": sorted(f"{e[0]}-{e[1]}" for e in hit & closed),
        "traffic_spikes": disrupt.get("traffic_spike", 0),
        "closures_that_changed_a_route": len(hit & closed),
        "replans_changed_by_closure": replan.get("closure", 0),
        "replans_kept_after_closure": kept.get("closure", 0),
        "replans_changed_by_spike": replan.get("traffic_spike", 0),
        "blocked_waits": ev.get("blocked_wait", 0),
        "capacity_blocked_at_delivery": ev.get("capacity_blocked", 0),
    }


def closure_effect(
    name: str, rate: float, log_dir: Path, graphs: dict, allocation=STAND_IN
) -> dict:
    """Run one scenario in the simulator and count closure effects in its log.

    allocation=STAND_IN drives scripts/run_audit_demo.py's hand-written routes; EXPERIMENT (None)
    leaves the simulator's default allocation, the one the experiments use."""
    import importlib.util

    # Loaded by file path (scripts/ is not a package). Always pass allocation: since R1's #38 the
    # simulator's default (contract-net) ignores hand-written routes, so without allocation="route"
    # the stand-in result silently becomes a contract-net result (tests/test_rejection_stats.py).
    spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "run_audit_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    r = demo.run_one(name, rate, log_dir, graphs, allocation=allocation)
    rows = [json.loads(line) for line in open(r["log"], encoding="utf-8") if line.strip()]
    return {"scenario": name, **closure_counts(rows), "audit_violations": r["total_violations"]}


# ---------------------------------------------------------------- driver and report
def scenario_names(seeds) -> list[str]:
    return [f"{s}_{lay}_s{seed}" for seed in seeds for s in SIZES for lay in LAYOUTS]


def run(
    seeds=(0,), sim=True, rate=10.0, log_dir="logs/rejection_stats", shift_end=SIM_END_S
) -> dict:
    import yaml

    from .adapters import travel_time_from_matrix
    from .audit import load_graph_for
    from .planner_rules import RoadRules
    from src.search.travel_matrix import TravelTimeMatrix
    from src.sim.run import build_order, build_vehicle

    graphs: dict = {}
    part_a, part_a_shift, b1_nodes, b2, b2_exp = [], [], set(), [], []
    G = None
    for name in scenario_names(seeds):
        cfg = yaml.safe_load(
            (ROOT / "configs" / "scenarios" / f"{name}.yaml").read_text(encoding="utf-8")
        )
        if cfg["map_path"] not in graphs:
            graphs[cfg["map_path"]] = load_graph_for(cfg)
        G = graphs[cfg["map_path"]]
        vehicles = [build_vehicle(v) for v in cfg["vehicles"]]
        orders = [build_order(o) for o in cfg["orders"]]
        nodes = {o.node for o in orders} | {v.depot_node for v in vehicles}
        rules = RoadRules(G)

        def tt_for(v, rules=rules, nodes=nodes):
            return travel_time_from_matrix(
                TravelTimeMatrix(G, rules.cost_fn_factory(v)), nodes, bucket_s=1e9
            )

        stats = insertion_stats(vehicles, orders, tt_for)
        stats.update(scenario=name, vehicles=len(vehicles), orders_n=len(orders))
        part_a.append(stats)
        if shift_end is not None:  # sensitivity: every vehicle due back at the depot by shift_end
            for v in vehicles:
                v.shift_end = shift_end
            stats = insertion_stats(vehicles, orders, tt_for)
            stats.update(scenario=name, vehicles=len(vehicles), orders_n=len(orders))
            part_a_shift.append(stats)
        b1_nodes |= {o.node for o in orders}
        if sim:
            b2.append(closure_effect(name, rate, Path(log_dir), graphs, STAND_IN))
            b2_exp.append(closure_effect(name, rate, Path(log_dir), graphs, EXPERIMENT))
    b1 = road_class_effect(G, sorted(b1_nodes)) if G is not None else {}
    return {
        "seeds": list(seeds),
        "rate": rate,
        "shift_end": shift_end,
        "part_a": part_a,
        "part_a_shift_end": part_a_shift,
        "part_b1": b1,
        "part_b2": b2,
        "part_b2_contract_net": b2_exp,
    }


def _pct(n, d):
    return f"{100 * n / d:.1f}%" if d else "n/a"


def _distinct_closures(B2) -> tuple:
    """(distinct closures, distinct closures that changed a route in at least one scenario).

    A closure is identified by its seed and its road; scenarios of the same seed share closures.
    """
    seen, hit = set(), set()
    for r in B2:
        seed = r["scenario"].rsplit("_s", 1)[-1]
        seen |= {(seed, c) for c in r.get("closure_ids", [])}
        hit |= {(seed, c) for c in r.get("changed_closure_ids", [])}
    return len(seen), len(hit & seen)


def _distinct_closure_lines(B2) -> list:
    n, k = _distinct_closures(B2)
    pairs = sum(r["closures"] for r in B2)
    changed_pairs = sum(r.get("closures_that_changed_a_route", 0) for r in B2)
    return [
        f"Distinct closures: **{n}**; **{k} of {n}** changed some vehicle's route in at least "
        f"one scenario. Closure-scenario pairs: {pairs}, of which {changed_pairs} changed a route."
    ]


def _kr15_rules(A) -> dict:
    total = Counter()
    for s in A:
        total.update(s["kr15_main_rule"])
    return dict(total)


def _rule_table(A) -> list[str]:
    tot = Counter()
    for s in A:
        tot.update(s["pairs"])
    rejected = sum(v for k, v in tot.items() if k not in ("checked", "accepted"))
    kr15 = sum(s["orders"].get("rejected (KR-15)", 0) for s in A)
    orders = sum(s["orders"].get("assigned", 0) for s in A) + kr15
    out = [
        "| Rule | Meaning | Rejected (order, vehicle) pairs | Share of all checks "
        "| Share of rejections |",
        "|---|---|---|---|---|",
    ]
    for rule in RULE_ORDER:
        n = tot.get(rule, 0)
        out.append(
            f"| {rule} | {RULE_NAMES[rule]} | {n:,} | {_pct(n, tot['checked'])} | "
            f"{_pct(n, rejected)} |"
        )
    out += [
        f"| **all** | | **{rejected:,}** of {tot['checked']:,} checks | "
        f"{_pct(rejected, tot['checked'])} | |",
        "",
        f"Orders no vehicle could take (KR-15): {kr15} of {orders}"
        + (f"; main blocking rule: {_kr15_rules(A)}" if kr15 else "")
        + ".",
        "",
    ]
    return out


def _scenario_table(A) -> list[str]:
    out = [
        "| Scenario | Vehicles | Orders | Assigned | KR-15 rejected | Checks | KR-1 | KR-8 "
        "| KR-10 | REACH | heuristic |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in A:
        p, c = s["pairs"], s["pairs"].get("checked", 0)
        out.append(
            f"| {s['scenario']} | {s['vehicles']} | {s['orders_n']} "
            f"| {s['orders'].get('assigned', 0)} | {s['orders'].get('rejected (KR-15)', 0)} | "
            f"{c:,} | "
            + " | ".join(
                _pct(p.get(r, 0), c) for r in ("KR-1", "KR-8", "KR-10", "REACH", "heuristic")
            )
            + " |"
        )
    return out


def report(res: dict) -> str:
    out = [
        "# Rule-rejection statistics (R3, Week 6)",
        "",
        f"Generated by `python -m src.kr.rejection_stats --seeds "
        f"{' '.join(map(str, res['seeds']))}` from the committed scenario files "
        "(real Amazon statistics on the Secunderabad map).",
        "",
        "## A. How often a rule blocks an insertion",
        "",
        "A stand-alone greedy allocation at planning time, not the allocation the experiments "
        "run: R4's contract-net uses its own capacity checks, and R3's rules are not called in "
        "the experiment runs. Every order is offered to every vehicle in release order and goes "
        "to the eligible vehicle whose plan gets the least extra time; a rejected pair is "
        "attributed to the rule the rule engine reports. That capacity (KR-1) is the only rule "
        "that blocks follows from how the scenarios were built: fleet capacity is sized so total "
        "demand is 80% of it, and every order is loaded at 06:00 with no unloading. It is not a "
        "measurement of R4's runs.",
        "",
        "### A1. Scenarios as committed (no shift end)",
        "",
    ]
    out += _rule_table(res["part_a"])
    out += _scenario_table(res["part_a"]) + [""]
    if res.get("part_a_shift_end"):
        hh = int(res["shift_end"] // 3600)
        out += [
            f"### A2. Sensitivity: every vehicle due back at its depot by {hh:02d}:00",
            "",
            "The committed scenarios set no shift end (KR-10 cannot bind there). This run adds "
            f"one at {hh:02d}:00, the end of the simulated period, to show what KR-10 would do. "
            "Only this one shift end was tested; an earlier one is a tighter limit and would be "
            "expected to block at least as many checks.",
            "",
        ]
        out += _rule_table(res["part_a_shift_end"])
        out += _scenario_table(res["part_a_shift_end"]) + [""]
    b1 = res["part_b1"]
    by_class = ", ".join(f"{k} {v:,}" for k, v in b1.get("edges_blocked_by_class", {}).items())
    out += [
        "## B. How often a rule blocks a road (reroutes)",
        "",
        "### B1. Road class (KR-3) for large vehicles",
        "",
        "Large vehicles may not use residential, living_street, service or unclassified roads. "
        "The committed scenarios are small-fleet only (D2), so this measures what the rule would "
        f"do. A trip is an (order node, depot) pair: {b1.get('order_nodes', 0):,} distinct order "
        f"nodes of these scenarios x {b1.get('depots_n', 0)} hypothetical depots, placed on the "
        f"large-vehicle road network ({b1.get('large_network_nodes', 0):,} of "
        f"{b1.get('graph_nodes', 0):,} nodes). Each trip is driven by a large and a small vehicle "
        "from the same depot. Most trips are impossible because of the rule and the map together: "
        f"the roads closed to large vehicles are {by_class}.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Roads (u -> v) closed to large vehicles | {b1.get('edges_blocked_for_large', 0):,} "
        f"of {b1.get('edges_total', 0):,} "
        f"({_pct(b1.get('edges_blocked_for_large', 0), b1.get('edges_total', 0))}) |",
        f"| (Order node, hypothetical depot) trips a large vehicle cannot make at all | "
        f"{b1.get('unreachable_for_large', 0):,} of {b1.get('depot_order_trips', 0):,} "
        f"({_pct(b1.get('unreachable_for_large', 0), b1.get('depot_order_trips', 0))}) |",
        f"| Reachable trips that need a detour | {b1.get('reachable_trips_longer', 0):,} of "
        f"{b1.get('reachable_trips', 0):,} |",
        f"| Detour on reachable trips, median / mean | {b1.get('median_detour_pct')}% / "
        f"{b1.get('mean_detour_pct')}% longer |",
        f"| Detour on the trips that need one, mean | "
        f"{b1.get('mean_detour_when_longer_pct')}% longer |",
        "",
    ]
    if res["part_b2"]:
        out += [
            "### B2. Closures (KR-4) in the simulator",
            "",
            f"R1's simulator with R7's planner and {res['rate']:g} disruptions per hour, each "
            "scenario run twice. The disruption timeline depends only on the seed, so the "
            "scenarios of one seed share the same closures: a row counts closure-scenario pairs, "
            "not distinct closures.",
            "",
        ]
        out += _b2_section(
            "B2a. Stand-in hand-written routes",
            "Routes from the stand-in driver of scripts/run_audit_demo.py (allocation \"route\"): "
            "every vehicle gets a fixed share of the orders up front and drives one route through "
            "all of them. This is not the system the experiments run.",
            res["part_b2"],
        )
        if res.get("part_b2_contract_net"):
            out += _b2_section(
                "B2b. Contract-net, the allocation the experiments use",
                "The simulator's default allocation (R4's contract-net since R1's #38): orders "
                "are auctioned to vehicles as they are released, instead of split up front.",
                res["part_b2_contract_net"],
            )
    return "\n".join(out).rstrip("\n") + "\n"


def _b2_section(title: str, text: str, rows: list) -> list:
    out = [
        f"#### {title}",
        "",
        text,
        "",
        *_distinct_closure_lines(rows),
        "",
        "| Scenario | Closures | Closures that changed a route | Routes changed | Route "
        "checks that kept the route | Vehicles waiting at a closed road | Audit violations |",
        "|---|---|---|---|---|---|---|",
    ]
    tb = Counter()
    for r in rows:
        tb.update({k: v for k, v in r.items() if isinstance(v, int)})
        out.append(
            f"| {r['scenario']} | {r['closures']} | {r['closures_that_changed_a_route']} | "
            f"{r['replans_changed_by_closure']} | "
            f"{r['replans_kept_after_closure']} | {r['blocked_waits']} | "
            f"{r['audit_violations']} |"
        )
    out.append(
        f"| **all** | **{tb['closures']}** | **{tb['closures_that_changed_a_route']}** | "
        f"**{tb['replans_changed_by_closure']}** | "
        f"**{tb['replans_kept_after_closure']}** | **{tb['blocked_waits']}** | "
        f"**{tb['audit_violations']}** |"
    )
    return out + [""]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--no-sim", action="store_true", help="skip part B2 (simulation)")
    ap.add_argument("--rate", type=float, default=10.0)
    ap.add_argument("--log-dir", default="logs/rejection_stats")
    ap.add_argument("--md", default="reports/rule_rejections.md")
    ap.add_argument("--json", default="reports/rule_rejections.json")
    a = ap.parse_args(argv)
    res = run(a.seeds, not a.no_sim, a.rate, a.log_dir)
    for path, text in ((a.md, report(res)), (a.json, json.dumps(res, indent=1) + "\n")):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(text.encode("utf-8"))
    print(report(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
