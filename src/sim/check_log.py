"""W6 log checker: is a run's JSONL log complete and consistent?

    python -m src.sim.check_log logs/sim_demo.jsonl
    python -m src.sim.check_log runs/pilot/logs                  # every *.jsonl in the folder
    python -m src.sim.check_log logs/x.jsonl --config configs/x.yaml   # + every order accounted

Errors (exit code 1) are broken logs that R8's metrics would get wrong:

* a line that is not JSON, or does not have the nine keys in order (docs/log_schema.md);
* run_id / mode / seed / scenario / rate changing within one log;
* not exactly one sim_start (first row) and one sim_complete; after sim_complete
  only rows listed in AFTER_COMPLETE (R4's centralized runner logs allocation_summary);
* time going backwards, or outside [start, start + duration];
* an order ending twice, an order_failed without a known reason;
* sim_complete's delivered / failed not matching the order_delivered / order_failed rows;
* a vehicle entering a road before leaving the one it is on, or leaving at the wrong node;
* with --config: an order released before the end that never ended, or an unknown order id.

Warnings (printed, exit code 0): event names not in the schema (another role may
have added one; add it to docs/log_schema.md).
"""
import argparse
import glob
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

KEYS = ["run_id", "mode", "seed", "scenario", "rate", "t", "agent_id", "event", "payload"]
CONSTANT_KEYS = ["run_id", "mode", "seed", "scenario", "rate"]
KNOWN_EVENTS = {
    # R1 environment + generator
    "sim_start", "edge_enter", "edge_exit", "blocked_wait", "order_delivered",
    "capacity_blocked", "order_failed", "disruption", "closure_skipped", "new_order",
    "sim_complete",
    # R7 agent planner / R5 coordination
    "replan", "replan_kept", "stop_unreachable", "negotiation_request",
    "negotiation_response", "negotiation_result", "trigger_toggle", "hook_error",
    "replan_summary",
    # R4 allocation (contract-net and centralized runner)
    "allocation", "allocation_complete", "allocation_summary",
    "central_state_report", "central_plan_broadcast",
    # R3 knowledge base (src/kr/planner_rules.py, log_kr_trace)
    "kr_check",
}
FAIL_REASONS = {
    "time_window_missed", "invalid_time_window", "capacity_exceeded_unservable",
    "not_on_route", "unreachable", "no_vehicle", "no_feasible_bid", "horizon_exceeded",
    # R4's centralized runner (src/allocation/runtime.py, sim_runner.py)
    "central_unassigned", "service_infeasible",
}
# Rows allowed after sim_complete. TODO(R4): log allocation_summary before
# sim_complete (env.add_finisher), then this can be empty.
AFTER_COMPLETE = {"allocation_summary"}
T_EPS = 0.01        # t is rounded to 2 decimals


def released_order_ids(config: Dict[str, Any]) -> List[str]:
    """Orders in the config whose release_time is not after the end of the run."""
    from src.sim.environment import DEFAULT_DURATION_S, DEFAULT_START_TIME_S

    start = float(config.get("start_time_s", DEFAULT_START_TIME_S))
    end = start + float(config.get("duration_seconds", DEFAULT_DURATION_S))
    return [str(o["id"]) for o in config.get("orders", [])
            if float(o.get("release_time", 0.0)) <= end]


