"""R1 Week 4: mode switch in src/sim/run.py (centralized / independent / full).

Grid world 3 x 3 (node = 3 * row + col), every road 100 m at 10 m/s = 10 s.
"""
import json

import networkx as nx
import pytest

from src.sim.agent_planner import AgentPlanner
from src.sim.coordination_hook import TriggerCoordination
from src.sim.demo_modes import run_modes
from src.sim.environment import DeliverySimEnvironment
from src.sim.models import Event
from src.sim.run import build_simulation, run_from_config
from src.sim.world import FakeWorld

START = 21600.0


def grid_graph(n=3):
    G = nx.MultiDiGraph()
    for r in range(n):
        for c in range(n):
            # ~100 m apart, so R2's straight-line heuristic stays a lower bound
            G.add_node(n * r + c, lat=17.44 + 0.0009 * r, lon=78.50 + 0.0009 * c)
    for r in range(n):
        for c in range(n):
            a = n * r + c
            for b in ([a + 1] if c + 1 < n else []) + ([a + n] if r + 1 < n else []):
                # FakeWorld drives 10 m/s (36 km/h); 40 keeps the heuristic admissible
                G.add_edge(a, b, key=0, length=100.0, speed_kmh=40.0)
                G.add_edge(b, a, key=0, length=100.0, speed_kmh=40.0)
    return G


def config(tmp_path, mode, **extra):
    cfg = {
        "run_id": f"modes_{mode}", "seed": 3, "mode": mode, "scenario": "grid",
        "start_time_s": START, "duration_seconds": 3600.0,
        "disruption_rate_per_hour": 0.0, "solver_time_limit_s": 0.2,
        "log_path": str(tmp_path / f"{mode}.jsonl"),
        "vehicles": [
            {"id": "V1", "depot_node": 0, "capacity": 50.0, "route": [0, 1, 2, 5, 8]},
            {"id": "V2", "depot_node": 0, "capacity": 50.0, "route": [0, 1, 2, 5, 8]},
        ],
        "orders": [
            {"id": "O1", "node": 2, "demand": 5.0, "service_time": 0.0},
            {"id": "O2", "node": 8, "demand": 5.0, "service_time": 0.0},
        ],
    }
    cfg.update(extra)
    return cfg


def read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def close_road_at(env, u, v, after_s, duration_s=3600.0):
    """A closure announced through R1's event stream, as the generator does."""
    def proc():
        yield env.env.timeout(after_s)
        t = env.get_current_time()
        env.world.add_disruption(u, v, 0, t + duration_s, start_t_seconds=t)
        env.handle_event(Event(t, "disruption", {
            "type": "closure", "edge": [u, v, 0], "magnitude": None,
            "duration": duration_s, "expiry": t + duration_s}))
    env.env.process(proc())


# -- config checks ----------------------------------------------------------
def test_unknown_mode_raises(tmp_path):
    with pytest.raises(ValueError, match="mode must be one of"):
        build_simulation(config(tmp_path, "greedy"), FakeWorld(grid_graph()))


@pytest.mark.parametrize("block", ["planner", "coordination"])
def test_typo_in_block_raises(tmp_path, block):
    cfg = config(tmp_path, "full", **{block: {"tua": 1.0}})
    with pytest.raises(ValueError, match=f"unknown keys in {block} config"):
        build_simulation(cfg, FakeWorld(grid_graph()))


def test_predictor_switch(tmp_path):
    world = FakeWorld(grid_graph())
    world.predictor = object()                     # stands in for R6's Predictor
    env, _ = build_simulation(config(tmp_path, "independent", predictor=False), world)
    assert world.predictor is None
    env.run(10.0)
    assert read(env.log_path)[0]["payload"]["predictor"] is False
    with pytest.raises(ValueError, match="predictor must be true or false"):
        build_simulation(config(tmp_path, "independent", predictor="no"), FakeWorld(grid_graph()))


# -- independent ------------------------------------------------------------
def test_independent_uses_r7_planner_without_coordination(tmp_path):
    env, planner = build_simulation(config(tmp_path, "independent"), FakeWorld(grid_graph()))
    assert type(env) is DeliverySimEnvironment
    assert isinstance(planner, AgentPlanner)
    assert planner.mode == "independent" and planner.coordination is None
    assert set(planner.agents) == {"V1", "V2"}


def test_independent_replans_around_closure_and_ends_cleanly(tmp_path):
    env, planner = build_simulation(config(tmp_path, "independent"), FakeWorld(grid_graph()))
    close_road_at(env, 5, 8, after_s=5.0)        # on both routes, before they reach it
    env.run(3600.0)
    rows = read(env.log_path)
    events = [r["event"] for r in rows]
    assert events.count("replan") >= 1
    assert all(r["mode"] == "independent" for r in rows)
    assert events[-2:] == ["replan_summary", "sim_complete"]   # summary inside the run
    summary = rows[-2]["payload"]
    assert summary["negotiation_messages"] == 0
    assert rows[-1]["payload"] == {"delivered": 2, "failed": 0}
    assert not any((r["event"] == "edge_enter" and r["payload"]["edge"][:2] == [5, 8])
                   for r in rows)


