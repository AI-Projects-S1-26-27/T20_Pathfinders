"""R3 Week 5: rule coverage. Every rule has one case where it lets a plan through (positive) and
one where it blocks it (negative). The last two tests make the table complete: every rule in the
engine and every KR id in docs/kr_ontology_rules.md must be here, unless the doc names another
owner (TODO(Rn)) for it.

Line world: nodes 0..10, 60 s per unit of distance, depot 0. T = 06:00 (seconds since midnight).
"""

import re
from pathlib import Path

import networkx as nx
import pytest

from src.kr.audit import audit_log
from src.kr.csp import eligible_vehicles, solve_batch
from src.kr.ontology import ALL_ROAD_CLASSES, NARROW_ROAD_CLASSES, SIM_START_S as T, Order
from src.kr.ontology import Vehicle, Zone
from src.kr.rules import RULES, feasible, road_allowed, violated_rules
from src.search.astar import astar
from src.search.dijkstra import free_flow_cost

ROOT = Path(__file__).resolve().parents[1]


def tt(a, b, t):
    return 60.0 * abs(a - b)


def veh(**kw):
    base = dict(id="V", depot_node=0, capacity=10, shift_end=T + 4 * 3600)
    base.update(kw)
    return Vehicle(**base)


def order(i, node, tw=None, **kw):
    base = dict(
        id=i,
        node=node,
        demand=1,
        priority=2,
        service_time=60,
        release_time=T,
        tw_start=T + tw[0] if tw else 0.0,
        tw_end=T + tw[1] if tw else 86400.0,
    )
    base.update(kw)
    return Order(**base)


def ok(vehicle, orders, **kw):
    return feasible(vehicle, orders, tt, **kw)[0]


def blocked_by(vehicle, orders, **kw):
    return violated_rules(feasible(vehicle, orders, tt, **kw)[1])


LARGE = veh(vehicle_class="large", allowed_road_classes=ALL_ROAD_CLASSES - NARROW_ROAD_CLASSES)
ZONE = Zone("z", frozenset({2}), start=T, end=T + 3600)


def one_way():
    G = nx.MultiDiGraph()
    G.add_edge(0, 1, key=0, free_flow_time=10.0)  # only 0 -> 1 exists
    return G


def audit_rows(*deliveries):
    return [
        {
            "t": t,
            "agent_id": "V",
            "event": "order_delivered",
            "payload": {"order_id": oid, "node": 2, "demand": 1},
        }
        for oid, t in deliveries
    ]


AUDIT_CFG = {
    "vehicles": [{"id": "V", "depot_node": 2, "capacity": 10, "current_node": 2}],
    "orders": [{"id": "a", "node": 2, "demand": 1, "service_time": 0.0}],
}


def trace_has(vehicle, orders, prefix):
    return any(line.startswith(prefix) for line in feasible(vehicle, orders, tt)[1])


