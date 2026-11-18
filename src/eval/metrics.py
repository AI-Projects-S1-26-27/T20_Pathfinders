"""R8 metrics (plan Section 7.2, decision D12): one run's log + config in, one flat row out.

    from src.eval.metrics import compute_metrics, load_road_info
    row = compute_metrics(rows, config, load_road_info())

``rows`` are the parsed lines of ``logs/<run_id>.jsonl`` (docs/log_schema.md). ``config`` is the
run's resolved config (src.sim.launcher.resolve_config): it supplies the order table (release time,
time window, priority, service time) and the vehicle list. ``RoadInfo`` supplies road lengths and
road classes from R2's graph.

Definitions (every one is a plain function of the log, so a rerun gives identical numbers):

* ``delivery_time_total_s``   sum over delivered orders of (delivery time - release time);
                              ``delivery_time_mean_s`` is the same per delivered order.
* ``makespan_s``              last delivery - run start.
* ``on_time_rate``            delivered orders whose service started by ``tw_end``, over released orders
                              (a failed or unfinished order counts as not on time).
* ``lateness_total_s``        sum over delivered orders of max(0, service start - tw_end).
* ``weighted_delivery_time_s`` sum of w * delivery time with w = 4 - priority (express 3, standard 2,
                              economy 1). The weights are our assumption: D3 names three tiers but
                              does not give weights. Time windows are almost never binding in our
                              scenarios, so this is the metric that shows priority-weighted lateness.
* ``distance_km``             sum of the lengths of every road a vehicle entered.
* ``wait_s``                  time vehicles stood at a node between two roads while they still had a
                              delivery to make, minus the service time done there (blocked-road waits
                              and waits for an order to be released are included).
* ``max_on_edge`` / ``violations_cap1`` / ``overload_s_cap1``
                              the most vehicles on one directed road at once; the number of times a
                              road went above capacity 1 (what the coordination layer enforces, see
                              DECISIONS.md "Simulator Integration Status"); the seconds spent above it.
                              ``*_d5`` use the D5 class capacities (residential 1, secondary 2,
                              primary 3, other roads 1). These count *actual* violations whether or
                              not the system predicted them (the plan's "unresolved conflicts").
* ``jain``                    Jain's index of per-vehicle workload (driving + serving time), all
                              vehicles of the config included.
* ``planning_cpu_s``          replan + coordination + allocation CPU (decentralized) or solver CPU
                              (centralized), D9, summed over the run (same accounting as
                              src.sim.demo_modes).
* ``cpu_per_decision_mean_s`` / ``_p95_s``  CPU of one planning decision: one replan (independent,
                              full) or one re-solve (centralized). The two are not the same amount
                              of work, which is why ``planning_cpu_s`` is the headline CPU number.
* ``messages_total``          negotiation messages + allocation messages (D7). Centralized: 2N per
                              re-solve (N state reports in, N plans out).
"""
import functools
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import networkx as nx

DEFAULT_MAP_PATH = "data/processed/secunderabad.graphml"
# D5 fleet-slot capacities; every other road class defaults to 1 (docs/DECISIONS.md).
D5_CAPACITIES = {"residential": 1, "secondary": 2, "primary": 3}
RANK = ["motorway", "trunk", "primary", "secondary", "tertiary",
        "unclassified", "residential", "living_street"]


@dataclass
class RoadInfo:
    """Static facts about the road graph that the metrics need."""
    length_m: Dict[Tuple[int, int, int], float] = field(default_factory=dict)
    road_class: Dict[Tuple[int, int], str] = field(default_factory=dict)

    def length(self, u: int, v: int, key: int) -> float:
        if (u, v, key) in self.length_m:
            return self.length_m[(u, v, key)]
        # the log carries the key of the road actually used; fall back to any road u -> v
        options = [d for (a, b, _), d in self.length_m.items() if a == u and b == v]
        if not options:
            raise KeyError(f"road ({u}, {v}, {key}) is not in the graph")
        return min(options)

    def capacity_d5(self, u: int, v: int) -> int:
        return D5_CAPACITIES.get(self.road_class.get((u, v), ""), 1)


def _clean_class(raw: Any) -> str:
    """'primary_link' -> 'primary'; a list or "['a', 'b']" -> the highest-ranked class."""
    import ast
    if isinstance(raw, str) and raw.startswith("["):
        vals = ast.literal_eval(raw)
    elif isinstance(raw, (list, tuple)):
        vals = list(raw)
    else:
        vals = [raw]
    vals = [str(v).replace("_link", "") for v in vals]
    return min(vals, key=lambda c: RANK.index(c) if c in RANK else len(RANK))


