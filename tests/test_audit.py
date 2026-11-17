"""R3 Week 5: constraint-violation audit. A real R1 run with disruptions must audit clean, and every
check must catch a violation planted in a log (one positive and one negative case each)."""

import json

import networkx as nx
import pytest
import yaml

from src.kr.audit import audit_log, find_config, main, markdown, read_log
from src.kr.ontology import SIM_START_S as T

CFG = {
    "run_id": "r",
    "scenario": "toy",
    "seed": 3,
    "vehicles": [
        {"id": "V", "depot_node": 0, "current_node": 0, "capacity": 6.0},
        {
            "id": "L",
            "depot_node": 0,
            "current_node": 0,
            "capacity": 5.0,
            "allowed_road_classes": ["primary", "secondary"],
        },
    ],
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
    "kr_zones": [{"id": "z", "nodes": [2], "start": T, "end": T + 3600}],
}


def graph():
    G = nx.MultiDiGraph()
    G.add_edge(0, 1, key=0, highway="primary")
    G.add_edge(1, 2, key=0, highway="residential")
    G.add_edge(1, 2, key=1, highway="primary")
    return G


def row(t, agent, event, **payload):
    return {
        "run_id": "r",
        "scenario": "toy",
        "seed": 3,
        "t": t,
        "agent_id": agent,
        "event": event,
        "payload": payload,
    }


def drive(agent, t, u, v, key=0):
    return [
        row(t, agent, "edge_enter", edge=[u, v, key], expected_duration=10.0),
        row(t + 10, agent, "edge_exit", node=v),
    ]


def deliver(agent, t, oid, node, demand=3.0):
    return [row(t, agent, "order_delivered", order_id=oid, node=node, demand=demand)]


CLEAN = (
    drive("V", T, 0, 1)
    + deliver("V", T + 200, "a", 1)
    + drive("V", T + 210, 1, 2)
    + deliver("V", T + 300, "b", 2)
)


def counts(rows, cfg=CFG, G=None):
    return audit_log(rows, cfg, G).counts()


def test_clean_log_has_no_violations():
    rep = audit_log(CLEAN, CFG, graph())
    assert rep.ok and rep.checked["KR-8"] == 1 and rep.checked["KR-4"] == 2


@pytest.mark.parametrize(
    "rule,rows",
    [
        ("KR-12", CLEAN + deliver("V", T + 400, "b", 2)),
        ("NODE", drive("V", T, 0, 1) + deliver("V", T + 200, "a", 2)),
        # arrives at node 2 at T+20; service (60 s) starts T+40: after arrival, before release T+50
        ("KR-11", drive("V", T, 0, 1) + drive("V", T + 10, 1, 2) + deliver("V", T + 100, "b", 2)),
        ("KR-7", drive("V", T, 0, 1) + deliver("V", T + 100, "a", 1)),
        ("KR-8", drive("V", T, 0, 1) + deliver("V", T + 700, "a", 1)),
        (
            "KR-1",
            CLEAN[:0]
            + drive("V", T, 0, 1)
            + deliver("V", T + 200, "a", 1)
            + drive("V", T + 210, 1, 2)
            + deliver("V", T + 300, "b", 2, demand=3.5),
        ),
        (
            "KR-4",
            [
                row(
                    T,
                    "env",
                    "disruption",
                    type="closure",
                    edge=[0, 1, 0],
                    magnitude=None,
                    duration=600.0,
                    expiry=T + 600,
                )
            ]
            + drive("V", T + 100, 0, 1),
        ),
        ("PATH", drive("V", T, 0, 1) + drive("V", T + 20, 0, 1)),
        # arrives at node 1 at T+160; 60 s service ending T+200 started at T+140
        ("KR-9", drive("V", T + 150, 0, 1) + deliver("V", T + 200, "a", 1)),
    ],
)
def test_each_check_catches_its_violation(rule, rows):
    c = counts(rows)
    assert c[rule] >= 1, c
    assert sum(c.values()) == c[rule], f"only {rule} should fire: {c}"


def test_closure_only_counts_while_active():
    closure = [
        row(
            T,
            "env",
            "disruption",
            type="closure",
            edge=[0, 1, 0],
            magnitude=None,
            duration=60.0,
            expiry=T + 60,
        )
    ]
    assert counts(closure + drive("V", T + 60, 0, 1))["KR-4"] == 0  # expiry is exclusive
    assert counts(closure + drive("V", T + 59, 0, 1))["KR-4"] == 1
    other_key = [
        row(
            T,
            "env",
            "disruption",
            type="closure",
            edge=[1, 2, 1],
            magnitude=None,
            duration=600.0,
            expiry=T + 600,
        )
    ]
    assert counts(other_key + drive("V", T, 0, 1) + drive("V", T + 20, 1, 2, key=0))["KR-4"] == 0


def test_road_class_and_zone_need_the_map():
    rows = drive("L", T, 0, 1) + drive("L", T + 20, 1, 2, key=0)  # residential, into zone
    assert counts(rows, G=graph())["KR-3"] == 1
    assert counts(rows, G=graph())["KR-6"] == 1
    assert counts(rows)["KR-3"] == 0 and "no map" in audit_log(rows, CFG).skipped[0]
    ok_rows = drive("L", T, 0, 1) + drive("L", T + 4000, 1, 2, key=1)  # primary, after hours
    assert counts(ok_rows, G=graph()) == counts([], G=graph())


