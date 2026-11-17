"""R3 Week 6: rule-rejection statistics count and attribute rejections correctly.
Line world: 60 s per unit of distance, depot 0, T = 06:00."""

import json

from src.kr.ontology import SIM_START_S as T, Order, Vehicle
from src.kr.rejection_stats import insertion_stats, plan_end, report


def tt(a, b, t):
    return 60.0 * abs(a - b)


def tt_for(v):
    return tt


def veh(vid, **kw):
    return Vehicle(**dict(dict(id=vid, depot_node=0, capacity=10), **kw))


def order(i, node, tw=None, **kw):
    base = dict(
        id=i,
        node=node,
        demand=1,
        priority=2,
        service_time=0.0,
        release_time=T,
        tw_start=T + tw[0] if tw else 0.0,
        tw_end=T + tw[1] if tw else 86400.0,
    )
    base.update(kw)
    return Order(**base)


def test_capacity_rejections_are_kr1():
    s = insertion_stats(
        [veh("A", capacity=2), veh("B", capacity=2)],
        [order(f"o{i}", i + 1) for i in range(4)],
        tt_for,
    )
    assert s["pairs"]["checked"] == 8
    assert s["orders"] == {"assigned": 4}
    assert s["pairs"]["KR-1"] == 2  # the last two orders each find one full van


def test_window_rejection_is_attributed_to_kr8():
    far = veh("far", depot_node=9, current_node=9)
    s = insertion_stats([veh("near"), far], [order("o", 1, tw=(0, 100))], tt_for)
    assert s["pairs"].get("KR-8") == 1 and s["orders"] == {"assigned": 1}


def test_order_nobody_can_take_is_kr15_with_its_main_rule():
    s = insertion_stats([veh("A", capacity=1)], [order("big", 2, demand=5)], tt_for)
    assert s["orders"] == {"rejected (KR-15)": 1} and s["kr15_main_rule"] == {"KR-1": 1}


def test_shift_end_rejection_is_kr10():
    s = insertion_stats([veh("A", shift_end=T + 300)], [order("o", 5, service_time=60)], tt_for)
    assert s["pairs"].get("KR-10") == 1


def test_assignment_prefers_the_least_extra_time():
    a, b = veh("A"), veh("B", depot_node=8, current_node=8)
    s = insertion_stats([a, b], [order("o", 7)], tt_for)
    assert s["orders"] == {"assigned": 1}
    assert plan_end(b, [order("o", 7)], tt) < plan_end(a, [order("o", 7)], tt)


def test_same_input_same_statistics():
    vs = [veh("A", capacity=3), veh("B", capacity=3)]
    os_ = [order(f"o{i}", (i * 3) % 10 + 1, tw=(0, 400 + 100 * i)) for i in range(8)]
    one = insertion_stats([veh("A", capacity=3), veh("B", capacity=3)], os_, tt_for)
    two = insertion_stats(vs, os_, tt_for)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)


def test_report_renders_every_section():
    a = insertion_stats([veh("A", capacity=1)], [order("x", 1), order("y", 2)], tt_for)
    a.update(scenario="toy", vehicles=1, orders_n=2)
    res = {
        "seeds": [0],
        "rate": 10.0,
        "shift_end": T + 4 * 3600,
        "part_a": [a],
        "part_a_shift_end": [a],
        "part_b1": {
            "depots": [0],
            "large_network_nodes": 5,
            "graph_nodes": 9,
            "depot_order_trips": 4,
            "unreachable_for_large": 1,
            "reachable_trips": 3,
            "reachable_trips_longer": 1,
            "median_detour_pct": 0.0,
            "mean_detour_pct": 5.0,
            "mean_detour_when_longer_pct": 15.0,
            "edges_blocked_for_large": 2,
            "edges_blocked_by_class": {"residential": 2},
            "edges_total": 8,
            "order_nodes": 4,
            "depots_n": 1,
        },
        "part_b2": [
            {
                "scenario": "toy",
                "closures": 2,
                "traffic_spikes": 1,
                "closures_that_changed_a_route": 1,
                "replans_changed_by_closure": 1,
                "replans_kept_after_closure": 3,
                "replans_changed_by_spike": 0,
                "blocked_waits": 0,
                "capacity_blocked_at_delivery": 0,
                "audit_violations": 0,
            }
        ],
    }
    res["part_b2_contract_net"] = [dict(res["part_b2"][0], closures_that_changed_a_route=0)]
    text = report(res)
    for heading in ("### A1.", "### A2.", "### B1.", "### B2.", "#### B2a. Stand-in hand-written",
                    "#### B2b. Contract-net, the allocation the experiments use"):
        assert heading in text
    assert "| KR-1 | capacity | 1 |" in text and "KR-15): 1 of 2" in text
    assert "not the allocation the experiments run" in text
    assert "4 distinct order nodes of these scenarios x 1 hypothetical depots" in text
    assert "residential 2" in text and "will change once" not in text
    assert text.endswith("|\n") and not text.startswith("\n")


def test_distinct_closures_are_counted_once_per_seed():
    from src.kr.rejection_stats import _distinct_closures

    b2 = [{"scenario": "small_uniform_s0", "closures": 2, "closure_ids": ["1-2", "3-4"],
           "changed_closure_ids": ["1-2"]},
          {"scenario": "large_hotspot_s0", "closures": 2, "closure_ids": ["1-2", "3-4"],
           "changed_closure_ids": ["1-2", "3-4"]},
          {"scenario": "small_uniform_s1", "closures": 1, "closure_ids": ["1-2"],
           "changed_closure_ids": []}]
    assert _distinct_closures(b2) == (3, 2)   # seed 0: two closures, both hit; seed 1: one, none


