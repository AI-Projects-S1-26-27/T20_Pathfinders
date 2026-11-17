"""
R3 · Week 7: run the constraint-violation audit (src/kr/audit.py) over every log of an experiment
sweep written by R1's launcher (src/sim/launcher.py), and write table T1's audit half.

    python -m src.kr.audit_sweep runs/<sweep_id> --sweep configs/sweeps/<sweep>.yaml \
        --md reports/audit_sweep.md --json reports/audit_sweep.json

The launcher writes runs/<sweep_id>/logs/<run_id>.jsonl (a run still going is <run_id>.jsonl.part),
done/<run_id>.json and failed/<run_id>.txt. A run_id starts with the scenario file's name
(large_uniform_s0__full__r10__s9001), so each log is audited against configs/scenarios/<that>.yaml
with the sweep's `overrides` applied, which is the config the run used for its vehicles and orders
(the sweep's mode/seed/rate/predictor do not change them).

Exit code 0 = every run audited, zero violations; 1 = violations; 2 = some run could not be
audited (crashed run, unfinished log, or no scenario config). Exit 0 therefore means "zero on
every run of the sweep", not "zero on the runs that happened to finish".

Planted checks: unless --no-plant, one violation of each kind in PLANTED is planted in a copy of
the first log of each mode (the modes log differently), and the report says whether the audit
flagged it under its own rule. This shows
that a zero comes from checks that fire on this log format, not from checks that never run.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import yaml

from .audit import CHECKS, audit_log, load_graph_for, read_log

NOT_CHECKED = [
    ("KR-10, KR-10a", "back at the depot by shift_end: no scenario or R1 Vehicle sets a shift end"),
    (
        "KR-2, KR-13, KR-14, KR-15",
        "allocation and stop-order decisions: the log shows their outcome, "
        "not the decision; covered by R3's unit tests",
    ),
    (
        "KR-16",
        "replan after a closure: covered indirectly, KR-4 catches a vehicle that drives onto "
        "a closed road instead",
    ),
    (
        "KR-7/KR-8 on dyn_* orders",
        "orders created during the run are not in the scenario file, so "
        "only KR-12 applies to them",
    ),
]


# ---------------------------------------------------------------- run id -> scenario config
def scenario_stem(run_id: str) -> str:
    """large_uniform_s0__full__r10__s9001 -> large_uniform_s0 (the launcher's make_run_id)."""
    return run_id.split("__", 1)[0]


def load_overrides(sweep_path: Optional[str]) -> dict:
    if not sweep_path:
        return {}
    sweep = yaml.safe_load(Path(sweep_path).read_text(encoding="utf-8")) or {}
    return dict(sweep.get("overrides") or {})


def config_for(run_id: str, config_dir: Path, overrides: dict, cache: dict) -> Optional[dict]:
    stem = scenario_stem(run_id)
    if stem not in cache:
        p = config_dir / f"{stem}.yaml"
        cache[stem] = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    cfg = cache[stem]
    if cfg is None:
        return None
    cfg = copy.deepcopy(cfg)
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------- what a log says about its run
def run_info(rows: list[dict]) -> dict:
    first = rows[0] if rows else {}
    failed = Counter(
        (r.get("payload") or {}).get("reason", "?")
        for r in rows
        if r.get("event") == "order_failed"
    )
    return {
        "mode": first.get("mode"),
        "seed": first.get("seed"),
        "rate": first.get("rate"),
        "complete": any(r.get("event") == "sim_complete" for r in rows),
        "delivered": sum(1 for r in rows if r.get("event") == "order_delivered"),
        "failed": sum(failed.values()),
        "failed_by_reason": dict(sorted(failed.items())),
        "closures": sum(
            1
            for r in rows
            if r.get("event") == "disruption" and (r.get("payload") or {}).get("type") == "closure"
        ),
        # what the closures did to routes: a replan after a closure changed a vehicle's route,
        # a replan_kept means the route was checked and kept, a blocked_wait is a vehicle that
        # had to wait at a closed road
        "closure_replans_changed": _count(rows, "replan", "closure"),
        "closure_replans_kept": _count(rows, "replan_kept", "closure"),
        "blocked_waits": sum(1 for r in rows if r.get("event") == "blocked_wait"),
    }


def _count(rows, event, trigger) -> int:
    return sum(
        1
        for r in rows
        if r.get("event") == event and (r.get("payload") or {}).get("trigger") == trigger
    )


# ---------------------------------------------------------------- planted violations
def _first(rows, event, pred=lambda r: True):
    for i, r in enumerate(rows):
        if r.get("event") == event and pred(r):
            return i, r
    return None, None


def plant(rows: list[dict], cfg: dict, rule: str, G=None):
    """A copy of (rows, cfg) with one violation of `rule`, or None if this log offers no place."""
    rows, cfg = copy.deepcopy(rows), copy.deepcopy(cfg)
    orders = {o["id"]: o for o in cfg.get("orders", [])}
    i, d = _first(rows, "order_delivered", lambda r: r["payload"].get("order_id") in orders)
    if rule == "KR-12" and d is not None:
        rows.insert(i + 1, copy.deepcopy(d))
    elif rule == "NODE" and d is not None:
        d["payload"]["node"] = -1
    elif rule == "KR-11" and d is not None:
        orders[d["payload"]["order_id"]]["release_time"] = float(d["t"]) + 3600.0
    elif rule == "KR-8" and d is not None:
        o = orders[d["payload"]["order_id"]]
        o["tw_start"], o["tw_end"] = 0.0, float(d["t"]) - o["service_time"] - 60.0
    elif rule == "KR-1" and d is not None:
        for v in cfg["vehicles"]:
            if v["id"] == d["agent_id"]:
                v["capacity"] = 0.0
    elif rule == "KR-3":
        from .audit import _road_class

        j, e = _first(
            rows, "edge_enter", lambda r: G is not None and G.has_edge(*r["payload"]["edge"])
        )
        if e is None:
            return None
        u, v, key = e["payload"]["edge"]
        cls = _road_class(G[u][v][key])
        for veh in cfg["vehicles"]:
            if veh["id"] == e["agent_id"]:
                veh["allowed_road_classes"] = [c for c in ROAD_CLASSES if c != cls]
    elif rule in ("KR-4", "PATH"):
        j, e = _first(rows, "edge_enter")
        if e is None:
            return None
        if rule == "PATH":
            e["payload"]["edge"] = [-1, e["payload"]["edge"][1], e["payload"]["edge"][2]]
        else:
            rows.insert(
                j,
                {
                    "t": float(e["t"]) - 1.0,
                    "agent_id": "env",
                    "event": "disruption",
                    "payload": {
                        "type": "closure",
                        "edge": list(e["payload"]["edge"]),
                        "expiry": float(e["t"]) + 1.0,
                    },
                },
            )
    else:
        return None
    return rows, cfg


PLANTED = ("KR-12", "NODE", "KR-11", "KR-8", "KR-1", "KR-4", "KR-3", "PATH")
ROAD_CLASSES = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "residential",
    "living_street",
    "service",
    "unclassified",
)