@functools.lru_cache(maxsize=2)
def load_road_info(path: str = DEFAULT_MAP_PATH) -> RoadInfo:
    G = nx.read_graphml(path)
    info = RoadInfo()
    best: Dict[Tuple[int, int], str] = {}
    for u, v, k, d in G.edges(keys=True, data=True):
        u_i, v_i, k_i = int(u), int(v), int(k)
        info.length_m[(u_i, v_i, k_i)] = float(d.get("length", 0.0))
        c = _clean_class(d.get("highway"))
        old = best.get((u_i, v_i))
        if old is None or _rank(c) < _rank(old):
            best[(u_i, v_i)] = c
    info.road_class = best
    return info


def _rank(c: str) -> int:
    return RANK.index(c) if c in RANK else len(RANK)


# ---------------------------------------------------------------------------
def jain_index(values: Sequence[float]) -> float:
    """Jain's fairness index (sum x)^2 / (n * sum x^2); 1.0 = perfectly even, 1/n = one does all."""
    vals = [float(v) for v in values]
    if not vals:
        return float("nan")
    total_sq = sum(v * v for v in vals)
    if total_sq == 0.0:
        return 1.0
    return sum(vals) ** 2 / (len(vals) * total_sq)


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (q in 0..100), the same as numpy's default."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return float("nan")
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def edge_pairs(rows: Iterable[Dict[str, Any]], end_t: float) -> List[Tuple[str, Tuple[int, int, int], float, float]]:
    """(vehicle, (u, v, key), t_enter, t_exit) for every road entry.

    A vehicle is on one road at a time: each ``edge_enter`` is closed by that vehicle's next
    ``edge_exit``. A road still open when the run ends is closed at ``end_t``.
    """
    open_edge: Dict[str, Tuple[Tuple[int, int, int], float]] = {}
    out = []
    for r in rows:
        ev = r["event"]
        if ev == "edge_enter":
            e = r["payload"]["edge"]
            open_edge[r["agent_id"]] = ((int(e[0]), int(e[1]), int(e[2])), float(r["t"]))
        elif ev == "edge_exit" and r["agent_id"] in open_edge:
            edge, t0 = open_edge.pop(r["agent_id"])
            out.append((r["agent_id"], edge, t0, float(r["t"])))
    for veh, (edge, t0) in open_edge.items():
        out.append((veh, edge, t0, max(end_t, t0)))
    return out


def occupancy_stats(pairs, capacity_of) -> Dict[str, float]:
    """Max vehicles on one directed road, how often a road went above capacity, and for how long.

    ``capacity_of(u, v)`` is the capacity of the directed road u -> v. Roads u -> v with different
    keys are the same road for this purpose. At equal timestamps exits are processed before enters.
    """
    events: Dict[Tuple[int, int], List[Tuple[float, int]]] = {}
    for _, (u, v, _k), t0, t1 in pairs:
        events.setdefault((u, v), []).extend([(t0, +1), (t1, -1)])
    max_occ = 0
    violations = 0
    overload_s = 0.0
    for (u, v), evs in events.items():
        cap = capacity_of(u, v)
        evs.sort(key=lambda x: (x[0], x[1]))      # -1 before +1 at the same time
        occ = 0
        prev_t = evs[0][0]
        for t, d in evs:
            if occ > cap:
                overload_s += t - prev_t
            if d > 0 and occ == cap:
                violations += 1                    # this entry takes the road above capacity
            occ += d
            max_occ = max(max_occ, occ)
            prev_t = t
    return {"max": max_occ, "violations": violations, "overload_s": overload_s}


