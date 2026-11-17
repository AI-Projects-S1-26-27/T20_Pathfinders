"""R1 Week 6: log checker (src/sim/check_log.py) - each broken log is caught."""
import copy
import json

import pytest

from src.sim.check_log import check_file, check_rows, main

START = 21600.0
HEAD = {"run_id": "r", "mode": "independent", "seed": 1, "scenario": "s", "rate": 5.0}


def row(t, event, payload, agent="env"):
    return {**HEAD, "t": t, "agent_id": agent, "event": event, "payload": payload}


def good_log():
    return [
        row(START, "sim_start", {"start_time_s": START, "duration_seconds": 600.0,
                                 "n_vehicles": 1, "n_orders": 2, "predictor": False}),
        row(START, "edge_enter", {"edge": [0, 1, 0], "expected_duration": 10.0}, "V1"),
        row(START + 10, "edge_exit", {"node": 1}, "V1"),
        row(START + 10, "order_delivered", {"order_id": "O1", "node": 1, "demand": 1.0}, "V1"),
        row(START + 10, "edge_enter", {"edge": [1, 2, 0], "expected_duration": 10.0}, "V1"),
        row(START + 600, "order_failed", {"order_id": "O2", "node": 5,
                                          "reason": "horizon_exceeded"}, "V1"),
        row(START + 600, "sim_complete", {"delivered": 1, "failed": 1}),
    ]


CONFIG = {"start_time_s": START, "duration_seconds": 600.0,
          "orders": [{"id": "O1", "node": 1}, {"id": "O2", "node": 5},
                     {"id": "LATE", "node": 2, "release_time": START + 9999}]}


def errors_of(rows, config=None):
    return check_rows(rows, config)[0]


def test_good_log_passes():
    assert check_rows(good_log(), CONFIG) == ([], [])


def broken(change):
    rows = copy.deepcopy(good_log())
    change(rows)
    return rows


@pytest.mark.parametrize("change, message", [
    (lambda r: r[1].pop("agent_id"), "nine keys"),
    (lambda r: r[3].update(seed=2), "seed changes"),
    (lambda r: r.pop(0), "first row"),
    (lambda r: r.pop(), "0 sim_complete"),
    (lambda r: r.append(row(START + 600, "edge_exit", {"node": 2}, "V1")),
     "after sim_complete"),
    (lambda r: r[2].update(t=START - 5), "t "),
    (lambda r: r[5].update(t=START + 900), "outside"),
    (lambda r: r.insert(4, row(START + 10, "order_failed", {
        "order_id": "O1", "node": 1, "reason": "not_on_route"}, "V1")), "ended twice"),
    (lambda r: r[5]["payload"].update(reason="bored"), "unknown reason"),
    (lambda r: r[6]["payload"].update(delivered=2), "sim_complete says"),
    (lambda r: r.pop(2), "while still on"),
    (lambda r: r[2]["payload"].update(node=7), "at node 7"),
])
def test_broken_log_is_caught(change, message):
    errors = errors_of(broken(change))
    assert any(message in e for e in errors), errors


def test_config_order_never_ended():
    rows = broken(lambda r: (r.pop(5), r[-1]["payload"].update(failed=0)))
    errors = errors_of(rows, CONFIG)
    assert any("never delivered or failed" in e and "O2" in e for e in errors), errors


def test_config_unknown_order():
    rows = broken(lambda r: r[3]["payload"].update(order_id="GHOST"))
    errors = errors_of(rows, CONFIG)
    assert any("not in the config" in e and "GHOST" in e for e in errors), errors


def test_order_released_after_the_end_need_not_end():
    assert "LATE" not in " ".join(errors_of(good_log(), CONFIG))


def test_allocation_summary_after_sim_complete_is_allowed():
    """R4's centralized runner logs it after sim_complete (TODO(R4))."""
    rows = good_log() + [row(START + 600, "allocation_summary", {})]
    assert errors_of(rows) == []


def test_unknown_event_is_a_warning_not_an_error():
    rows = good_log()
    rows.insert(3, row(START + 10, "something_new", {}))
    errors, warnings = check_rows(rows)
    assert errors == []
    assert any("something_new" in w for w in warnings)


def test_not_json_and_cli(tmp_path, capsys):
    good = tmp_path / "good.jsonl"
    good.write_text("".join(json.dumps(r) + "\n" for r in good_log()), encoding="utf-8")
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n", encoding="utf-8")
    assert check_file(str(good)) == ([], [])
    assert "not JSON" in check_file(str(bad))[0][0]
    assert main([str(good)]) == 0
    assert main([str(tmp_path)]) == 1             # folder: both files, one bad
    out = capsys.readouterr().out
    assert "1/2 logs ok" in out


def test_blank_lines_are_skipped(tmp_path):
    """W6 review: a blank line at the end (or between rows) is harmless."""
    lines = [json.dumps(r) for r in good_log()]
    text = lines[0] + "\n\n" + "\n".join(lines[1:]) + "\n\n  \n"   # blank in the middle + end
    path = tmp_path / "blank.jsonl"
    path.write_text(text, encoding="utf-8")
    assert check_file(str(path)) == ([], [])


def test_empty_file_is_still_an_error(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("\n\n", encoding="utf-8")
    assert check_file(str(path))[0] == ["empty log"]
