"""
R3 · Week 5: new-order CSP fast path.

When an order arrives during the run, every vehicle must be re-checked: can it take the new order on
top of what it already has? csp.eligible_vehicles answers that exactly, but runs the full stop-order
search per vehicle. Most vehicles are decided long before that:

    1. KR-1  capacity:  load + committed + new demand > capacity              -> reject
    2. KR-11 release:   order not released yet                                -> reject
    3. bound reach:     even driving straight there it is late (or no path),  -> reject
                        or it could not get back to the depot by shift_end
    4. insertion:       slot the order into the vehicle's current stop order;  -> accept
                        the first slot whose schedule is feasible wins
    5. exact:           csp.set_feasible on committed + new (the slow path)   -> accept / reject

Steps 1-3 only reject what is really infeasible (3 uses the direct trip as a lower bound on arrival,
true for shortest-path travel times), and step 4 only accepts a schedule it has checked, so the
answer equals eligible_vehicles whenever that one is exact (up to csp.EXACT_LIMIT stops);
above that the fast path can only accept MORE vehicles (insertion finds schedules the
priority-first / EDF fallback misses). tests/test_fast_path.py checks both.

    f = NewOrderFilter(vehicles, travel_time, t_now, committed={vid: [orders in stop order]})
    res = f.check(order)          # res.eligible, res.sequence[vid], res.decided_by[vid]
    f.assign(res.eligible[0], order, res)   # keep the plan up to date for the next arrival
"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .csp import TravelTime, find_sequence, schedule_in_order, set_feasible
from .ontology import SIM_START_S, position_of, shift_end_of, tw_of


@dataclass
class FastResult:
    order_id: str
    eligible: list  # vehicle ids that can take the order
    sequence: dict = field(default_factory=dict)  # vid -> stop order including the new order
    decided_by: dict = field(default_factory=dict)  # vid -> "KR-1" | "KR-11" | "bound" | ...
    seconds: float = 0.0


class NewOrderFilter:
    def __init__(
        self,
        vehicles,
        travel_time: TravelTime,
        t_now: float = SIM_START_S,
        committed: Optional[dict] = None,
        check_release: bool = True,
    ):
        self.V = {v.id: v for v in vehicles}
        self.tt, self.t_now, self.check_release = travel_time, t_now, check_release
        self.plan: dict = {}
        self.stats = Counter()
        for vid, v in self.V.items():
            orders = list((committed or {}).get(vid, []))
            # keep the given order if it is feasible, else find one (None = no known feasible plan)
            if schedule_in_order(v, orders, travel_time, t_now):
                self.plan[vid] = orders
            else:
                self.plan[vid] = find_sequence(v, orders, travel_time, t_now)[0]
                if self.plan[vid] is None:
                    self.plan[vid] = orders
                    self.stats["infeasible_plan"] += 1

    def set_time(self, t_now: float) -> None:
        self.t_now = t_now

    def _decide(self, v, order):
        plan = self.plan[v.id]
        demand = v.load + sum(o.demand for o in plan) + order.demand
        if demand > v.capacity:
            return False, None, "KR-1"
        if self.check_release and order.release_time > self.t_now:
            return False, None, "KR-11"
        # bound: direct trip from where the vehicle is now
        here = position_of(v)
        arrival = self.t_now + self.tt(here, order.node, self.t_now)
        if math.isinf(arrival):
            return False, None, "bound"
        tw = tw_of(order)
        start = max(arrival, tw[0] if tw else -math.inf, order.release_time)
        if tw and start > tw[1]:
            return False, None, "bound"
        back = start + order.service_time
        if back + self.tt(order.node, v.depot_node, back) > shift_end_of(v):
            return False, None, "bound"
        # insertion into the current stop order (only if that order is itself feasible)
        if schedule_in_order(v, plan, self.tt, self.t_now):
            for i in range(len(plan) + 1):
                seq = plan[:i] + [order] + plan[i:]
                if schedule_in_order(v, seq, self.tt, self.t_now):
                    return True, seq, "insertion"
        # exact (slow) path
        if set_feasible(v, plan + [order], self.tt, self.t_now, self.check_release):
            seq = find_sequence(v, plan + [order], self.tt, self.t_now)[0]
            return True, seq or plan + [order], "exact"
        return False, None, "exact"

    def check(self, order) -> FastResult:
        t0 = time.perf_counter()
        res = FastResult(order.id, [])
        for vid, v in self.V.items():
            ok, seq, how = self._decide(v, order)
            res.decided_by[vid] = how
            self.stats[how] += 1
            if ok:
                res.eligible.append(vid)
                res.sequence[vid] = seq
        res.seconds = time.perf_counter() - t0
        return res

    def assign(self, vid, order, res: Optional[FastResult] = None) -> None:
        """Record that vid took the order (uses the checked sequence when given)."""
        if res is not None and vid in res.sequence:
            self.plan[vid] = list(res.sequence[vid])
        else:
            self.plan[vid] = self.plan[vid] + [order]


def benchmark(n_vehicles=15, per_vehicle=4, n_new=40, seed=0, capacity=(8, 16)):
    """Fast path vs csp.eligible_vehicles on the same arrivals (table in docs/csp.md)."""
    import random

    from .csp import eligible_vehicles
    from .ontology import Order, Vehicle

    rng = random.Random(seed)
    T = SIM_START_S
    pts = {i: (rng.random() * 6000, rng.random() * 6000) for i in range(200)}

    def tt(a, b, t):
        return math.dist(pts[a], pts[b]) / 8.0

    def order(i):
        s = T + rng.uniform(0, 7200)
        w = rng.choice([None, rng.uniform(900, 3600)])
        return Order(
            f"O{i}",
            rng.randrange(1, 200),
            rng.randint(1, 3),
            rng.randint(1, 3),
            tw_start=s if w else 0.0,
            tw_end=s + w if w else 86400.0,
            service_time=120.0,
            release_time=T,
        )

    vehicles = [
        Vehicle(f"V{i}", 0, rng.randint(*capacity), shift_end=T + 4 * 3600)
        for i in range(n_vehicles)
    ]
    committed, k = {}, 0
    for v in vehicles:  # up to per_vehicle orders each, giving up after 50 tries
        mine = []
        for _ in range(50):
            if len(mine) == per_vehicle:
                break
            o = order(k)
            k += 1
            if set_feasible(v, mine + [o], tt, T):
                mine.append(o)
        committed[v.id] = find_sequence(v, mine, tt, T)[0]
    new = [order(1000 + i) for i in range(n_new)]

    f = NewOrderFilter(vehicles, tt, T, committed)
    fast_s, slow_s, same = 0.0, 0.0, 0
    for o in new:
        t0 = time.perf_counter()
        r = f.check(o)
        fast_s += time.perf_counter() - t0
        t0 = time.perf_counter()
        exact = eligible_vehicles(o, vehicles, tt, T, committed={v: f.plan[v] for v in f.plan})[0]
        slow_s += time.perf_counter() - t0
        same += sorted(r.eligible) == sorted(exact)
    return {
        "vehicles": n_vehicles,
        "committed_per_vehicle": per_vehicle,
        "new_orders": n_new,
        "capacity": list(capacity),
        "fast_ms_per_order": round(1000 * fast_s / n_new, 3),
        "exact_ms_per_order": round(1000 * slow_s / n_new, 3),
        "speedup": round(slow_s / fast_s, 1) if fast_s else None,
        "same_answer": f"{same}/{n_new}",
        "decided_by": dict(f.stats),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(benchmark(), indent=1))