def compute_metrics(rows: List[Dict[str, Any]], config: Dict[str, Any], road: RoadInfo) -> Dict[str, Any]:
    by_event: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_event.setdefault(r["event"], []).append(r)
    start_row = by_event["sim_start"][0]
    start = float(start_row["t"])
    duration = float(start_row["payload"]["duration_seconds"])
    end = start + duration
    done = (by_event.get("sim_complete") or [{"payload": {}}])[-1]["payload"]

    orders = {str(o["id"]): o for o in config.get("orders", [])}
    released = [o for o in orders.values() if float(o.get("release_time", 0.0)) <= end]
    n_released = len(released)

    # ---- delivery ----------------------------------------------------------------
    delivery_total = weighted_total = lateness_total = 0.0
    n_delivered = on_time = 0
    delivered_last = None
    served_by: Dict[str, float] = {}                # vehicle -> service seconds
    delivered_at: Dict[str, List[Tuple[float, float]]] = {}   # vehicle -> [(t, service_s)]
    for r in by_event.get("order_delivered", []):
        o = orders.get(str(r["payload"]["order_id"]))
        if o is None:
            continue
        t_done = float(r["t"])
        service = float(o.get("service_time", 0.0))
        rel = max(float(o.get("release_time", 0.0)), 0.0)
        n_delivered += 1
        dt = t_done - rel
        delivery_total += dt
        weighted_total += (4 - int(o.get("priority", 3))) * dt
        t_service_start = t_done - service
        late = max(0.0, t_service_start - float(o.get("tw_end", 86400.0)))
        lateness_total += late
        on_time += late == 0.0
        delivered_last = t_done if delivered_last is None else max(delivered_last, t_done)
        v = r["agent_id"]
        served_by[v] = served_by.get(v, 0.0) + service
        delivered_at.setdefault(v, []).append((t_done, service))
    n_failed = int(done.get("failed", len(by_event.get("order_failed", []))))

    # ---- roads: distance, waiting, congestion, fairness ----------------------------
    pairs = edge_pairs(rows, end)
    distance_m = sum(road.length(*edge) for _, edge, _, _ in pairs)
    drive_s: Dict[str, float] = {}
    by_vehicle: Dict[str, List[Tuple[float, float]]] = {}
    for veh, _edge, t0, t1 in pairs:
        drive_s[veh] = drive_s.get(veh, 0.0) + (t1 - t0)
        by_vehicle.setdefault(veh, []).append((t0, t1))
    wait_s = 0.0
    for veh, spans in by_vehicle.items():
        spans.sort()
        last_delivery = max((t for t, _ in delivered_at.get(veh, [])), default=None)
        if last_delivery is None:
            continue
        for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
            gap_end = min(b0, last_delivery)        # time after the last delivery is not waiting
            if gap_end <= a1:
                continue
            service_in_gap = sum(s for t, s in delivered_at.get(veh, []) if a1 < t <= gap_end)
            wait_s += max(0.0, (gap_end - a1) - service_in_gap)

    vehicle_ids = [str(v["id"]) for v in config.get("vehicles", [])]
    workload = [drive_s.get(v, 0.0) + served_by.get(v, 0.0) for v in vehicle_ids]

    occ1 = occupancy_stats(pairs, lambda u, v: 1)
    occ5 = occupancy_stats(pairs, road.capacity_d5)

    # ---- computation and messages -------------------------------------------------
    summary = (by_event.get("replan_summary") or [{"payload": {}}])[-1]["payload"]
    allocs = [r["payload"] for r in by_event.get("allocation_complete", [])]
    alloc_cpu = sum(float(a.get("cpu_seconds", 0.0)) for a in allocs)
    alloc_msgs = sum(int(a.get("messages", 0)) for a in allocs)
    mode = str(start_row.get("mode", config.get("mode", "")))
    if mode == "centralized":
        decisions = [float(a.get("cpu_seconds", 0.0)) for a in allocs]
        planning_cpu = alloc_cpu
        neg_msgs = 0
    else:
        decisions = [float(r["payload"].get("cpu_seconds", 0.0)) for r in by_event.get("replan", [])]
        planning_cpu = (float(summary.get("agent_cpu_seconds", 0.0))
                        + float(summary.get("coordination_cpu_seconds", 0.0)) + alloc_cpu)
        neg_msgs = int(summary.get("negotiation_messages", 0))

    disruptions = by_event.get("disruption", [])
    return {
        "mode": mode, "seed": start_row.get("seed"), "rate": start_row.get("rate"),
        "scenario": start_row.get("scenario"),
        "released": n_released, "delivered": n_delivered, "failed": n_failed,
        "unaccounted": n_released - n_delivered - n_failed,
        "delivery_time_total_s": delivery_total,
        "delivery_time_mean_s": delivery_total / n_delivered if n_delivered else float("nan"),
        "weighted_delivery_time_s": weighted_total,
        "makespan_s": (delivered_last - start) if delivered_last is not None else float("nan"),
        "on_time_rate": on_time / n_released if n_released else float("nan"),
        "lateness_total_s": lateness_total,
        "distance_km": distance_m / 1000.0,
        "wait_s": wait_s,
        "max_on_edge": occ1["max"],
        "violations_cap1": occ1["violations"], "overload_s_cap1": occ1["overload_s"],
        "violations_d5": occ5["violations"], "overload_s_d5": occ5["overload_s"],
        "jain": jain_index(workload),
        "disruptions": len(disruptions),
        "closures": sum(d["payload"].get("type") == "closure" for d in disruptions),
        "replans": len(by_event.get("replan", [])),
        "blocked_waits": len(by_event.get("blocked_wait", [])),
        "planning_cpu_s": planning_cpu,
        "cpu_per_decision_mean_s": (sum(decisions) / len(decisions)) if decisions else float("nan"),
        "cpu_per_decision_p95_s": percentile(decisions, 95),
        "n_decisions": len(decisions),
        "negotiation_msgs": neg_msgs, "alloc_msgs": alloc_msgs,
        "messages_total": neg_msgs + alloc_msgs,
    }
