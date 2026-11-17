"""R1 Week 5: R4's contract-net hands out orders in independent / full (D8).

Grid 5 x 5 (node = 5 * row + col), every road 100 m at 10 m/s = 10 s.
V1 starts in the top-left corner (0), V2 in the bottom-right (24).
"""
import json

import networkx as nx
import pytest

from src.sim.run import build_simulation, run_from_config
from src.sim.world import FakeWorld

START = 21600.0


def grid_graph(n=5):
    G = nx.MultiDiGraph()
    for r in range(n):
        for c in range(n):
            G.add_node(n * r + c, lat=17.44 + 0.0009 * r, lon=78.50 + 0.0009 * c)
    for r in range(n):
        for c in range(n):
            a = n * r + c
            for b in ([a + 1] if c + 1 < n else []) + ([a + n] if r + 1 < n else []):
                G.add_edge(a, b, key=0, length=100.0, speed_kmh=40.0)
                G.add_edge(b, a, key=0, length=100.0, speed_kmh=40.0)
    return G


def order(oid, node, release=0.0, **kw):
    return {"id": oid, "node": node, "demand": 1.0, "service_time": 0.0,
            "release_time": release, **kw}


def config(tmp_path, mode="independent", orders=None, **extra):
    cfg = {
        "run_id": f"cnet_{mode}", "seed": 1, "mode": mode, "scenario": "grid",
        "start_time_s": START, "duration_seconds": 1800.0,
        "disruption_rate_per_hour": 0.0, "log_path": str(tmp_path / f"{mode}.jsonl"),
        "vehicles": [
            {"id": "V1", "depot_node": 0, "capacity": 10.0, "route": []},
            {"id": "V2", "depot_node": 24, "capacity": 10.0, "route": []},
        ],
        "orders": orders if orders is not None else [
            order("A", 1), order("B", 5), order("C", 23), order("D", 19)],
    }
    cfg.update(extra)
    return cfg


def read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def delivered_by(rows):
    return {r["payload"]["order_id"]: r["agent_id"] for r in rows
            if r["event"] == "order_delivered"}


@pytest.mark.parametrize("mode", ["independent", "full"])
def test_orders_go_to_the_nearest_vehicle(tmp_path, mode):
    rows = read(run_from_config(config(tmp_path, mode), world=FakeWorld(grid_graph())))
    assert delivered_by(rows) == {"A": "V1", "B": "V1", "C": "V2", "D": "V2"}
    assert rows[0]["event"] == "sim_start"                 # auctions come after it
    auctions = [r for r in rows if r["event"] == "allocation_complete"]
    assert [a["payload"]["order_id"] for a in auctions] == ["A", "B", "C", "D"]
    phases = {r["payload"]["phase"] for r in rows if r["event"] == "allocation"}
    assert phases == {"announcement", "bid", "award"}       # R4's D7 message rows
    assert rows[-1]["payload"] == {"delivered": 4, "failed": 0}


def test_hand_written_routes_are_not_used(tmp_path):
    cfg = config(tmp_path)
    cfg["vehicles"][0]["route"] = [0, 1, 2, 3, 4, 9, 14, 19, 24]   # would pass C and D
    rows = read(run_from_config(cfg, world=FakeWorld(grid_graph())))
    assert delivered_by(rows) == {"A": "V1", "B": "V1", "C": "V2", "D": "V2"}


def test_route_allocation_keeps_week4_behaviour(tmp_path):
    cfg = config(tmp_path, allocation="route")
    cfg["vehicles"][0]["route"] = [0, 1, 2, 3, 4, 9, 14, 19, 24]
    rows = read(run_from_config(cfg, world=FakeWorld(grid_graph())))
    # W4 rule: A and D are on V1's route; B and C on nobody's, so V1 (first) gets them too.
    assert delivered_by(rows) == {"A": "V1", "B": "V1", "C": "V1", "D": "V1"}
    assert not [r for r in rows if r["event"] == "allocation_complete"]


def test_unknown_allocation_raises(tmp_path):
    with pytest.raises(ValueError, match="allocation must be one of"):
        build_simulation(config(tmp_path, allocation="greedy"), FakeWorld(grid_graph()))


def test_late_order_is_auctioned_at_release_and_wakes_an_idle_vehicle(tmp_path):
    """Nobody knows E before it is released; V2 has nothing to do until then."""
    orders = [order("A", 1), order("E", 18, release=START + 300.0)]
    rows = read(run_from_config(config(tmp_path, orders=orders), world=FakeWorld(grid_graph())))
    auction_e = next(r for r in rows if r["event"] == "allocation_complete"
                     and r["payload"]["order_id"] == "E")
    assert auction_e["t"] == START + 300.0
    assert auction_e["payload"]["winner"] == "V2"
    assert not [r for r in rows if r["t"] < START + 300.0
                and r["event"] == "edge_enter" and r["agent_id"] == "V2"]   # idle before
    done_e = next(r for r in rows if r["event"] == "order_delivered"
                  and r["payload"]["order_id"] == "E")
    assert done_e["agent_id"] == "V2" and done_e["t"] > START + 300.0
    new_order_replans = [r for r in rows if r["event"] == "replan"
                         and r["payload"]["trigger"] == "new_order"]
    assert {r["agent_id"] for r in new_order_replans} == {"V1", "V2"}


