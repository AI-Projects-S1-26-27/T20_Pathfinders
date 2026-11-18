"""R3 Week 7: generated-order statistics next to the Amazon profile (src/data/scenario_stats.py)."""

import json

import pandas as pd
import yaml

from src.data.scenario_stats import amazon, generated, main, report
from src.kr.ontology import SIM_START_S as T


def order(i, tw=None, prio=2, release=T, service=60.0):
    lo, hi = tw if tw else (0.0, 86400.0)
    return {"id": f"o{i}", "node": i, "demand": 2.0, "priority": prio, "service_time": service,
            "release_time": release, "tw_start": lo, "tw_end": hi}


def write_cfg(d, name, orders, unservable=()):
    cfg = {"orders": orders, "vehicles": [],
           "scenario_meta": {"demand_source": "test", "orders_unservable_alone": list(unservable)}}
    (d / f"{name}.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_generated_counts(tmp_path):
    write_cfg(tmp_path, "a_s0", [order(0, tw=(T, T + 7200)), order(1, prio=1),
                                 order(2, release=T + 600, prio=3, service=120.0)])
    write_cfg(tmp_path, "b_s0", [order(3)], unservable=["o3"])
    g = generated(str(tmp_path / "*.yaml"))
    assert g["files"] == 2 and g["orders"] == 4
    assert g["share_with_tw"] == 0.25 and g["tw_width_h"]["0.5"] == 2.0
    assert g["released_during_run"] == 0.25 and g["orders_unservable_alone"] == 1
    assert g["priority_share"] == {1: 0.25, 2: 0.5, 3: 0.25}
    assert g["service_time_s"]["0.95"] > 60.0


def test_amazon_column_uses_the_per_stop_service_time(tmp_path):
    assert amazon(tmp_path) is None
    q = {"0.05": 1, "0.25": 2, "0.5": 3, "0.75": 4, "0.95": 5, "mean": 3}
    (tmp_path / "summary.json").write_text(json.dumps({
        "n_routes": 7, "share_stops_with_any_tw": 0.066, "tw_width_h": q,
        "service_time_per_stop_s(max)": q,
        "service_time_per_stop_s(sum)": {k: 10 * v for k, v in q.items()}}), encoding="utf-8")
    pd.DataFrame({"route_id": ["r"] * 4, "stop_id": ["a", "a", "b", "b"],
                  "service_s": [60.0, 60.0, 30.0, 30.0]}).to_csv(tmp_path / "packages.csv")
    a = amazon(tmp_path)
    assert a["service_column"] == "service_time_per_stop_s(max)" and a["service_time_s"] == q
    pd.DataFrame({"route_id": ["r"] * 4, "stop_id": ["a", "a", "b", "b"],
                  "service_s": [60.0, 20.0, 30.0, 10.0]}).to_csv(tmp_path / "packages.csv")
    assert amazon(tmp_path)["service_column"] == "service_time_per_stop_s(sum)"


def test_report_without_profile_says_so(tmp_path):
    write_cfg(tmp_path, "a_s0", [order(0)])
    md = report(generated(str(tmp_path / "*.yaml")), None)
    assert "no local profile" in md and "| | Amazon (no profile) | - |" in md


def test_cli_is_reproducible(tmp_path):
    write_cfg(tmp_path, "a_s0", [order(0, tw=(T, T + 3600)), order(1)])
    outs = []
    for i in range(2):
        md, js = tmp_path / f"s{i}.md", tmp_path / f"s{i}.json"
        assert main(["--configs", str(tmp_path / "*.yaml"), "--profile-dir", str(tmp_path / "x"),
                     "--md", str(md), "--json", str(js)]) == 0
        outs.append((md.read_bytes(), js.read_bytes()))
    assert outs[0] == outs[1] and b"\r\n" not in outs[0][0]
