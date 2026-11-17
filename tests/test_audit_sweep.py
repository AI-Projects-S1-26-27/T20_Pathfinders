"""R3 Week 7: audit of a whole launcher sweep (src/kr/audit_sweep.py). A sweep directory is built by
hand in R1's launcher layout (logs/, done/, failed/), with run ids as R1's make_run_id writes them.
"""

import json

import networkx as nx
import yaml

from src.kr.audit import find_config
from src.kr.audit_sweep import (
    PLANTED,
    audit_sweep,
    config_for,
    main,
    plant,
    planted_checks,
    report,
    scenario_stem,
    status,
)
from src.kr.ontology import SIM_START_S as T

CFG = {
    "scenario": "toy",
    "seed": 3,
    "duration_seconds": 14400.0,
    "vehicles": [{"id": "V", "depot_node": 0, "current_node": 0, "capacity": 6.0}],
    "orders": [
        {
            "id": "a",
            "node": 1,
            "demand": 3.0,
            "service_time": 60.0,
            "release_time": T,
            "tw_start": T + 100,
            "tw_end": T + 500,
        },
        {"id": "b", "node": 2, "demand": 3.0, "service_time": 60.0, "release_time": T + 50},
    ],
}
RUN = "toy_s3__full__r10__s9001"


def row(t, event, agent="V", run=RUN, **payload):
    return {
        "run_id": run,
        "mode": "full",
        "seed": 9001,
        "scenario": "toy",
        "rate": 10.0,
        "t": t,
        "agent_id": agent,
        "event": event,
        "payload": payload,
    }


def clean(run=RUN):
    return [
        row(T, "sim_start", "env", run),
        row(T, "edge_enter", run=run, edge=[0, 1, 0]),
        row(T + 10, "edge_exit", run=run, node=1),
        row(T + 200, "order_delivered", run=run, order_id="a", node=1, demand=3.0),
        row(T + 210, "edge_enter", run=run, edge=[1, 2, 0]),
        row(T + 220, "edge_exit", run=run, node=2),
        row(T + 300, "order_delivered", run=run, order_id="b", node=2, demand=3.0),
        row(T + 400, "sim_complete", "env", run),
    ]


def graph():
    G = nx.MultiDiGraph()
    G.add_edge(0, 1, key=0, highway="primary")
    G.add_edge(1, 2, key=0, highway="residential")
    return G


