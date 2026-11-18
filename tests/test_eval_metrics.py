"""R8 W2: metric functions checked on a hand-made log whose answers are worked out by hand."""
import math

import numpy as np
import pytest

from src.eval.metrics import (RoadInfo, compute_metrics, edge_pairs, jain_index,
                              occupancy_stats, percentile)

E12 = (1, 2, 0)    # primary road, 500 m (D5 capacity 3)
E23 = (2, 3, 0)    # residential road, 300 m (capacity 1)
ROAD = RoadInfo(
    length_m={E12: 500.0, E23: 300.0, (3, 1, 0): 400.0},
    road_class={(1, 2): "primary", (2, 3): "residential", (3, 1): "residential"},
)

CONFIG = {
    "vehicles": [{"id": "V1"}, {"id": "V2"}, {"id": "V3"}],     # V3 never moves
    "orders": [
        {"id": "A", "release_time": 1000.0, "tw_end": 86400.0, "priority": 1, "service_time": 10.0},
        {"id": "B", "release_time": 1000.0, "tw_end": 1100.0, "priority": 3, "service_time": 20.0},
        {"id": "C", "release_time": 1500.0, "tw_end": 86400.0, "priority": 2, "service_time": 30.0},
        {"id": "D", "release_time": 9000.0, "tw_end": 86400.0, "priority": 2, "service_time": 30.0},
    ],
}


def row(t, agent, event, mode="independent", **payload):
    return {"run_id": "t", "mode": mode, "seed": 1, "scenario": "hand", "rate": 5.0,
            "t": t, "agent_id": agent, "event": event, "payload": payload}


def hand_log(mode="independent"):
    m = {"mode": mode}
    rows = [
        row(1000.0, "env", "sim_start", start_time_s=1000.0, duration_seconds=1000.0,
            n_vehicles=3, n_orders=4, predictor=False, **m),
        row(1000.0, "V1", "edge_enter", edge=list(E12), expected_duration=50.0, **m),
        row(1020.0, "V2", "edge_enter", edge=list(E12), expected_duration=50.0, **m),
        row(1050.0, "V1", "edge_exit", node=2, **m),
        row(1060.0, "V1", "order_delivered", order_id="A", node=2, demand=1.0, **m),
        row(1070.0, "V2", "edge_exit", node=2, **m),
        row(1080.0, "V1", "edge_enter", edge=list(E23), expected_duration=20.0, **m),
        row(1100.0, "V1", "edge_exit", node=3, **m),
        row(1135.0, "V1", "order_delivered", order_id="B", node=3, demand=1.0, **m),
        row(1500.0, "V2", "edge_enter", edge=list(E23), expected_duration=20.0, **m),
        row(1520.0, "V2", "edge_exit", node=3, **m),
        row(1560.0, "V2", "order_delivered", order_id="C", node=3, demand=1.0, **m),
        row(1600.0, "env", "disruption", type="closure", edge=list(E23), magnitude=None,
            duration=100.0, expiry=1700.0, **m),
        row(1650.0, "env", "disruption", type="traffic_spike", edge=list(E12), magnitude=2.0,
            duration=100.0, expiry=1750.0, **m),
    ]
    if mode == "centralized":
        rows += [row(1000.0, "env", "allocation_complete", cpu_seconds=2.0, messages=20, **m)
                 for _ in range(3)]
    else:
        rows += [
            row(1000.0, "env", "allocation_complete", cpu_seconds=0.25, messages=10,
                order_id="A", winner="V1", **m),
            row(1500.0, "env", "allocation_complete", cpu_seconds=0.25, messages=10,
                order_id="C", winner="V2", **m),
            row(1040.0, "V1", "replan", trigger="closure", cpu_seconds=0.2, **m),
            row(1510.0, "V2", "replan", trigger="traffic_spike", cpu_seconds=0.6, **m),
            row(2000.0, "env", "replan_summary", agent_cpu_seconds=1.0, coordination_cpu_seconds=0.5,
                negotiation_messages=6, **m),
        ]
    rows.append(row(2000.0, "env", "sim_complete", delivered=3, failed=0, **m))
    return rows


def test_physical_metrics_match_hand_calculation():
    m = compute_metrics(hand_log(), CONFIG, ROAD)
    assert (m["released"], m["delivered"], m["failed"], m["unaccounted"]) == (3, 3, 0, 0)
    assert m["delivery_time_total_s"] == 255.0            # 60 + 135 + 60
    assert m["delivery_time_mean_s"] == pytest.approx(85.0)
    assert m["weighted_delivery_time_s"] == 435.0         # 3*60 + 1*135 + 2*60
    assert m["makespan_s"] == 560.0
    assert m["on_time_rate"] == pytest.approx(2 / 3)      # B started service 15 s after its window
    assert m["lateness_total_s"] == pytest.approx(15.0)
    assert m["distance_km"] == pytest.approx(1.6)         # 2 x (500 + 300) m
    assert m["wait_s"] == pytest.approx(450.0)            # V1: 30 - 10 service; V2: 430
    assert m["disruptions"] == 2 and m["closures"] == 1


