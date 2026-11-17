"""Tests for the Week 7 report figures (src/viz/report_figures.py).

The pure helpers run on hand-made logs and a 5-node graph; the tests that need the
Secunderabad map (and the one that runs the simulator) skip if the map file is missing.
"""
from pathlib import Path

import matplotlib
import networkx as nx
import pytest
import yaml

matplotlib.use("Agg")

from src.viz import report_figures as rf  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MAP_FILE = REPO / "data" / "processed" / "secunderabad.graphml"
needs_map = pytest.mark.skipif(not MAP_FILE.exists(), reason="road graph file missing")
START = 21600.0


def small_graph():
    G = nx.MultiDiGraph()
    coords = {1: (17.430, 78.490), 2: (17.430, 78.500), 3: (17.440, 78.500),
              4: (17.440, 78.490), 5: (17.435, 78.495)}
    for n, (lat, lon) in coords.items():
        G.add_node(n, lat=lat, lon=lon, x=lon, y=lat)
    for u, v in [(1, 2), (2, 3), (3, 4), (4, 1), (1, 5), (5, 3)]:
        G.add_edge(u, v, 0, highway="residential", length=1000.0)
        G.add_edge(v, u, 0, highway="residential", length=1000.0)
    return G


def ev(t, event, payload, agent="env"):
    return {"run_id": "unit", "mode": "independent", "seed": 1, "scenario": "unit",
            "rate": 20.0, "t": float(START + t), "agent_id": agent, "event": event,
            "payload": payload}


def closure_log():
    """V1 at node 1, road 1->2 closes at +100 for 600 s, V1 replans and goes 1->5->3."""
    return [
        ev(0, "sim_start", {"start_time_s": START, "duration_seconds": 1200.0,
                            "n_vehicles": 1, "n_orders": 1}),
        ev(50, "disruption", {"type": "closure", "edge": [3, 4, 0], "magnitude": None,
                              "duration": 30.0, "expiry": START + 80}),
        ev(60, "replan", {"trigger": "traffic_spike", "edge": [2, 3]}, "V1"),
        ev(100, "disruption", {"type": "closure", "edge": [1, 2, 0], "magnitude": None,
                               "duration": 600.0, "expiry": START + 700}),
        ev(100, "replan", {"trigger": "closure", "edge": [1, 2], "from_node": 1,
                           "route_found": True}, "V1"),
        ev(200, "edge_enter", {"edge": [1, 5, 0], "expected_duration": 50.0}, "V1"),
        ev(250, "edge_exit", {"node": 5}, "V1"),
        ev(250, "edge_enter", {"edge": [5, 3, 0], "expected_duration": 50.0}, "V1"),
        ev(300, "edge_exit", {"node": 3}, "V1"),
        ev(1200, "sim_complete", {"delivered": 0, "failed": 0}),
    ]


CONFIG = {"start_time_s": START, "scenario": "unit", "seed": 1, "mode": "independent",
          "disruption_rate_per_hour": 20.0,
          "vehicles": [{"id": "V1", "depot_node": 1, "capacity": 10}],
          "orders": [{"id": "O1", "node": 3, "demand": 1, "release_time": 0.0}]}


# --- closure helpers (no map) ----------------------------------------------

def test_first_closure_replan_skips_other_triggers():
    row = rf.first_closure_replan(closure_log())
    assert row["t"] == START + 100 and row["payload"]["trigger"] == "closure"
    assert rf.first_closure_replan([e for e in closure_log()
                                    if e["payload"].get("trigger") != "closure"]) is None


def test_closure_for_matches_edge_and_active_time():
    log = closure_log()
    replan = rf.first_closure_replan(log)
    closure = rf.closure_for(log, replan)
    assert closure["payload"]["edge"] == [1, 2, 0]
    late = dict(replan, t=START + 700)                  # at expiry: no longer active
    assert rf.closure_for(log, late) is None
    assert rf.closure_for(log, dict(replan, payload={"edge": None})) is None


def test_detour_and_snapshot_times():
    log = closure_log()
    replan = rf.first_closure_replan(log)
    closure = rf.closure_for(log, replan)
    detour = rf.detour_entry(log, replan, closure)
    assert detour["payload"]["edge"] == [1, 5, 0]       # leaves node 1, not on 1->2
    before, active = rf.snapshot_times(closure, replan, START, detour)
    assert before == START + 100 - rf.BEFORE_S
    assert active == START + 200 + rf.AFTER_S / 4
    # no detour: AFTER_S after the replan; never past the closure's end
    assert rf.snapshot_times(closure, replan, START)[1] == START + 100 + rf.AFTER_S
    short = dict(closure, payload=dict(closure["payload"], expiry=START + 150))
    assert rf.snapshot_times(short, replan, START)[1] == START + 149
    # a closure at the very start: "before" is not before the run
    assert rf.snapshot_times(closure, replan, START + 90)[0] == START + 90