def check_rows(rows: List[Dict[str, Any]],
               config: Optional[Dict[str, Any]] = None) -> Tuple[List[str], List[str]]:
    """(errors, warnings) for one run's parsed rows."""
    errors: List[str] = []
    warnings: List[str] = []
    if not rows:
        return ["empty log"], warnings

    bad = [i for i, r in enumerate(rows, 1) if list(r) != KEYS]
    if bad:     # the checks below index these keys, so stop here
        return [f"{len(bad)} row(s) without the nine keys in order, first row {bad[0]}: "
                f"keys {list(rows[bad[0] - 1])}, expected {KEYS}"], warnings

    first = rows[0]
    for key in CONSTANT_KEYS:
        values = {json.dumps(r[key]) for r in rows}
        if len(values) > 1:
            errors.append(f"{key} changes within the log: {sorted(values)}")

    events = [r["event"] for r in rows]
    if events[0] != "sim_start":
        errors.append(f"first row is {events[0]!r}, expected 'sim_start'")
    for name in ("sim_start", "sim_complete"):
        if events.count(name) != 1:
            errors.append(f"{events.count(name)} {name} rows, expected 1")
    if "sim_complete" in events:
        after = events[events.index("sim_complete") + 1:]
        extra = sorted(set(after) - AFTER_COMPLETE)
        if extra:
            errors.append(f"rows after sim_complete: {extra}")
    unknown = sorted(set(events) - KNOWN_EVENTS)
    if unknown:
        warnings.append(f"events not in docs/log_schema.md: {unknown}")

    # -- clock --
    for i in range(1, len(rows)):
        if rows[i]["t"] < rows[i - 1]["t"]:
            errors.append(f"row {i + 1}: t {rows[i]['t']} < previous {rows[i - 1]['t']}")
            break
    if first["event"] == "sim_start":
        start = float(first["payload"].get("start_time_s", first["t"]))
        end = start + float(first["payload"].get("duration_seconds", 0.0))
        outside = [r["t"] for r in rows if not start - T_EPS <= r["t"] <= end + T_EPS]
        if outside:
            errors.append(f"{len(outside)} row(s) outside [{start}, {end}], e.g. t={outside[0]}")

    # -- orders --
    ended: Dict[str, str] = {}
    n_delivered = n_failed = 0
    for r in rows:
        if r["event"] not in ("order_delivered", "order_failed"):
            continue
        oid = str(r["payload"].get("order_id"))
        if oid in ended:
            errors.append(f"order {oid} ended twice ({ended[oid]}, then {r['event']} "
                          f"at t={r['t']})")
        ended[oid] = r["event"]
        if r["event"] == "order_delivered":
            n_delivered += 1
        else:
            n_failed += 1
            reason = r["payload"].get("reason")
            if reason not in FAIL_REASONS:
                errors.append(f"order {oid} failed with unknown reason {reason!r}")
    done = [r for r in rows if r["event"] == "sim_complete"]
    if done:
        p = done[0]["payload"]
        if (p.get("delivered"), p.get("failed")) != (n_delivered, n_failed):
            errors.append(f"sim_complete says delivered={p.get('delivered')} "
                          f"failed={p.get('failed')}, rows say {n_delivered} / {n_failed}")

    if config is not None:
        released = released_order_ids(config)
        missing = [o for o in released if o not in ended]
        if missing:
            errors.append(f"{len(missing)} released order(s) never delivered or failed: "
                          f"{missing[:10]}")
        known = {str(o["id"]) for o in config.get("orders", [])}
        strangers = sorted(set(ended) - known)
        if strangers:
            errors.append(f"orders not in the config: {strangers[:10]}")

    # -- vehicles: enter a road, leave it at its end node, then the next one --
    on_edge: Dict[str, Any] = {}
    for i, r in enumerate(rows, 1):
        vid = r["agent_id"]
        if r["event"] == "edge_enter":
            if vid in on_edge:
                errors.append(f"row {i}: {vid} enters {r['payload']['edge']} while still on "
                              f"{on_edge[vid]}")
            on_edge[vid] = r["payload"]["edge"]
        elif r["event"] == "edge_exit":
            edge = on_edge.pop(vid, None)
            if edge is None:
                errors.append(f"row {i}: {vid} leaves a road it never entered")
            elif r["payload"].get("node") != edge[1]:
                errors.append(f"row {i}: {vid} left {edge} at node {r['payload'].get('node')}")
    return errors, warnings


def read_rows(path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows, errors = [], []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():      # a blank line (e.g. at the end) is not an error
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                errors.append(f"row {i}: not JSON: {line[:80]!r}")
    return rows, errors


def check_file(path: str,
               config: Optional[Dict[str, Any]] = None) -> Tuple[List[str], List[str]]:
    rows, parse_errors = read_rows(path)
    if parse_errors:
        return parse_errors, []
    return check_rows(rows, config)


def expand(paths: Iterable[str]) -> List[str]:
    out: List[str] = []
    for p in paths:
        out.extend(sorted(glob.glob(os.path.join(p, "*.jsonl"))) if os.path.isdir(p) else [p])
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Check simulation JSONL logs.")
    ap.add_argument("paths", nargs="+", help="log files or folders of *.jsonl")
    ap.add_argument("--config", help="the run's config: also check every order is accounted")
    args = ap.parse_args(argv)

    config = None
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    files = expand(args.paths)
    if not files:
        print("no logs found", file=sys.stderr)
        return 1
    n_bad = 0
    for path in files:
        errors, warnings = check_file(path, config)
        n_bad += bool(errors)
        print(f"{'FAIL' if errors else 'ok  '}  {path}")
        for e in errors[:20]:
            print(f"      error: {e}")
        if len(errors) > 20:
            print(f"      ... {len(errors) - 20} more")
        for w in warnings:
            print(f"      warning: {w}")
    print(f"{len(files) - n_bad}/{len(files)} logs ok")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