def test_congestion_counts_actual_capacity_violations():
    m = compute_metrics(hand_log(), CONFIG, ROAD)
    assert m["max_on_edge"] == 2
    assert m["violations_cap1"] == 1 and m["overload_s_cap1"] == pytest.approx(30.0)   # 1020 to 1050
    assert m["violations_d5"] == 0 and m["overload_s_d5"] == 0.0                       # primary holds 3


def test_jain_counts_idle_vehicles():
    m = compute_metrics(hand_log(), CONFIG, ROAD)
    # workloads: V1 70 s driving + 30 s service, V2 70 + 30, V3 0  ->  200^2 / (3 * 20000)
    assert m["jain"] == pytest.approx(2 / 3)


def test_cpu_and_messages_decentralized():
    m = compute_metrics(hand_log(), CONFIG, ROAD)
    assert m["planning_cpu_s"] == pytest.approx(2.0)       # 1.0 agent + 0.5 coordination + 2 x 0.25
    assert m["cpu_per_decision_mean_s"] == pytest.approx(0.4)
    assert m["cpu_per_decision_p95_s"] == pytest.approx(0.58)
    assert (m["negotiation_msgs"], m["alloc_msgs"], m["messages_total"]) == (6, 20, 26)


def test_cpu_and_messages_centralized():
    m = compute_metrics(hand_log("centralized"), CONFIG, ROAD)
    assert m["mode"] == "centralized"
    assert m["planning_cpu_s"] == pytest.approx(6.0)
    assert m["cpu_per_decision_mean_s"] == pytest.approx(2.0)
    assert m["n_decisions"] == 3
    assert (m["negotiation_msgs"], m["messages_total"]) == (0, 60)


def test_unfinished_order_shows_up_as_unaccounted():
    rows = [r for r in hand_log() if not (r["event"] == "order_delivered" and r["payload"]["order_id"] == "C")]
    m = compute_metrics(rows, CONFIG, ROAD)
    assert m["delivered"] == 2 and m["unaccounted"] == 1


def test_rerun_gives_identical_numbers():
    assert compute_metrics(hand_log(), CONFIG, ROAD) == compute_metrics(hand_log(), CONFIG, ROAD)


def test_jain_index_bounds():
    assert jain_index([5, 5, 5, 5]) == pytest.approx(1.0)
    assert jain_index([8, 0, 0, 0]) == pytest.approx(0.25)
    assert jain_index([0, 0]) == 1.0
    assert math.isnan(jain_index([]))


def test_percentile_matches_numpy():
    data = [0.1, 0.9, 0.4, 0.2, 0.7, 0.3]
    for q in (0, 25, 50, 95, 100):
        assert percentile(data, q) == pytest.approx(float(np.percentile(data, q)))
    assert math.isnan(percentile([], 50))


def test_exit_before_enter_at_the_same_instant_is_not_a_violation():
    pairs = [("V1", E12, 0.0, 10.0), ("V2", E12, 10.0, 20.0)]
    s = occupancy_stats(pairs, lambda u, v: 1)
    assert (s["max"], s["violations"], s["overload_s"]) == (1, 0, 0.0)


def test_road_still_open_at_the_end_is_closed_at_the_end():
    rows = [row(0.0, "V1", "edge_enter", edge=list(E12), expected_duration=5.0)]
    assert edge_pairs(rows, end_t=100.0) == [("V1", E12, 0.0, 100.0)]


def test_unknown_road_key_falls_back_to_any_road_between_the_nodes():
    assert ROAD.length(1, 2, 7) == 500.0
    with pytest.raises(KeyError):
        ROAD.length(9, 9, 0)


def test_time_after_a_vehicles_last_delivery_is_not_waiting():
    rows = hand_log()
    # V1 drives back to the depot after its last delivery (B at 1135); the 165 s it stands at
    # node 3 afterwards must not count, only the 15 s between arriving at 1100 and starting
    # service at 1115 (delivery logged at the end of service, 1135, service 20 s)
    rows += [row(1300.0, "V1", "edge_enter", edge=[3, 1, 0], expected_duration=40.0),
             row(1340.0, "V1", "edge_exit", node=1)]
    rows.sort(key=lambda r: r["t"])
    assert compute_metrics(rows, CONFIG, ROAD)["wait_s"] == pytest.approx(450.0 + 15.0)