def test_stand_in_driver_keeps_its_hand_written_routes():
    """Part B2 runs scripts/run_audit_demo.py's stand-in driver. Since R1's #38 the simulator
    defaults to contract-net, which ignores hand-written routes, so the driver must ask for the
    "route" allocation (without it B2 silently measured a different system)."""
    import importlib.util
    from pathlib import Path

    import networkx as nx

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("demo", root / "scripts" / "run_audit_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    G = nx.MultiDiGraph()
    for u, v in ((0, 1), (1, 2), (2, 0)):
        G.add_edge(u, v, key=0, free_flow_time=10.0)
    cfg = {
        "vehicles": [{"id": "V", "depot_node": 0, "current_node": 0, "capacity": 5.0}],
        "orders": [{"id": "a", "node": 2, "demand": 1.0, "service_time": 60.0,
                    "release_time": T}],
    }
    out = demo.drive_routes(cfg, G)
    assert out["allocation"] == "route"
    assert out["vehicles"][0]["route"] == [0, 1, 2, 0]
    assert "allocation" not in cfg  # the committed scenario is not changed


def test_closure_counts_from_log_rows():
    from src.kr.rejection_stats import closure_counts

    def row(event, **payload):
        return {"t": 0.0, "agent_id": "V", "event": event, "payload": payload}

    rows = [
        row("disruption", type="closure", edge=[1, 2, 0]),
        row("disruption", type="closure", edge=[3, 4, 0]),
        row("disruption", type="traffic_spike", edge=[5, 6, 0]),
        row("replan", trigger="closure", edge=[1, 2, 0]),  # closure 1-2 changed a route
        row("replan", trigger="closure", edge=[1, 2, 0]),  # ... of a second vehicle
        row("replan", trigger="closure", edge=[7, 8, 0]),  # a closure from outside this log
        row("replan_kept", trigger="closure", edge=[3, 4, 0]),  # 3-4 was checked, route kept
        row("replan", trigger="traffic_spike", edge=[5, 6, 0]),
        row("blocked_wait", edge=[3, 4, 0]),
    ]
    c = closure_counts(rows)
    assert c["closures"] == 2 and c["closure_ids"] == ["1-2", "3-4"]
    assert c["changed_closure_ids"] == ["1-2"] and c["closures_that_changed_a_route"] == 1
    assert c["replans_changed_by_closure"] == 3 and c["replans_kept_after_closure"] == 1
    assert c["replans_changed_by_spike"] == 1 and c["traffic_spikes"] == 1
    assert c["blocked_waits"] == 1 and closure_counts([])["closures"] == 0


def test_road_class_effect_on_a_small_graph(monkeypatch):
    """Primary square 0-1-2-3 (both ways); residential 0-3 (a shortcut) and 2-4 (a dead end).
    From depot 0 a large vehicle reaches 1 directly, 3 only the long way, and 4 not at all."""
    import networkx as nx

    import src.data.make_scenarios as ms
    from src.kr.rejection_stats import road_class_effect

    G = nx.MultiDiGraph()
    for n, (x, y) in enumerate([(0, 0), (1, 0), (1, 1), (0, 1), (2, 1)]):
        G.add_node(n, x=float(x), y=float(y))
    for u, v, cls, t in [(0, 1, "primary", 10), (1, 2, "primary", 10), (2, 3, "primary", 10),
                         (0, 3, "residential", 5), (2, 4, "residential", 5)]:
        for a, b in ((u, v), (v, u)):
            G.add_edge(a, b, key=0, highway=cls, free_flow_time=float(t))

    def depot_0(table, k, seed, allowed=None):
        assert allowed == {0, 1, 2, 3}  # the large-vehicle network
        return [0]

    monkeypatch.setattr(ms, "pick_depots", depot_0)
    b1 = road_class_effect(G, [1, 3, 4], n_depots=1)
    assert b1["order_nodes"] == 3 and b1["depots_n"] == 1 and b1["depot_order_trips"] == 3
    assert b1["unreachable_for_large"] == 1 and b1["unreachable_share"] == round(1 / 3, 4)
    assert b1["reachable_trips"] == 2 and b1["reachable_trips_longer"] == 1
    # node 3: 5 s for a small vehicle, 30 s for a large one -> 500% longer
    assert b1["median_detour_pct"] == 500.0 and b1["mean_detour_pct"] == 250.0
    assert b1["mean_detour_when_longer_pct"] == 500.0
    assert b1["edges_blocked_for_large"] == 4 and b1["edges_total"] == 10
    assert b1["edges_blocked_by_class"] == {"residential": 4}
    assert b1["large_network_nodes"] == 4 and b1["graph_nodes"] == 5


def test_stand_in_driver_passes_the_allocation_through():
    """allocation=None leaves the simulator's default (contract-net); the default is "route"."""
    import importlib.util
    from pathlib import Path

    import networkx as nx

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("demo", root / "scripts" / "run_audit_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    G = nx.MultiDiGraph()
    G.add_edge(0, 1, key=0, free_flow_time=10.0)
    G.add_edge(1, 0, key=0, free_flow_time=10.0)
    cfg = {
        "vehicles": [{"id": "V", "depot_node": 0, "current_node": 0, "capacity": 5.0}],
        "orders": [{"id": "a", "node": 1, "demand": 1.0, "service_time": 60.0,
                    "release_time": T}],
    }
    assert "allocation" not in demo.drive_routes(cfg, G, allocation=None)
    assert demo.drive_routes(cfg, G)["allocation"] == "route"
