"""Tests for src/search/replan_analysis.py (R7, Week 7), on small hand-written
logs whose answers are known, and on one real simulation run."""
import json
import math

import pytest

from src.search import replan_analysis as ra


def write_log(path, mode="independent", rate=10.0, seed=0, hours=2.0, n_vehicles=4,
              replans=(), kept=0, delivered=3, failed=1, summary=True):
    """replans = [(trigger, wall_s, cpu_s, route_found)]"""
    head = {"run_id": f"r_{mode}_{rate}_{seed}", "mode": mode, "seed": seed,
            "scenario": "toy", "rate": rate}
    rows = [dict(head, t=0.0, agent_id="env", event="sim_start",
                 payload={"start_time_s": 0.0, "duration_seconds": hours * 3600,
                          "n_vehicles": n_vehicles, "n_orders": delivered + failed})]
    rows.append(dict(head, t=1.0, agent_id="env", event="disruption",
                     payload={"type": "closure", "edge": [1, 2, 0]}))
    for trig, wall, cpu, found in replans:
        rows.append(dict(head, t=2.0, agent_id="V1", event="replan",
                         payload={"trigger": trig, "wall_seconds": wall, "cpu_seconds": cpu,
                                  "route_found": found}))
    for _ in range(kept):
        rows.append(dict(head, t=3.0, agent_id="V1", event="replan_kept",
                         payload={"wall_seconds": 0.001, "cpu_seconds": 0.0}))
    for i in range(delivered):
        rows.append(dict(head, t=4.0, agent_id="V1", event="order_delivered",
                         payload={"order_id": f"o{i}"}))
    for i in range(failed):
        rows.append(dict(head, t=5.0, agent_id="V1", event="order_failed",
                         payload={"order_id": f"f{i}", "reason": "unreachable"}))
    if summary:
        rows.append(dict(head, t=6.0, agent_id="env", event="replan_summary", payload={}))
    rows.append(dict(head, t=7.0, agent_id="env", event="sim_complete", payload={}))
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return str(path)


def test_one_run_counts_and_latency(tmp_path):
    p = write_log(tmp_path / "a.jsonl", hours=2.0, n_vehicles=4, kept=6, replans=[
        ("closure", 0.002, 0.0, True), ("traffic_spike", 0.004, 0.0156, True),
        ("closure", 0.010, 0.0, False),                  # no route: not a latency sample
        ("negotiation_yield", 0.001, 0.0, True), ("unreachable", 0.012, 0.0, True)])
    r = ra.summarise_run(p)
    assert r["disruption_replans"] == 3                  # closure x2 + spike
    assert r["replans_per_hour"] == pytest.approx(1.5)
    assert r["replans_per_vehicle_hour"] == pytest.approx(3 / 8)
    assert r["route_changes_per_hour"] == pytest.approx(2.5)
    assert r["kept_per_hour"] == pytest.approx(3.0)
    assert r["lat_wall_ms"] == pytest.approx([2.0, 4.0])
    assert r["negotiation_yields"] == 1 and r["unreachable_replans"] == 1
    assert r["failed_unreachable"] == 1 and r["complete"]


def test_logs_without_planner_or_not_sim_are_skipped(tmp_path):
    assert ra.summarise_run(write_log(tmp_path / "c.jsonl", summary=False)) is None
    (tmp_path / "x.jsonl").write_text('{"event": "something"}\n', encoding="utf-8")
    assert ra.summarise_run(str(tmp_path / "x.jsonl")) is None


def test_aggregate_mean_ci_and_pooled_percentiles(tmp_path):
    paths = [write_log(tmp_path / f"s{s}.jsonl", seed=s, hours=1.0,
                       replans=[("closure", 0.001 * (s + 1), 0.0, True)] * (s + 1))
             for s in range(3)]                         # 1, 2, 3 replans per hour
    runs, summary = ra.analyse(paths)
    (row,) = summary
    assert row["runs"] == 3 and row["replans"] == 6
    assert row["replans_per_hour"] == pytest.approx(2.0)
    assert row["replans_per_hour_ci95"] == pytest.approx(4.303 * 1.0 / math.sqrt(3))
    lat = [1.0, 2.0, 2.0, 3.0, 3.0, 3.0]
    assert row["wall_p50_ms"] == pytest.approx(ra.pct(lat, 0.5))
    assert row["wall_max_ms"] == pytest.approx(3.0)
    assert row["cpu_zero_share"] == pytest.approx(1.0)  # every cpu read 0.0 (Windows-like)


