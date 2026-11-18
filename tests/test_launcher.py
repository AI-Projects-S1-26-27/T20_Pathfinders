"""R1 Week 5: sweep launcher (src/sim/launcher.py) - grid, resume, crashes, seeds."""
import json
from pathlib import Path

import networkx as nx
import pytest
import yaml

import src.sim.run as run_mod
from src.sim import launcher
from src.sim.world import FakeWorld

ROOT = Path(__file__).resolve().parents[1]
SMALL_MAP = ROOT / "data" / "processed" / "secunderabad_small.graphml"


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


@pytest.fixture
def grid_world(monkeypatch):
    """run_from_config builds this grid instead of loading the real map (inline runs only)."""
    monkeypatch.setattr(run_mod.World, "from_defaults",
                        classmethod(lambda cls, *a, **k: FakeWorld(grid_graph())))


def write_config(tmp_path, name="grid.yaml", **extra):
    cfg = {
        "run_id": "grid", "scenario": "grid", "seed": 1, "mode": "independent",
        "start_time_s": 21600.0, "duration_seconds": 1800.0,
        "disruption_rate_per_hour": 120.0, "solver_time_limit_s": 0.2,
        "vehicles": [
            {"id": "V1", "depot_node": 0, "capacity": 50.0, "route": [0, 1, 2, 3, 7, 11, 15]},
            {"id": "V2", "depot_node": 12, "capacity": 50.0, "route": [12, 13, 14, 15]},
        ],
        "orders": [
            {"id": "O1", "node": 3, "demand": 1.0, "service_time": 0.0},
            {"id": "O2", "node": 14, "demand": 1.0, "service_time": 0.0},
        ],
    }
    cfg.update(extra)
    path = tmp_path / name
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def sweep(tmp_path, config_glob, **extra):
    s = {"sweep_id": "t", "configs": [str(config_glob)], "modes": ["independent", "full"],
         "seeds": [1, 2], "out_dir": str(tmp_path / "out")}
    s.update(extra)
    path = tmp_path / "sweep.yaml"
    path.write_text(yaml.safe_dump(s), encoding="utf-8")
    return launcher.load_sweep(str(path))


# -- planning the grid ------------------------------------------------------
def test_plan_is_the_full_grid_with_readable_ids(tmp_path):
    write_config(tmp_path)
    specs = launcher.plan_runs(sweep(tmp_path, tmp_path / "gri*.yaml", rates=[5, 10],
                                     predictor=[True, False]))
    assert len(specs) == 1 * 2 * 2 * 2 * 2
    assert "grid__full__r10__s2__nopred" in {s.run_id for s in specs}
    assert len({s.run_id for s in specs}) == len(specs)


@pytest.mark.parametrize("bad, match", [
    ({"modes": ["greedy"]}, "unknown modes"),
    ({"seed": [1]}, "unknown keys in sweep"),
    ({"seeds": []}, "sweep needs 'seeds'"),
])
def test_bad_sweep_files_raise(tmp_path, bad, match):
    write_config(tmp_path)
    with pytest.raises(ValueError, match=match):
        sweep(tmp_path, tmp_path / "grid.yaml", **bad)


def test_missing_config_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        launcher.plan_runs(sweep(tmp_path, tmp_path / "nothing_*.yaml"))


def test_resolved_config_carries_the_spec(tmp_path):
    write_config(tmp_path, log_path="logs/should_be_dropped.jsonl")
    spec = launcher.plan_runs(sweep(tmp_path, tmp_path / "grid.yaml", rates=[7.5],
                                    predictor=[False], overrides={"duration_seconds": 60.0}))[0]
    cfg = launcher.resolve_config(spec)
    assert (cfg["mode"], cfg["seed"], cfg["disruption_rate_per_hour"]) == ("independent", 1, 7.5)
    assert cfg["predictor"] is False and cfg["duration_seconds"] == 60.0
    assert cfg["run_id"] == spec.run_id and "log_path" not in cfg


# -- running, resuming, crashing --------------------------------------------
def test_runs_then_resumes_without_rerunning(tmp_path, grid_world):
    write_config(tmp_path)
    specs = launcher.plan_runs(sweep(tmp_path, tmp_path / "grid.yaml"))
    first = launcher.run_sweep(specs, workers=1, echo=lambda *_: None)
    assert [r["status"] for r in first] == ["done"] * 4
    for spec in specs:
        log = Path(launcher._paths(spec)["log"])
        assert log.exists() and not Path(str(log) + ".part").exists()
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        assert rows[-1]["event"] == "sim_complete" and rows[0]["run_id"] == spec.run_id
    assert launcher.run_sweep(specs, workers=1, echo=lambda *_: None) == []   # resumed
    rows = launcher.collect_results(specs)
    assert [r["run_id"] for r in rows] == sorted(s.run_id for s in specs)


def test_results_record_the_commit(tmp_path, grid_world):
    """W7: every result row says which code made it (git commit + dirty)."""
    import csv
    import re
    import subprocess
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()
    write_config(tmp_path)
    specs = launcher.plan_runs(sweep(tmp_path, tmp_path / "grid.yaml"))
    launcher.run_sweep(specs, workers=1, echo=lambda *_: None)
    rows = launcher.collect_results(specs)
    assert {r["commit"] for r in rows} == {head} and re.fullmatch(r"[0-9a-f]{40}", head)
    assert all(isinstance(r["dirty"], bool) for r in rows)
    launcher.write_results(rows, str(tmp_path / "results.csv"))
    with open(tmp_path / "results.csv", encoding="utf-8") as f:
        assert {r["commit"] for r in csv.DictReader(f)} == {head}