def planted_checks(rows: list[dict], cfg: dict, G=None) -> dict:
    out = {}
    for rule in PLANTED:
        p = plant(rows, cfg, rule, G)
        if p is None:
            out[rule] = None
            continue
        out[rule] = audit_log(p[0], p[1], G).counts().get(rule, 0) > 0
    return out


# ---------------------------------------------------------------- the sweep
def audit_sweep(
    runs_dir, config_dir="configs/scenarios", sweep=None, use_map=True, do_plant=True
) -> dict:
    runs_dir, config_dir = Path(runs_dir), Path(config_dir)
    overrides = load_overrides(sweep)
    logs = sorted((runs_dir / "logs").glob("*.jsonl"))
    unfinished = sorted(
        p.name[: -len(".jsonl.part")] for p in (runs_dir / "logs").glob("*.jsonl.part")
    )
    crashed = sorted(p.stem for p in (runs_dir / "failed").glob("*.txt"))
    cfg_cache, graphs, runs, no_config = {}, {}, [], []
    planted = {}  # mode -> {"run_id": ..., "result": {rule: True/False/None}}
    for log in logs:
        rows = read_log(log)
        run_id = log.stem
        cfg = config_for(run_id, config_dir, overrides, cfg_cache)
        if cfg is None:
            no_config.append(run_id)
            continue
        G = None
        if use_map and cfg.get("map_path"):
            if cfg["map_path"] not in graphs:
                graphs[cfg["map_path"]] = load_graph_for(cfg)
            G = graphs[cfg["map_path"]]
        rep = audit_log(rows, cfg, G)
        restricted = {v["id"] for v in cfg.get("vehicles", []) if v.get("allowed_road_classes")}
        mode = (rows[0].get("mode") if rows else None) or "?"
        if do_plant and mode not in planted:
            planted[mode] = {"run_id": run_id, "result": planted_checks(rows, cfg, G)}
        runs.append(
            {
                "run_id": run_id,
                "scenario": scenario_stem(run_id),
                **run_info(rows),
                "checked": dict(rep.checked),
                "road_entries_restricted_vehicles": sum(
                    1
                    for x in rows
                    if x.get("event") == "edge_enter" and x.get("agent_id") in restricted
                ),
                "violations_by_rule": rep.counts(),
                "violations": [v.as_dict() for v in rep.violations],
                "skipped": rep.skipped,
                "schema_gaps": dict(sorted(rep.schema_gaps.items())),
            }
        )
    checked, viol, gaps, gap_modes = Counter(), Counter(), Counter(), defaultdict(set)
    for r in runs:
        checked.update(r["checked"])
        viol.update(r["violations_by_rule"])
        gaps.update(r["schema_gaps"])
        for k in r["schema_gaps"]:
            gap_modes[k].add(r["mode"] or "?")
    return {
        "runs_dir": runs_dir.as_posix(),
        "sweep": Path(sweep).as_posix() if sweep else None,
        "overrides": overrides,
        "map": use_map,
        "runs_audited": len(runs),
        "runs_with_violations": sum(1 for r in runs if r["violations"]),
        "not_audited": {"crashed": crashed, "unfinished_log": unfinished, "no_config": no_config},
        "checked": {k: checked.get(k, 0) for k in CHECKS},
        "violations": {k: viol.get(k, 0) for k in CHECKS},
        "road_entries_restricted_vehicles": sum(
            r["road_entries_restricted_vehicles"] for r in runs
        ),
        "closure_effects_by_mode": _closure_effects_by_mode(runs),
        "schema_gaps": {k: {"rows": gaps[k], "modes": sorted(gap_modes[k])} for k in sorted(gaps)},
        "planted": dict(sorted(planted.items())) if do_plant else None,
        "runs": runs,
    }


