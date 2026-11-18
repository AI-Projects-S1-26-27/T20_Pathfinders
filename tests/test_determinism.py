"""R1 Week 6: determinism - same config + seed gives the same log (plan W6).

Compared: every row of the JSONL log, except CPU / wall-clock timings
(keys ending in cpu_seconds / wall_seconds), which measure the machine.
Checked in one process, across processes with different PYTHONHASHSEED
(set / dict order of strings changes per process: the sweep launcher runs
every run in its own worker), and through the launcher's results.

Grid world 4 x 4 (node = 4 * row + col), every road 100 m at 10 m/s.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import networkx as nx
import pytest
import yaml

import src.sim.run as run_mod
from src.sim import launcher
from src.sim.check_log import check_file
from src.sim.run import run_from_config
from src.sim.world import FakeWorld

ROOT = Path(__file__).resolve().parents[1]
START = 21600.0
MODES = ["independent", "full", "centralized"]


def grid_graph(n=4):
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


def grid_config(mode, log_path, seed=5, **extra):
    """Three vehicles, orders released over the run, one order no vehicle can
    carry (fails), disruptions often enough to close roads and force replans."""
    nodes = [3, 12, 15, 5, 10, 6, 9, 1, 14, 7, 8, 2]
    cfg = {
        "run_id": f"det_{mode}", "scenario": "grid", "seed": seed, "mode": mode,
        "start_time_s": START, "duration_seconds": 3600.0,
        "disruption_rate_per_hour": 60.0, "solver_time_limit_s": 0.2,
        "log_path": str(log_path),
        "vehicles": [
            {"id": "V1", "depot_node": 0, "capacity": 10.0, "route": []},
            {"id": "V2", "depot_node": 15, "capacity": 10.0, "route": []},
            {"id": "V3", "depot_node": 12, "capacity": 10.0, "route": []},
        ],
        "orders": [{"id": f"O{i}", "node": n, "demand": 2.0, "service_time": 30.0,
                    "release_time": START + 150.0 * i} for i, n in enumerate(nodes)]
        + [{"id": "BIG", "node": 9, "demand": 99.0, "service_time": 0.0}],
    }
    cfg.update(extra)
    return cfg


def run_grid(mode, log_path, seed=5):
    """Run the grid config; also the entry point of the subprocess test."""
    return run_from_config(grid_config(mode, log_path, seed), world=FakeWorld(grid_graph()))


def without_timings(obj):
    if isinstance(obj, dict):
        return {k: without_timings(v) for k, v in obj.items()
                if not (k.endswith("cpu_seconds") or k.endswith("wall_seconds"))}
    if isinstance(obj, list):
        return [without_timings(v) for v in obj]
    return obj


def read(path):
    with open(path, encoding="utf-8") as f:
        return [without_timings(json.loads(line)) for line in f]


def events(rows, name):
    return [r for r in rows if r["event"] == name]


def need(mode):
    if mode == "centralized":
        pytest.importorskip("ortools")


# -- one process -------------------------------------------------------------
@pytest.mark.parametrize("mode", MODES)
def test_same_seed_same_log(tmp_path, mode):
    need(mode)
    a = read(run_grid(mode, tmp_path / "a.jsonl"))
    b = read(run_grid(mode, tmp_path / "b.jsonl"))
    assert a == b
    # not vacuous: the run had disruptions, deliveries and a failure
    assert events(a, "disruption")
    assert events(a, "order_delivered")
    assert events(a, "order_failed")
    if mode != "centralized":
        assert events(a, "replan")
        assert events(a, "allocation")          # R4's contract-net awards


@pytest.mark.parametrize("mode", MODES)
def test_same_seed_log_passes_the_checker(tmp_path, mode):
    need(mode)
    path = run_grid(mode, tmp_path / "a.jsonl")
    errors, _ = check_file(path, grid_config(mode, path))
    assert errors == []


def test_different_seed_different_disruptions(tmp_path):
    """Guards the tests above: the seed really drives the run."""
    a = read(run_grid("independent", tmp_path / "a.jsonl", seed=5))
    b = read(run_grid("independent", tmp_path / "b.jsonl", seed=6))
    assert events(a, "disruption") != events(b, "disruption")


def test_timings_are_the_only_difference_allowed(tmp_path):
    """The comparison ignores timing keys only: everything else is compared."""
    rows = [{"payload": {"cpu_seconds": 1.0, "wall_seconds": 2.0,
                         "agent_cpu_seconds": 3.0, "gain_s": 4.0}}]
    assert without_timings(rows) == [{"payload": {"gain_s": 4.0}}]


# -- separate processes, different hash seeds (how the launcher runs) --------
SUBPROCESS = (
    "import sys; sys.path.insert(0, {tests!r}); "
    "from test_determinism import run_grid; run_grid({mode!r}, {log!r})"
)


@pytest.mark.parametrize("mode", ["full", "centralized"])
def test_same_log_across_processes_and_hash_seeds(tmp_path, mode):
    need(mode)
    logs = []
    for hash_seed in ("1", "2"):
        log = str(tmp_path / f"h{hash_seed}.jsonl")
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        code = SUBPROCESS.format(tests=str(ROOT / "tests"), mode=mode, log=log)
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True,
                       capture_output=True, timeout=600)
        logs.append(read(log))
    assert logs[0] == logs[1]
    assert events(logs[0], "disruption")


# -- with R6's traffic model and predictor (real map, real data) -------------
REAL_DATA = ROOT / "data" / "raw" / "metr-la.h5"
REAL_SCENARIO = ROOT / "configs" / "scenarios" / "small_uniform_s7.yaml"
REAL_SUBPROCESS = (
    "import sys; sys.path.insert(0, {tests!r}); "
    "from test_determinism import run_real; run_real({mode!r}, {log!r})"
)


def run_real(mode, log_path):
    """R3's small_uniform_s7 at 20 disruptions/h, traffic model and predictor on."""
    with open(REAL_SCENARIO, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.update(mode=mode, disruption_rate_per_hour=20.0, run_id=f"det_real_{mode}",
               log_path=str(log_path), predictor=True)
    return run_from_config(cfg)


@pytest.mark.skipif(not REAL_DATA.exists() or not REAL_SCENARIO.exists(),
                    reason="R6's METR-LA data or R3's scenario configs not present")
@pytest.mark.parametrize("mode", ["independent", "full"])
def test_same_log_with_traffic_and_predictor(tmp_path, mode):
    """Same as above with R6's traffic model and LSTM predictor loaded, in two
    processes with different PYTHONHASHSEED (as the launcher runs them)."""
    pytest.importorskip("torch")
    pytest.importorskip("h5py")
    logs = []
    for hash_seed in ("1", "2"):
        log = str(tmp_path / f"h{hash_seed}.jsonl")
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        code = REAL_SUBPROCESS.format(tests=str(ROOT / "tests"), mode=mode, log=log)
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True,
                       capture_output=True, timeout=600)
        logs.append(read(log))
    assert logs[0] == logs[1]
    start = events(logs[0], "sim_start")[0]["payload"]
    assert start["predictor"] is True            # not vacuous: the forecast was used
    assert events(logs[0], "disruption") and events(logs[0], "replan")


# -- through the launcher ----------------------------------------------------
@pytest.fixture
def grid_world(monkeypatch):
    """The launcher's runs build this grid instead of loading the real map."""
    monkeypatch.setattr(run_mod.World, "from_defaults",
                        classmethod(lambda cls, *a, **k: FakeWorld(grid_graph())))


def test_launcher_same_results_twice(tmp_path, grid_world):
    cfg = grid_config("independent", "unused.jsonl")
    cfg.pop("log_path")
    path = tmp_path / "grid.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    outputs = []
    for out in ("out1", "out2"):
        sweep = {"sweep_id": "det", "configs": [str(path)], "modes": ["independent", "full"],
                 "seeds": [5, 6], "out_dir": str(tmp_path / out)}
        specs = launcher.plan_runs(sweep)
        launcher.run_sweep(specs, workers=1, echo=lambda *_: None)
        rows = launcher.collect_results(specs)
        assert len(rows) == 4
        timing = {"wall_s", "planning_cpu_s", "log"}
        outputs.append([{k: v for k, v in r.items() if k not in timing} for r in rows])
        for r in rows:
            assert r["log_errors"] == 0           # the launcher ran the checker
            assert check_file(r["log"])[0] == []
    assert outputs[0] == outputs[1]
