"""
R3 · How often the above-limit heuristic says "no" when a route exists.

Up to csp.EXACT_LIMIT (9) stops per vehicle, csp.set_feasible searches every stop order. Above
it, set_feasible only tries two orders (priority-first and earliest-deadline-first). Those can miss
a feasible route (a false "no") but never accept an infeasible one. This measures the miss rate.

    python -m src.kr.heuristic_gap --instances 300 --md docs/heuristic_gap.md

Ground truth: csp.find_sequence run with no limit. Its memo on (stops left, last stop) keeps it
to at most 2^n * n states, so 15 stops stay tractable. Each instance is one vehicle with n stops,
starting at 06:00. Two window settings: wide (20-60 min, almost all feasible) and tight
(10-30 min, about half feasible). Reported: feasible instances, how many of those the heuristic
rejects, and whether it ever accepts an infeasible one (it must not).
"""

from __future__ import annotations

import argparse
import math
import random
import time
from pathlib import Path

from .csp import find_sequence, schedule_in_order
from .ontology import SIM_START_S as T
from .ontology import Order, Vehicle


def instance(seed: int, n: int, width=(1200, 3600), spread=4 * 3600, area=8000.0):
    """One vehicle, n stops; windows open over the period, `width` seconds wide."""
    rng = random.Random(seed)
    pts = {i: (rng.random() * area, rng.random() * area) for i in range(n + 1)}

    def tt(a, b, t):
        return math.dist(pts[a], pts[b]) / 8.0  # about 29 km/h

    orders = []
    for i in range(1, n + 1):
        s = T + rng.uniform(0, spread - width[0])
        orders.append(
            Order(
                f"O{i}",
                i,
                1,
                rng.randint(1, 3),
                tw_start=s,
                tw_end=s + rng.uniform(*width),
                service_time=120.0,
                release_time=T,
            )
        )
    return Vehicle("V", 0, 10 * n, shift_end=T + spread + 3600), orders, tt


def heuristic_yes(v, orders, tt) -> bool:
    """Exactly what csp.set_feasible does above EXACT_LIMIT (capacity is never binding here)."""

    def deadline(o):
        return o.tw_end

    for key in ((lambda o: (o.priority, deadline(o))), (lambda o: (deadline(o), o.priority))):
        if schedule_in_order(v, sorted(orders, key=key), tt, T):
            return True
    return False


def measure(n: int, instances: int, seed0: int = 0, width=(1200, 3600)) -> dict:
    feasible = missed = wrong_yes = 0
    exact_s = heur_s = worst = 0.0
    for k in range(instances):
        v, orders, tt = instance(seed0 + 1000 * n + k, n, width=width)
        t0 = time.perf_counter()
        truth = find_sequence(v, orders, tt, T)[0] is not None
        took = time.perf_counter() - t0
        exact_s += took
        worst = max(worst, took)
        t0 = time.perf_counter()
        guess = heuristic_yes(v, orders, tt)
        heur_s += time.perf_counter() - t0
        feasible += truth
        missed += truth and not guess
        wrong_yes += guess and not truth
    return {
        "windows_min": f"{width[0] // 60}-{width[1] // 60}",
        "stops": n,
        "instances": instances,
        "feasible": feasible,
        "heuristic_says_no_but_route_exists": missed,
        "miss_rate_among_feasible": round(missed / feasible, 3) if feasible else None,
        "heuristic_says_yes_but_no_route": wrong_yes,
        "exact_ms": round(1000 * exact_s / instances, 2),
        "exact_worst_ms": round(1000 * worst, 1),
        "heuristic_ms": round(1000 * heur_s / instances, 3),
    }


WIDTHS = ((1200, 3600), (600, 1800))  # wide: almost all feasible; tight: about half feasible


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", type=int, default=300)
    ap.add_argument("--stops", type=int, nargs="+", default=[10, 12, 15])
    ap.add_argument("--md", default="docs/heuristic_gap.md")
    a = ap.parse_args()
    rows = [measure(n, a.instances, width=w) for w in WIDTHS for n in a.stops]
    head = (
        "| Windows (min) | Stops | Instances | Feasible (exact search) "
        "| Heuristic says no but a route exists | Miss rate among feasible "
        "| Heuristic says yes but no route | Exact ms (mean / worst) | Heuristic ms |"
    )
    lines = [head, "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        rate = (
            "n/a"
            if r["miss_rate_among_feasible"] is None
            else f"{100 * r['miss_rate_among_feasible']:.1f}%"
        )
        lines.append(
            f"| {r['windows_min']} | {r['stops']} | {r['instances']} | {r['feasible']} | "
            f"{r['heuristic_says_no_but_route_exists']} | {rate} | "
            f"{r['heuristic_says_yes_but_no_route']} | "
            f"{r['exact_ms']} / {r['exact_worst_ms']} | {r['heuristic_ms']} |"
        )
    Path(a.md).parent.mkdir(parents=True, exist_ok=True)
    Path(a.md).write_text(
        '# Above-limit heuristic: false "no" rate\n\n'
        f"Generated by `python -m src.kr.heuristic_gap --instances {a.instances}` "
        "(see the module docstring for the instance design).\n\n" + "\n".join(lines) + "\n"
    )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