def _closure_effects_by_mode(runs) -> dict:
    keys = ("closures", "closure_replans_changed", "closure_replans_kept", "blocked_waits")
    out: dict = {}
    for r in runs:
        t = out.setdefault(r["mode"] or "?", {"runs": 0, **{k: 0 for k in keys}})
        t["runs"] += 1
        for k in keys:
            t[k] += r[k]
    return dict(sorted(out.items()))


def status(res: dict) -> int:
    if any(res["not_audited"].values()) or res["runs_audited"] == 0:
        return 2
    return 1 if sum(res["violations"].values()) else 0


# ---------------------------------------------------------------- report
def _check_notes(res: dict) -> str:
    notes = ["KR-7/KR-8 are checked only for orders with a time window."]
    if res["checked"].get("KR-6", 0) == 0:
        notes.append("KR-6 is checked 0 times: no scenario defines a restricted zone (`kr_zones`).")
    n = res["road_entries_restricted_vehicles"]
    if n:
        notes.append(f"KR-3: {n:,} road entries were made by vehicles with `allowed_road_classes`.")
    else:
        notes.append(
            "KR-3 can only fail for a vehicle with `allowed_road_classes`, and no vehicle in these "
            "runs has one (small-only fleet), so its zero says nothing about large vehicles; the "
            "planted check below shows the check does fire."
        )
    return " ".join(notes)