# rule id -> (positive: rule lets it through, negative: rule blocks it). Both must return True.
CASES = {
    "KR-1": (
        lambda: ok(veh(), [order("a", 2, demand=10)]),
        lambda: blocked_by(veh(load=5), [order("a", 2, demand=6)]) == ["KR-1"],
    ),
    "KR-2": (
        lambda: eligible_vehicles(order("a", 2), [veh(load=9)], tt)[0] == ["V"],
        lambda: eligible_vehicles(order("a", 2), [veh(load=10)], tt)[0] == [],
    ),
    "KR-3": (
        lambda: road_allowed(LARGE, 1, 2, {"highway": "primary"}, T)[0],
        lambda: road_allowed(LARGE, 1, 2, {"highway": "residential"}, T)[1].startswith("KR-3"),
    ),
    "KR-4": (
        lambda: road_allowed(veh(), 1, 2, {"highway": "primary"}, T)[0],
        lambda: road_allowed(
            veh(), 1, 2, {"highway": "primary"}, T, is_blocked=lambda u, v, t: True
        )[1].startswith("KR-4"),
    ),
    "KR-5": (
        lambda: astar(one_way(), 0, 1, free_flow_cost, lambda n, g: 0.0).path == [0, 1],
        lambda: not astar(one_way(), 1, 0, free_flow_cost, lambda n, g: 0.0).path,
    ),
    "KR-6": (
        lambda: road_allowed(LARGE, 1, 2, {"highway": "primary"}, T + 4000, [ZONE])[0],
        lambda: road_allowed(LARGE, 1, 2, {"highway": "primary"}, T + 10, [ZONE])[1].startswith(
            "KR-6"
        ),
    ),
    "KR-7": (
        lambda: ok(veh(), [order("a", 1, tw=(0, 900))]),
        lambda: any(
            "a waits 540s" in line
            for line in feasible(veh(), [order("a", 1, tw=(600, 900))], tt)[1]
        ),
    ),
    "KR-8": (
        lambda: ok(veh(), [order("a", 5, tw=(0, 400))]),
        lambda: "KR-8" in blocked_by(veh(), [order("a", 5, tw=(0, 200))]),
    ),
    # a must go first (its window closes at 100 s); its service time decides whether b still fits
    "KR-9": (
        lambda: ok(
            veh(), [order("a", 1, tw=(0, 100), service_time=60), order("b", 2, tw=(0, 300))]
        ),
        lambda: "KR-8"
        in blocked_by(
            veh(), [order("a", 1, tw=(0, 100), service_time=2000), order("b", 2, tw=(0, 300))]
        ),
    ),
    "KR-10": (
        lambda: ok(veh(shift_end=T + 1000), [order("a", 5)]),
        lambda: blocked_by(veh(shift_end=T + 600), [order("a", 5)]) == ["KR-10"],
    ),
    "KR-10a": (
        lambda: ok(veh(shift_end=T + 5000), [order("a", 1, service_time=2000)]),
        lambda: blocked_by(veh(shift_end=T + 1000), [order("a", 1, service_time=2000)])
        == ["KR-10a"],
    ),
    "KR-11": (
        lambda: ok(veh(), [order("a", 2, release_time=T + 100)], t_now=T + 100),
        lambda: blocked_by(veh(), [order("a", 2, release_time=T + 500)], t_now=T + 100)
        == ["KR-11"],
    ),
    "KR-12": (
        lambda: audit_log(audit_rows(("a", T)), AUDIT_CFG).counts()["KR-12"] == 0,
        lambda: audit_log(audit_rows(("a", T), ("a", T + 5)), AUDIT_CFG).counts()["KR-12"] == 1,
    ),
    "KR-13": (
        lambda: feasible(veh(), [order("s", 1, priority=3), order("e", 8, priority=1)], tt)[1][-1]
        == "FEASIBLE: e -> s",
        lambda: feasible(
            veh(), [order("e", 9, priority=1), order("s", 1, priority=3, tw=(0, 200))], tt
        )[1][-1]
        == "FEASIBLE: s -> e",
    ),
    "KR-13x": (
        lambda: not trace_has(veh(), [order("e", 8, priority=1), order("s", 1)], "KR-13x"),
        lambda: trace_has(
            veh(), [order("e", 9, priority=1), order("s", 1, priority=3, tw=(0, 200))], "KR-13x"
        ),
    ),
    "KR-13y": (
        lambda: ok(
            veh(),
            [order("a", 10, tw=(0, 2000), priority=3), order("b", 1, tw=(1500, 1600), priority=1)],
        ),
        lambda: any(
            "no order meets every window" in line
            for line in feasible(
                veh(),
                [
                    order("a", 10, tw=(0, 500), priority=3),
                    order("b", 1, tw=(1500, 1600), priority=1),
                ],
                tt,
            )[1]
        ),
    ),
    "KR-15": (
        lambda: solve_batch([veh()], [order("a", 2)], tt).rejected == {},
        lambda: "big" in solve_batch([veh()], [order("big", 2, demand=99)], tt).rejected,
    ),
}


@pytest.mark.parametrize("rule", sorted(CASES))
def test_rule_lets_valid_plan_through(rule):
    assert CASES[rule][0](), f"{rule}: positive case failed"


@pytest.mark.parametrize("rule", sorted(CASES))
def test_rule_blocks_invalid_plan(rule):
    assert CASES[rule][1](), f"{rule}: negative case failed"


def test_every_engine_rule_is_covered():
    derivations = {"LOAD", "SERVICE", "ETA"}  # derive facts, they cannot be violated
    assert {r.name for r in RULES} - derivations <= set(CASES)


def test_every_documented_rule_is_covered_or_owned_by_someone_else():
    table = (ROOT / "docs" / "kr_ontology_rules.md").read_text(encoding="utf-8")
    missing = []
    for line in table.splitlines():
        m = re.match(r"\|\s*(KR-\d+[a-z]?)\s*\|", line)
        if m and m.group(1) not in CASES and "TODO(R" not in line.split("|")[-2]:
            missing.append(m.group(1))
    assert missing == [], f"rules with no tests and no other owner: {missing}"