def test_dynamic_orders_only_get_the_duplicate_check():
    rows = deliver("V", T, "dyn_1", 7, demand=1.0) + deliver("V", T + 5, "dyn_1", 7, demand=1.0)
    c = counts(rows)
    assert c["KR-12"] == 1 and c["NODE"] == 0 and c["KR-8"] == 0


def test_delivered_and_failed_is_a_violation():
    rows = CLEAN + [row(T + 900, "V", "order_failed", order_id="a", node=1, reason="x")]
    assert counts(rows)["KR-12"] == 1


def test_real_r1_run_with_disruptions_audits_clean(tmp_path):
    """R1's simulator on the small Secunderabad map, 30 closures/spikes per hour."""
    from src.sim.run import run_from_config
    from src.world.graph_loader import load_road_graph

    G = load_road_graph("secunderabad_small")
    nodes = sorted(G.nodes)
    depot, stops = nodes[0], nodes[10:130:12]
    route = [depot]
    for a, b in zip([depot] + stops, stops + [depot]):
        route += nx.shortest_path(G, a, b, weight="free_flow_time")[1:]
    orders = [
        {
            "id": f"o{i}",
            "node": n,
            "demand": 1.0,
            "service_time": 30.0,
            "release_time": T,
            "tw_start": T,
            "tw_end": T + 3 * 3600,
        }
        for i, n in enumerate(stops)
    ]
    cfg = {
        "run_id": "audit_real",
        "scenario": "audit_real",
        "seed": 1,
        "mode": "independent",
        "map_path": "data/processed/secunderabad_small.graphml",
        "start_time_s": T,
        "duration_seconds": 4 * 3600.0,
        "disruption_rate_per_hour": 30.0,
        "headless": True,
        "log_path": str(tmp_path / "run.jsonl"),
        "vehicles": [
            {
                "id": "V1",
                "depot_node": depot,
                "current_node": depot,
                "capacity": 50.0,
                "route": route,
            }
        ],
        "orders": orders,
    }
    rows = read_log(run_from_config(cfg))
    assert any(r["event"] == "disruption" for r in rows)
    rep = audit_log(rows, cfg, G)
    assert rep.ok, [v.as_dict() for v in rep.violations[:5]]
    assert rep.checked["KR-4"] > 50 and rep.checked["KR-12"] >= 1


def test_cli_exit_codes_config_lookup_and_reports(tmp_path):
    (tmp_path / "toy_s3.yaml").write_text(yaml.safe_dump(CFG), encoding="utf-8")
    good, badlog = tmp_path / "good.jsonl", tmp_path / "bad.jsonl"
    good.write_text("\n".join(json.dumps(r) for r in CLEAN), encoding="utf-8")
    badlog.write_text(
        "\n".join(json.dumps(r) for r in CLEAN + deliver("V", T + 400, "b", 2)), encoding="utf-8"
    )
    assert find_config(read_log(good), tmp_path) == tmp_path / "toy_s3.yaml"
    assert main([str(good), "--config-dir", str(tmp_path), "--no-map"]) == 0
    md, js = tmp_path / "a.md", tmp_path / "a.json"
    assert (
        main(
            [
                str(good),
                str(badlog),
                "--config-dir",
                str(tmp_path),
                "--no-map",
                "--md",
                str(md),
                "--json",
                str(js),
            ]
        )
        == 1
    )
    assert "| **all runs** |" in md.read_text(encoding="utf-8")
    assert [r["ok"] for r in json.loads(js.read_text(encoding="utf-8"))] == [True, False]
    (tmp_path / "other").mkdir()
    assert main([str(good), "--config-dir", str(tmp_path / "other")]) == 2


def test_markdown_table_totals():
    reps = [audit_log(CLEAN, CFG), audit_log(CLEAN + deliver("V", T + 400, "b", 2), CFG)]
    last = markdown(reps).splitlines()[-1]
    assert last.startswith("| **all runs** | 1 |") and last.endswith("| 1 |")


def test_wrong_way_road_needs_the_map():
    rows = [row(T, "V", "edge_enter", edge=[1, 0, 0], expected_duration=10.0),
            row(T + 10, "V", "edge_exit", node=0)]
    cfg = dict(CFG, vehicles=[{"id": "V", "depot_node": 1, "current_node": 1, "capacity": 6.0}])
    assert counts(rows, cfg, G=graph())["KR-5"] == 1        # only 0 -> 1 exists
    assert counts(rows, cfg)["KR-5"] == 0                    # no map: not checked
    assert counts(drive("V", T, 0, 1), G=graph())["KR-5"] == 0


def test_service_after_arrival_is_fine():
    rows = drive("V", T + 100, 0, 1) + deliver("V", T + 200, "a", 1)   # arrived 110, start 140
    assert counts(rows)["KR-9"] == 0


def test_audit_demo_is_reproducible(tmp_path):
    """scripts/run_audit_demo.py on a committed real-data scenario: clean, and the same twice."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("demo", root / "scripts" / "run_audit_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    outs = []
    for k in (1, 2):
        js = tmp_path / f"r{k}.json"
        args = ["--scenarios", "small_clustered_s0", "--log-dir", str(tmp_path / f"logs{k}"),
                "--md", str(tmp_path / f"r{k}.md"), "--json", str(js)]
        assert demo.main(args) == 0
        outs.append(json.loads(js.read_text(encoding="utf-8")))
    assert outs[0] == outs[1]
    r = outs[0][0]
    assert r["total_violations"] == 0 and r["checked"]["KR-4"] > 1000 and r["delivered"] > 0