def test_late_order_is_inserted_into_a_busy_vehicle_route(tmp_path):
    """V1 is on its way to 4; an order released at node 2 joins its route."""
    orders = [order("A", 4), order("F", 2, release=START + 5.0)]
    cfg = config(tmp_path, orders=orders)
    cfg["vehicles"] = cfg["vehicles"][:1]                   # only V1
    rows = read(run_from_config(cfg, world=FakeWorld(grid_graph())))
    assert delivered_by(rows) == {"A": "V1", "F": "V1"}
    t = {r["payload"]["order_id"]: r["t"] for r in rows if r["event"] == "order_delivered"}
    assert t["F"] < t["A"]                                  # cheapest insertion: on the way


def test_order_no_vehicle_can_carry_fails(tmp_path):
    orders = [order("A", 1), order("BIG", 23, demand=50.0)]
    rows = read(run_from_config(config(tmp_path, orders=orders), world=FakeWorld(grid_graph())))
    failed = [r for r in rows if r["event"] == "order_failed"]
    assert [(r["payload"]["order_id"], r["payload"]["reason"], r["agent_id"])
            for r in failed] == [("BIG", "no_feasible_bid", "env")]
    assert rows[-1]["payload"] == {"delivered": 1, "failed": 1}


def test_every_order_is_accounted_for_under_disruptions(tmp_path):
    """Many orders, half released during the run, disruptions at 300/h:
    each order ends exactly once, delivered or failed."""
    orders = [order(f"o{i}", (7 * i + 3) % 25, release=START + (60.0 * i if i % 2 else 0.0))
              for i in range(16)]
    for mode in ("independent", "full"):
        cfg = config(tmp_path, mode, orders=orders, disruption_rate_per_hour=300.0, seed=4)
        rows = read(run_from_config(cfg, world=FakeWorld(grid_graph())))
        ends = [r["payload"]["order_id"] for r in rows
                if r["event"] in ("order_delivered", "order_failed")]
        assert sorted(ends) == sorted(o["id"] for o in orders)        # once each
        assert rows[-1]["event"] == "sim_complete"


def test_snapshot_cache_changes_nothing(tmp_path, monkeypatch):
    """Reusing R4's travel snapshot must give exactly the run that a fresh
    snapshot per auction gives (only CPU timings may differ)."""
    from src.allocation.world_adapter import WorldTravel
    from src.sim.dispatch import ContractNetDispatcher

    orders = [order(f"o{i}", (7 * i + 3) % 25, release=START + (45.0 * i if i % 2 else 0.0))
              for i in range(14)]

    def run(name):
        cfg = config(tmp_path, "full", orders=orders, disruption_rate_per_hour=400.0, seed=6,
                     log_path=str(tmp_path / f"{name}.jsonl"))
        env, _ = build_simulation(cfg, FakeWorld(grid_graph()))
        env.run(cfg["duration_seconds"])
        rows = read(env.log_path)
        for r in rows:
            for k in [k for k in r["payload"] if "cpu" in k or "wall" in k]:
                del r["payload"][k]
        return rows, env.dispatcher

    cached, d = run("cached")
    assert d.n_snapshots < d.n_auctions                     # the cache was used
    assert any(r["event"] == "disruption" for r in cached)  # ... and rebuilt on disruptions
    monkeypatch.setattr(ContractNetDispatcher, "travel_at",
                        lambda self, t: WorldTravel(self.env.world, t))
    fresh, _ = run("fresh")
    assert cached == fresh


def test_set_stops_with_a_cut_off_stop_uses_r7_unreachable_policy(tmp_path):
    """R7's set_stops: an unreachable new stop is dropped, the order is offered
    for re-auction (nobody can reach it either) and failed as unreachable; the
    vehicle keeps a valid route through the stops it can still reach."""
    from src.sim.models import Order

    env, planner = build_simulation(config(tmp_path), FakeWorld(grid_graph()))
    env.run(1.0)
    t = env.get_current_time()
    for u in (7, 11, 13, 17):                              # every road into 12
        env.world.add_disruption(u, 12, 0, t + 3600.0, start_t_seconds=t)
    x = Order("X", 12, 1.0, service_time=0.0)
    keep = list(env.dispatcher.sequence["V1"])
    env.assign_order("V1", x)
    route = planner.set_stops("V1", keep + [x], t)
    assert route.nodes and 12 not in route.nodes
    assert x not in env._pending["V1"] and x not in env._pending["V2"]
    failed = [r for r in read(env.log_path) if r["event"] == "order_failed"]
    assert [(r["payload"]["order_id"], r["payload"]["reason"]) for r in failed] == \
        [("X", "unreachable")]


def test_reassign_moves_cut_off_orders_to_another_vehicle(tmp_path):
    """R7's reassign hook: V1's order is re-auctioned among the others."""
    env, planner = build_simulation(config(tmp_path), FakeWorld(grid_graph()))
    env.run(1.0)                                            # opening auction done
    dispatcher = env.dispatcher
    a = next(o for o in env._pending["V1"] if o.id == "A")
    taken = dispatcher.reassign("V1", [a], env.get_current_time())
    assert taken == [a]
    assert a in env._pending["V2"]
    assert "A" not in {o.id for o in dispatcher.sequence["V1"]}
    assert "A" in {o.id for o in dispatcher.sequence["V2"]}
    assert planner.reassign == dispatcher.reassign