def report(res: dict) -> str:
    total_v = sum(res["violations"].values())
    na = res["not_audited"]
    n_na = sum(len(v) for v in na.values())
    verdict = f"**{total_v} violations** in {res['runs_audited']} audited runs" + (
        f"; **{n_na} runs not audited**" if n_na else ""
    )
    out = [
        "# Constraint-violation audit of the sweep (T1, audit half)",
        "",
        f"Logs: `{res['runs_dir']}/logs`"
        + (f", sweep file `{res['sweep']}`" if res["sweep"] else "")
        + ". Generated by `python -m src.kr.audit_sweep`; do not edit by hand.",
        "",
        f"Result: {verdict}.",
        "",
    ]
    if n_na:
        out += ["Not audited:", ""]
        for k, ids in na.items():
            if ids:
                out.append(f"- {k.replace('_', ' ')} ({len(ids)}): " + ", ".join(ids))
        out.append("")
    out += ["## By check", "", "| Check | Times checked | Violations |", "|---|---|---|"]
    for k in CHECKS:
        note = "" if res["map"] or k not in ("KR-3", "KR-5", "KR-6") else " (no map)"
        out.append(f"| {k} | {res['checked'][k]:,}{note} | {res['violations'][k]} |")
    out += [
        "",
        _check_notes(res),
        "",
        "## By scenario and mode",
        "",
        "| Scenario | Mode | Runs | Road entries | Deliveries | Failed orders | Closures "
        "| Routes changed after a closure | Routes kept after a closure | Waits at a closed road "
        "| Violations |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    groups = defaultdict(list)
    for r in res["runs"]:
        groups[(r["scenario"], r["mode"])].append(r)
    for (sc, mode), rs in sorted(groups.items()):
        out.append(
            f"| {sc} | {mode} | {len(rs)} | {sum(r['checked'].get('PATH', 0) for r in rs):,} | "
            f"{sum(r['delivered'] for r in rs):,} | {sum(r['failed'] for r in rs)} | "
            f"{sum(r['closures'] for r in rs)} | {sum(r['closure_replans_changed'] for r in rs)} | "
            f"{sum(r['closure_replans_kept'] for r in rs)} | "
            f"{sum(r['blocked_waits'] for r in rs)} | "
            f"{sum(len(r['violations']) for r in rs)} |"
        )
    for mode, t in res["closure_effects_by_mode"].items():
        out.append(
            f"| **all** | **{mode}** | **{t['runs']}** | | | | **{t['closures']}** | "
            f"**{t['closure_replans_changed']}** | **{t['closure_replans_kept']}** | "
            f"**{t['blocked_waits']}** | |"
        )
    reasons = Counter()
    for r in res["runs"]:
        reasons.update(r["failed_by_reason"])
    if reasons:
        out += [
            "",
            "Failed orders by reason: "
            + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items()))
            + ".",
        ]
    incomplete = [r["run_id"] for r in res["runs"] if not r["complete"]]
    if incomplete:
        out += ["", f"Logs without `sim_complete` ({len(incomplete)}): " + ", ".join(incomplete)]
    if res["schema_gaps"]:
        out += [
            "",
            "## Log-schema gaps (not violations)",
            "",
            "Rows that lack a field `docs/log_schema.md` requires. The audit then checks NODE by "
            "the vehicle's position and KR-1 by the scenario's demand, so these are not "
            "violations, "
            "but the log should carry the fields.",
            "",
            "| Gap | Rows | Modes |",
            "|---|---|---|",
        ]
        for k, g in res["schema_gaps"].items():
            out.append(f"| {k} | {g['rows']:,} | {', '.join(g['modes'])} |")
    if res["planted"]:
        modes = list(res["planted"])
        out += [
            "",
            "## Planted violations (does the audit fire on these logs?)",
            "",
            "One violation of each kind planted in a copy of the first log of each mode: "
            + ", ".join(f"`{v['run_id']}`" for v in res["planted"].values())
            + ". Each cell: was it flagged under its own rule?",
            "",
            "| Planted | " + " | ".join(modes) + " |",
            "|---" * (len(modes) + 1) + "|",
        ]
        for rule in PLANTED:
            cells = []
            for m in modes:
                ok = res["planted"][m]["result"].get(rule)
                cells.append("yes" if ok else ("no place to plant" if ok is None else "**NO**"))
            out.append(f"| {rule} | " + " | ".join(cells) + " |")
    out += ["", "## Not checked by the audit", ""]
    out += [f"- {k}: {why}." for k, why in NOT_CHECKED]
    if res["runs_with_violations"]:
        out += ["", "## Violations", ""]
        for r in res["runs"]:
            for v in r["violations"][:20]:
                out.append(
                    f"- {r['run_id']} {v['rule']} t={v['t']:.0f} {v['agent_id']}: " f"{v['detail']}"
                )
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.rstrip("\n") + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit every log of a launcher sweep (R3, Week 7).")
    ap.add_argument("runs_dir", help="runs/<sweep_id> written by src/sim/launcher.py")
    ap.add_argument("--config-dir", default="configs/scenarios")
    ap.add_argument("--sweep", help="the sweep YAML, to apply its overrides to each config")
    ap.add_argument("--no-map", action="store_true", help="skip KR-3 / KR-5 / KR-6")
    ap.add_argument("--no-plant", action="store_true", help="skip the planted-violation check")
    ap.add_argument("--md", help="write the report here")
    ap.add_argument("--json", help="write every run's result here")
    a = ap.parse_args(argv)
    res = audit_sweep(a.runs_dir, a.config_dir, a.sweep, not a.no_map, not a.no_plant)
    text = report(res)
    for path, body in ((a.md, text), (a.json, json.dumps(res, indent=1) + "\n")):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
    print(text)
    return status(res)


if __name__ == "__main__":
    sys.exit(main())