@pytest.mark.parametrize("mode", ["independent", "full"])
def test_every_order_ends_delivered_or_failed_at_horizon(tmp_path, mode):
    """A vehicle still driving when time runs out must not lose its orders."""
    cfg = config(tmp_path, mode, duration_seconds=15.0)   # node 2 is 20 s away
    path = run_from_config(cfg, world=FakeWorld(grid_graph()))
    rows = read(path)
    failed = [r["payload"] for r in rows if r["event"] == "order_failed"]
    assert sorted(f["order_id"] for f in failed) == ["O1", "O2"]
    assert {f["reason"] for f in failed} == {"horizon_exceeded"}
    assert rows[-1]["payload"] == {"delivered": 0, "failed": 2}


# -- full -------------------------------------------------------------------
def test_full_wires_r5_trigger_with_config(tmp_path):
    cfg = config(tmp_path, "full", coordination={"tau": 123.0, "rate_limit_s": 5.0})
    _, planner = build_simulation(cfg, FakeWorld(grid_graph()))
    assert planner.mode == "full" and planner.trigger_enabled
    hook = planner.coordination
    assert isinstance(hook, TriggerCoordination)
    assert hook.trigger.tau == 123.0 and hook.rate_limit_s == 5.0
    assert hook.planner is planner                      # bound by AgentPlanner


HUB_ROUTES = {   # 5 x 5 grid, four vehicles converging on hub 24
    "V1": [0, 1, 2, 3, 4, 9, 14, 19, 24], "V2": [20, 21, 22, 23, 24],
    "V3": [2, 7, 12, 17, 22, 23, 24], "V4": [10, 11, 12, 13, 14, 19, 24],
}


def test_full_negotiates_where_independent_does_not(tmp_path):
    """Same scenario and seed, R1's disruption generator at 600/h: full mode
    negotiates conflicts after replans, independent never does.

    Seed 1 is pinned because on so small a grid most seeds give no conflict
    after a replan (seeds 2-8 give none); the run itself is deterministic.
    allocation "route" keeps the hand-written hub routes that set up the conflicts.
    """
    vehicles = [{"id": v, "depot_node": r[0], "capacity": 50.0, "route": r}
                for v, r in HUB_ROUTES.items()]
    orders = [{"id": f"O{v}", "node": r[len(r) // 2], "demand": 1.0, "service_time": 0.0}
              for v, r in HUB_ROUTES.items()]
    msgs = {}
    for mode in ("independent", "full"):
        cfg = config(tmp_path, mode, seed=1, vehicles=vehicles, orders=orders,
                     disruption_rate_per_hour=600.0, coordination={"tau": 1e9},
                     allocation="route")
        env, planner = build_simulation(cfg, FakeWorld(grid_graph(5)))
        env.run(3600.0)
        msgs[mode] = planner.negotiation_messages
        rows = read(env.log_path)
        assert rows[-1]["payload"] == {"delivered": 4, "failed": 0}
        n_results = sum(r["event"] == "negotiation_result" for r in rows)
        assert (n_results > 0) == (mode == "full")
    assert msgs["independent"] == 0
    assert msgs["full"] > 0


# -- centralized ------------------------------------------------------------
def test_centralized_uses_r4_resolver(tmp_path):
    pytest.importorskip("ortools")
    from src.allocation.sim_runner import AllocationSimulation

    cfg = config(tmp_path, "centralized")
    env, planner = build_simulation(cfg, FakeWorld(grid_graph()))
    assert isinstance(env, AllocationSimulation) and planner is None
    env.run(3600.0)
    rows = read(env.log_path)
    events = [r["event"] for r in rows]
    assert "allocation_complete" in events
    assert "replan" not in events
    assert sorted(r["payload"]["order_id"] for r in rows
                  if r["event"] == "order_delivered") == ["O1", "O2"]


# -- run_from_config + check-in demo ---------------------------------------
def test_run_from_config_accepts_world(tmp_path):
    path = run_from_config(config(tmp_path, "independent"), world=FakeWorld(grid_graph()))
    assert read(path)[-1]["event"] == "sim_complete"


def test_demo_runs_same_scenario_in_each_mode(tmp_path, monkeypatch):
    import src.sim.run as run_mod

    monkeypatch.setattr(run_mod.World, "from_defaults",
                        classmethod(lambda cls, *a, **k: FakeWorld(grid_graph())))
    cfg = config(tmp_path, "independent", disruption_rate_per_hour=120.0)
    results = run_modes(cfg, ["independent", "full"], str(tmp_path))
    assert [r["mode"] for r in results] == ["independent", "full"]
    assert results[0]["disruptions"] == results[1]["disruptions"] > 0   # same seed
    assert results[0]["negotiation_msgs"] == 0
    for r in results:
        assert r["delivered"] + r["failed"] == 2
