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
            "edges_total": 8,
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
    text = report(res)
    for heading in ("### A1.", "### A2.", "### B1.", "### B2."):
        assert heading in text
    assert "| KR-1 | capacity | 1 |" in text and "KR-15): 1 of 2" in text


def test_distinct_closures_are_counted_once_per_seed():
    from src.kr.rejection_stats import _distinct_closures

    b2 = [{"scenario": "small_uniform_s0", "closures": 2, "closure_ids": ["1-2", "3-4"],
           "changed_closure_ids": ["1-2"]},
          {"scenario": "large_hotspot_s0", "closures": 2, "closure_ids": ["1-2", "3-4"],
           "changed_closure_ids": ["1-2", "3-4"]},
          {"scenario": "small_uniform_s1", "closures": 1, "closure_ids": ["1-2"],
           "changed_closure_ids": []}]
    assert _distinct_closures(b2) == (3, 2)   # seed 0: two closures, both hit; seed 1: one, none
