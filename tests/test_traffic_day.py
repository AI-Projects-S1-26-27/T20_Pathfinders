"""R1 Week 7: run.py's traffic_day and require_traffic keys, and the sim_start
fields that record them (traffic, traffic_day)."""
import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest

import src.sim.run as run_mod
from src.sim.run import build_simulation, check_traffic_loaded, traffic_day_of
from src.sim.world import FakeWorld
from src.world.traffic_model import day_for_seed

ROOT = Path(__file__).resolve().parents[1]
START = 21600.0


def line_graph():
    G = nx.MultiDiGraph()
    for n in range(3):
        G.add_node(n, lat=17.44, lon=78.50 + 0.001 * n)
    for a, b in ((0, 1), (1, 2)):
        G.add_edge(a, b, key=0, length=100.0, speed_kmh=40.0)
        G.add_edge(b, a, key=0, length=100.0, speed_kmh=40.0)
    return G


def config(tmp_path, **extra):
    cfg = {"run_id": "td", "scenario": "line", "seed": 7, "mode": "independent",
           "start_time_s": START, "duration_seconds": 60.0, "disruption_rate_per_hour": 0.0,
           "log_path": str(tmp_path / "td.jsonl"),
           "vehicles": [{"id": "V1", "depot_node": 0, "capacity": 5.0, "route": []}],
           "orders": [{"id": "O1", "node": 2, "demand": 1.0, "service_time": 0.0}]}
    cfg.update(extra)
    return cfg


def sim_start_payload(log_path):
    with open(log_path, encoding="utf-8") as f:
        return json.loads(f.readline())["payload"]


# -- traffic_day -------------------------------------------------------------
def test_traffic_day_default_is_day_zero():
    assert traffic_day_of({}) == 0


def test_traffic_day_int_and_per_seed():
    assert traffic_day_of({"traffic_day": 3}) == 3
    for seed in (0, 1, 9001, 9002):
        assert traffic_day_of({"traffic_day": "per_seed", "seed": seed}) == day_for_seed(seed)


@pytest.mark.parametrize("bad", [-1, 1.5, "monday", True, None])
def test_traffic_day_rejects_bad_values(bad):
    with pytest.raises(ValueError, match="traffic_day"):
        traffic_day_of({"traffic_day": bad})


def test_traffic_day_is_passed_to_from_defaults(tmp_path, monkeypatch):
    seen = {}

    def fake_from_defaults(cls, map_path, seed=42, day_offset=0):
        seen.update(seed=seed, day_offset=day_offset)
        return FakeWorld(line_graph())

    monkeypatch.setattr(run_mod.World, "from_defaults", classmethod(fake_from_defaults))
    build_simulation(config(tmp_path, traffic_day="per_seed", seed=9001))
    assert seen == {"seed": 9001, "day_offset": day_for_seed(9001)}
    build_simulation(config(tmp_path))
    assert seen["day_offset"] == 0                      # default unchanged


def test_traffic_day_with_a_given_world_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="only applies when run.py builds the world"):
        build_simulation(config(tmp_path, traffic_day=2), world=FakeWorld(line_graph()))


# -- require_traffic -----------------------------------------------------------
def test_require_traffic_fails_on_free_flow(tmp_path):
    with pytest.raises(RuntimeError, match="traffic model did not load"):
        build_simulation(config(tmp_path, require_traffic=True), world=FakeWorld(line_graph()))


def test_require_traffic_off_by_default_runs_on_free_flow(tmp_path):
    env, _ = build_simulation(config(tmp_path), world=FakeWorld(line_graph()))
    env.run(60.0)
    env.logger.close()
    p = sim_start_payload(tmp_path / "td.jsonl")
    assert p["traffic"] is False and p["traffic_day"] is None and p["predictor"] is False


def test_check_traffic_loaded_needs_the_predictor_only_when_used():
    no_predictor = SimpleNamespace(traffic_model=object(), predictor=None)
    check_traffic_loaded(no_predictor, use_predictor=False)
    with pytest.raises(RuntimeError, match="predictor did not load"):
        check_traffic_loaded(no_predictor, use_predictor=True)
    check_traffic_loaded(SimpleNamespace(traffic_model=object(), predictor=object()), True)


def test_require_traffic_must_be_a_bool(tmp_path):
    with pytest.raises(ValueError, match="require_traffic"):
        build_simulation(config(tmp_path, require_traffic="yes"), world=FakeWorld(line_graph()))


# -- with R6's data: the day reaches the traffic model and the log --------------
@pytest.mark.skipif(not (ROOT / "data" / "raw" / "metr-la.h5").exists(),
                    reason="R6's METR-LA data not present")
def test_per_seed_day_reaches_the_traffic_model_and_sim_start(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("h5py")
    cfg = config(tmp_path, traffic_day="per_seed", seed=9002, require_traffic=True)
    cfg["vehicles"] = []                                  # real map: no line-graph nodes
    cfg["orders"] = []
    env, _ = build_simulation(cfg)
    assert env.world.traffic_model.day_offset == day_for_seed(9002) != 0
    assert env.world.predictor.t0.day == 5 - 1 + day_for_seed(9002)   # 4 June 23:00 + day
    env.run(60.0)
    env.logger.close()
    p = sim_start_payload(tmp_path / "td.jsonl")
    assert p["traffic"] is True and p["predictor"] is True
    assert p["traffic_day"] == day_for_seed(9002)