def test_groups_by_mode_and_rate(tmp_path):
    paths = [write_log(tmp_path / f"{m}{r}.jsonl", mode=m, rate=r, replans=[("closure", .001,
                                                                            0, True)])
             for m in ("independent", "full") for r in (0.0, 10.0)]
    _, summary = ra.analyse(paths)
    assert [(s["mode"], s["rate"]) for s in summary] == [
        ("full", 0.0), ("full", 10.0), ("independent", 0.0), ("independent", 10.0)]
    assert all(math.isnan(s["replans_per_hour_ci95"]) for s in summary)   # one run each


def test_helpers():
    assert ra.pct([], 0.5) != ra.pct([], 0.5)            # nan
    assert ra.pct([4, 1, 3, 2], 0.5) == 2.5
    assert ra.t95(1) == 12.706 and ra.t95(11) == 2.179 and ra.t95(500) == 1.96
    m, h = ra.mean_ci([5.0])
    assert m == 5.0 and math.isnan(h)


def test_cli_writes_csv_and_figure(tmp_path, monkeypatch, capsys):
    for r in (2.0, 10.0):
        for s in (0, 1):
            write_log(tmp_path / f"r{r}_s{s}.jsonl", rate=r, seed=s,
                      replans=[("closure", 0.002, 0.0, True)] * int(r))
    monkeypatch.setattr(ra, "OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(ra, "FIG_PATH", str(tmp_path / "out" / "fig.png"))
    monkeypatch.setattr(ra.plot, "__defaults__", (str(tmp_path / "out" / "fig.png"),))
    runs, summary = ra.main([str(tmp_path / "*.jsonl")])
    assert len(runs) == 4 and len(summary) == 2
    assert (tmp_path / "out" / "replan_runs.csv").exists()
    assert (tmp_path / "out" / "replan_summary.csv").exists()
    assert (tmp_path / "out" / "fig.png").stat().st_size > 1000
    assert "4 runs" in capsys.readouterr().out


def test_real_pilot_run(tmp_path):
    """One real scenario through R1's run_from_config: the log it writes is read.

    How many disruptions change a route depends on the traffic (R6's model and
    predictor load only where data/raw/metr-la.h5 is present): this run has 1
    disruption replan on free-flow and 0 with the predictor. So the test checks
    that summarise_run agrees with the raw rows, not a number of replans. The
    opening auction (contract-net) always gives vehicles their stops, so there
    are new_order replans whatever the traffic.
    """
    import os
    if not os.path.exists(os.path.join("configs", "scenarios", "small_uniform_s7.yaml")):
        pytest.skip("R3's scenario configs not present")
    paths = ra.make_pilot_logs(str(tmp_path), ["small_uniform"], [7], [20.0], ["independent"])
    (r,) = [ra.summarise_run(p) for p in paths]
    assert r["mode"] == "independent" and r["rate"] == 20.0 and r["complete"]

    with open(paths[0], encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    replans = [x["payload"] for x in rows if x["event"] == "replan"]
    found = [p for p in replans if p["trigger"] in ra.DISRUPTION_TRIGGERS and p["route_found"]]
    assert r["disruptions"] == sum(x["event"] == "disruption" for x in rows) > 0
    assert r["disruption_replans"] == sum(p["trigger"] in ra.DISRUPTION_TRIGGERS
                                          for p in replans)
    assert r["new_order_replans"] == sum(p["trigger"] == "new_order" for p in replans) >= 1
    assert r["lat_wall_ms"] == pytest.approx([p["wall_seconds"] * 1000 for p in found])
    assert r["rebuild_wall_ms"] and all(x > 0 for x in r["rebuild_wall_ms"])
    assert all(x > 0 for x in r["lat_wall_ms"])