def test_driven_after_lists_only_that_vehicle_in_window():
    assert rf.driven_after(closure_log(), "V1", START + 100, START + 260) == [(1, 5), (5, 3)]
    assert rf.driven_after(closure_log(), "V1", START + 100, START + 220) == [(1, 5)]
    assert rf.driven_after(closure_log(), "V2", START, START + 1200) == []


def test_closure_caption_names_run_and_times():
    log = closure_log()
    replan = rf.first_closure_replan(log)
    found = {"config": CONFIG, "replan": replan, "closure": rf.closure_for(log, replan),
             "path": Path("unit_s1.yaml")}
    text = rf.closure_caption(found, START + 40, START + 230,
                              rf.detour_entry(log, replan, rf.closure_for(log, replan)))
    assert "scenario unit (unit_s1.yaml), seed 1, rate 20/h, mode independent" in text
    assert "Road 1->2 closed at 06:01:40 for 10.0 min; V1 replanned at 06:01:40" in text
    assert "left node 1 on road 1->5" in text
    assert "Left: 06:00:40" in text and "Right: 06:03:50" in text


def test_closure_figure_on_small_graph(tmp_path):
    log = closure_log()
    replan = rf.first_closure_replan(log)
    found = {"config": CONFIG, "events": log, "replan": replan,
             "closure": rf.closure_for(log, replan), "path": Path("unit_s1.yaml")}
    out = tmp_path / "F_closure.png"
    info = rf.closure_figure(small_graph(), found, out)
    assert out.exists() and out.stat().st_size > 0
    # active = detour (+200) + AFTER_S / 4 = +230: only 1->5 entered by then
    assert info["active"] == START + 230
    assert info["vehicle"] == "V1" and info["edges_after"] == 1
    assert info["closures_active"] == [0, 1]            # before: none; active: 1->2


def test_region_points_split_and_dedupe():
    cfg = {"start_time_s": START,
           "vehicles": [{"id": "A", "depot_node": 9}, {"id": "B", "depot_node": 9},
                        {"id": "C", "depot_node": 7}],
           "orders": [{"node": 1, "release_time": START}, {"node": 1, "release_time": 0.0},
                      {"node": 2, "release_time": START + 10},
                      {"node": 1, "release_time": START + 10}]}
    pts = rf.region_points(cfg)
    assert pts == {"depots": [7, 9], "orders_start": [1], "orders_later": [2]}


# --- with the map ------------------------------------------------------------

@pytest.fixture(scope="module")
def graph():
    if not MAP_FILE.exists():
        pytest.skip("road graph file missing")
    return rf.load_graph()


@needs_map
def test_region_figure_real_scenario(graph, tmp_path):
    cfg = yaml.safe_load(rf.REGION_SCENARIO.read_text(encoding="utf-8"))
    out = tmp_path / "F_region.png"
    info = rf.region_figure(graph, cfg, out)
    assert out.exists() and out.stat().st_size > 0
    assert info["depots"] == len(cfg["scenario_meta"]["depots"])
    assert info["orders"] == len(cfg["orders"]) and info["missing_nodes"] == 0


@needs_map
def test_routes_are_deterministic_and_optimal(graph, tmp_path):
    pairs = rf.route_pairs(graph, seeds=(42, 43))
    assert pairs == rf.route_pairs(graph, seeds=(42, 43))      # fixed seeds
    routes = rf.plan_routes(graph, pairs)
    assert all(r["same_cost"] and r["path"][0] == r["source"]
               and r["path"][-1] == r["target"] for r in routes)
    out = tmp_path / "F_routes.png"
    rf.routes_figure(graph, routes, out)
    assert out.exists() and out.stat().st_size > 0


@needs_map
def test_find_closure_run_uses_a_real_closure_replan(tmp_path):
    # One seed only (about 10 s): small_hotspot_s1 at 20/h, the run report_figures picks.
    found = rf.find_closure_run(tmp_path, seeds=(1,), verbose=False)
    assert found is not None
    assert found["replan"]["event"] == "replan"
    assert found["replan"]["payload"]["trigger"] == "closure"
    assert found["closure"]["payload"]["type"] == "closure"
    assert found["config"]["seed"] == 1 and found["config"]["mode"] == "independent"