def test_code_version_outside_git_is_none(monkeypatch):
    launcher.code_version.cache_clear()
    monkeypatch.setattr(launcher, "ROOT", "/")      # not a git checkout
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    try:
        assert launcher.code_version() == {"commit": None, "dirty": None}
    finally:
        launcher.code_version.cache_clear()


def test_version_warnings():
    clean = [{"run_id": "a", "commit": "c1", "dirty": False},
             {"run_id": "b", "commit": "c1", "dirty": False}]
    assert launcher.version_warnings(clean) == []
    mixed = launcher.version_warnings(clean + [{"run_id": "c", "commit": "c2", "dirty": False}])
    assert len(mixed) == 1 and "2 commits" in mixed[0]
    dirty = launcher.version_warnings([{"run_id": "a", "commit": "c1", "dirty": True}])
    assert len(dirty) == 1 and "uncommitted" in dirty[0] and "e.g. a" in dirty[0]


def test_centralized_run_is_renamed_after_r4_logs_past_env_run(tmp_path, grid_world):
    """R4's runner logs after env.run(); the log must still be released so the
    .part file can be renamed (Windows refuses to rename an open file)."""
    pytest.importorskip("ortools")
    write_config(tmp_path, disruption_rate_per_hour=0.0)
    specs = launcher.plan_runs(sweep(tmp_path, tmp_path / "grid.yaml",
                                     modes=["centralized"], seeds=[1]))
    row = launcher.run_sweep(specs, workers=1, echo=lambda *_: None)[0]
    assert row["status"] == "done", Path(launcher._paths(specs[0])["failed"]).read_text()
    log = Path(launcher._paths(specs[0])["log"])
    events = [json.loads(x)["event"] for x in log.read_text(encoding="utf-8").splitlines()]
    assert "allocation_summary" in events


def test_changed_config_runs_again(tmp_path, grid_world):
    path = write_config(tmp_path)
    specs = launcher.plan_runs(sweep(tmp_path, path, modes=["independent"], seeds=[1]))
    launcher.run_sweep(specs, workers=1, echo=lambda *_: None)
    write_config(tmp_path, duration_seconds=900.0)          # edit the scenario
    assert not launcher.is_done(specs[0])
    assert len(launcher.run_sweep(specs, workers=1, echo=lambda *_: None)) == 1


def test_crash_is_recorded_and_retried(tmp_path, grid_world, monkeypatch):
    write_config(tmp_path)
    specs = launcher.plan_runs(sweep(tmp_path, tmp_path / "grid.yaml",
                                     modes=["independent"], seeds=[1]))
    real = run_mod.run_from_config

    def boom(config, world=None):
        real(config, world)                     # writes the .part log, then dies
        raise RuntimeError("worker died")

    monkeypatch.setattr(run_mod, "run_from_config", boom)
    row = launcher.run_sweep(specs, workers=1, echo=lambda *_: None)[0]
    paths = launcher._paths(specs[0])
    assert row["status"] == "failed"
    assert "worker died" in Path(paths["failed"]).read_text(encoding="utf-8")
    assert not Path(paths["log"]).exists() and not launcher.is_done(specs[0])

    monkeypatch.setattr(run_mod, "run_from_config", real)
    assert launcher.run_sweep(specs, workers=1, echo=lambda *_: None)[0]["status"] == "done"
    assert not Path(paths["failed"]).exists()


def test_same_spec_gives_the_same_log(tmp_path, grid_world):
    """Seed control: a run depends only on its config, not on where it ran."""
    write_config(tmp_path)
    logs = []
    for out in ("a", "b"):
        spec = launcher.plan_runs(sweep(tmp_path, tmp_path / "grid.yaml", modes=["full"],
                                        seeds=[3], out_dir=str(tmp_path / out)))[0]
        launcher.run_sweep([spec], workers=1, echo=lambda *_: None)
        rows = [json.loads(x) for x in
                Path(launcher._paths(spec)["log"]).read_text(encoding="utf-8").splitlines()]
        for r in rows:              # CPU / wall timings are measurements, not outcomes
            for k in [k for k in r["payload"] if "cpu" in k or "wall" in k]:
                del r["payload"][k]
        logs.append(rows)
    assert logs[0] == logs[1]


@pytest.mark.skipif(not SMALL_MAP.exists(), reason="small real map not available")
def test_parallel_workers_on_real_small_map(tmp_path):
    """Spawned worker processes (no monkeypatching reaches them) on R2's small map."""
    from src.sim.scenarios import make_demo_config
    from src.sim.world import World
    from src.world.graph_loader import load_road_graph

    cfg = make_demo_config(World(load_road_graph(SMALL_MAP.stem)), seed=5)
    cfg.update(map_path=str(SMALL_MAP), duration_seconds=1800.0, disruption_rate_per_hour=60.0)
    (tmp_path / "small.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    specs = launcher.plan_runs(sweep(tmp_path, tmp_path / "small.yaml"))
    results = launcher.run_sweep(specs, workers=2, echo=lambda *_: None)
    assert sorted(r["status"] for r in results) == ["done"] * 4
    launcher.write_results(launcher.collect_results(specs), str(tmp_path / "results.csv"))
    lines = (tmp_path / "results.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("run_id,") and len(lines) == 5