def write_log(d, run_id, rows):
    (d / "logs").mkdir(parents=True, exist_ok=True)
    (d / "logs" / f"{run_id}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def sweep_dir(tmp_path, runs=(RUN,)):
    cdir = tmp_path / "configs"
    cdir.mkdir()
    (cdir / "toy_s3.yaml").write_text(yaml.safe_dump(CFG), encoding="utf-8")
    d = tmp_path / "runs" / "pilot"
    for r in runs:
        write_log(d, r, clean(r))
    return d, cdir


def test_run_id_gives_the_scenario_file():
    assert scenario_stem("large_uniform_s0__full__r10__s9001") == "large_uniform_s0"
    assert scenario_stem("small_hotspot_s3__centralized__s9001__nopred") == "small_hotspot_s3"


def test_plain_audit_finds_the_config_of_a_sweep_log(tmp_path):
    (tmp_path / "toy_s3.yaml").write_text(yaml.safe_dump(CFG), encoding="utf-8")
    # the log's seed is the run seed (9001), so toy_s9001.yaml does not exist
    assert find_config(clean(), tmp_path) == tmp_path / "toy_s3.yaml"


def test_overrides_of_the_sweep_are_applied(tmp_path):
    (tmp_path / "toy_s3.yaml").write_text(yaml.safe_dump(CFG), encoding="utf-8")
    cfg = config_for(RUN, tmp_path, {"duration_seconds": 600.0}, {})
    assert cfg["duration_seconds"] == 600.0 and len(cfg["orders"]) == 2
    assert config_for("nope_s1__full__s1", tmp_path, {}, {}) is None


def test_clean_sweep_is_zero_and_exit_0(tmp_path):
    d, cdir = sweep_dir(tmp_path, [RUN, "toy_s3__independent__r10__s9001"])
    res = audit_sweep(d, cdir, use_map=False)
    assert res["runs_audited"] == 2 and sum(res["violations"].values()) == 0
    assert res["checked"]["KR-12"] == 4 and res["checked"]["KR-7"] == 2
    assert status(res) == 0
    md = report(res)
    assert "**0 violations** in 2 audited runs" in md and "not audited" not in md


def test_every_planted_violation_is_flagged():
    got = planted_checks(clean(), CFG, graph())
    assert set(got) == set(PLANTED)
    assert all(got.values()), got


def test_planting_does_not_touch_the_original():
    rows, cfg = clean(), json.loads(json.dumps(CFG))
    for rule in PLANTED:
        plant(rows, cfg, rule, graph())
    assert rows == clean() and cfg == CFG


def test_a_violation_gives_exit_1_and_is_listed(tmp_path):
    d, cdir = sweep_dir(tmp_path)
    bad = clean()
    bad.insert(4, dict(bad[3]))  # order a delivered twice
    write_log(d, RUN, bad)
    res = audit_sweep(d, cdir, use_map=False, do_plant=False)
    assert res["violations"]["KR-12"] == 1 and res["runs_with_violations"] == 1
    assert status(res) == 1
    assert f"- {RUN} KR-12" in report(res)


def test_crashed_unfinished_and_unknown_runs_are_not_hidden(tmp_path):
    d, cdir = sweep_dir(tmp_path)
    (d / "failed").mkdir()
    (d / "failed" / "toy_s3__centralized__r10__s9001.txt").write_text("Traceback", encoding="utf-8")
    (d / "logs" / "toy_s3__full__r10__s9002.jsonl.part").write_text("", encoding="utf-8")
    write_log(d, "other_s1__full__r10__s9001", clean("other_s1__full__r10__s9001"))
    res = audit_sweep(d, cdir, use_map=False)
    assert res["not_audited"] == {
        "crashed": ["toy_s3__centralized__r10__s9001"],
        "unfinished_log": ["toy_s3__full__r10__s9002"],
        "no_config": ["other_s1__full__r10__s9001"],
    }
    assert res["runs_audited"] == 1 and status(res) == 2
    assert "3 runs not audited" in report(res)


def test_log_without_sim_complete_is_reported(tmp_path):
    d, cdir = sweep_dir(tmp_path)
    write_log(d, RUN, clean()[:-1])
    assert "Logs without `sim_complete` (1)" in report(audit_sweep(d, cdir, use_map=False))


def test_cli_writes_identical_reports_twice(tmp_path):
    d, cdir = sweep_dir(tmp_path)
    sweep = tmp_path / "sweep.yaml"
    sweep.write_text(
        yaml.safe_dump({"sweep_id": "pilot", "overrides": {"duration_seconds": 600.0}}),
        encoding="utf-8",
    )
    outs = []
    for i in range(2):
        md, js = tmp_path / f"a{i}.md", tmp_path / f"a{i}.json"
        args = [
            str(d),
            "--config-dir",
            str(cdir),
            "--sweep",
            str(sweep),
            "--no-map",
            "--md",
            str(md),
            "--json",
            str(js),
        ]
        assert main(args) == 0
        outs.append((md.read_bytes(), js.read_bytes()))
    assert outs[0] == outs[1]
    assert json.loads(outs[0][1])["overrides"] == {"duration_seconds": 600.0}
    assert b"\r\n" not in outs[0][0]


def test_kr3_note_depends_on_restricted_vehicles(tmp_path):
    d, cdir = sweep_dir(tmp_path)
    res = audit_sweep(d, cdir, use_map=False, do_plant=False)
    assert res["road_entries_restricted_vehicles"] == 0
    assert "says nothing about large vehicles" in report(res)
    cfg = json.loads(json.dumps(CFG))
    cfg["vehicles"][0]["allowed_road_classes"] = ["primary", "residential"]
    (cdir / "toy_s3.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    res = audit_sweep(d, cdir, use_map=False, do_plant=False)
    assert res["road_entries_restricted_vehicles"] == 2
    assert "KR-3: 2 road entries" in report(res)


def test_schema_gaps_are_reported_apart_from_violations(tmp_path):
    d, cdir = sweep_dir(tmp_path)
    rows = clean()
    for r in rows:
        if r["event"] == "order_delivered":
            r["payload"] = {"order_id": r["payload"]["order_id"]}
    write_log(d, RUN, rows)
    res = audit_sweep(d, cdir, use_map=False, do_plant=False)
    assert status(res) == 0 and sum(res["violations"].values()) == 0
    assert res["schema_gaps"]["order_delivered without node"] == {"rows": 2, "modes": ["full"]}
    assert "## Log-schema gaps (not violations)" in report(res)


def test_violations_are_planted_in_one_log_per_mode(tmp_path):
    d, cdir = sweep_dir(tmp_path)
    other = "toy_s3__centralized__r10__s9001"
    write_log(d, other, [dict(r, mode="centralized") for r in clean(other)])
    res = audit_sweep(d, cdir, use_map=False)
    assert set(res["planted"]) == {"full", "centralized"}
    assert res["planted"]["centralized"]["run_id"] == other
    assert res["planted"]["full"]["result"]["KR-12"] is True
    assert res["planted"]["full"]["result"]["KR-3"] is None  # needs the map
    assert "| Planted | centralized | full |" in report(res)
