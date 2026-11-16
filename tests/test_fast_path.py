"""R3 Week 5: new-order fast path. Same answers as csp.eligible_vehicles; every route tested.
Line world: 60 s per unit of distance, depot 0, T = 06:00."""

import math
import random

import pytest

from src.kr.csp import eligible_vehicles, find_sequence, set_feasible
from src.kr.fast_path import NewOrderFilter, benchmark
from src.kr.ontology import SIM_START_S as T, Order, Vehicle


def tt(a, b, t):
    return 60.0 * abs(a - b)


def veh(vid="V", **kw):
    base = dict(id=vid, depot_node=0, capacity=10, shift_end=T + 4 * 3600)
    base.update(kw)
    return Vehicle(**base)


def order(i, node, tw=None, **kw):
    base = dict(
        id=i,
        node=node,
        demand=1,
        priority=2,
        service_time=60,
        release_time=T,
        tw_start=T + tw[0] if tw else 0.0,
        tw_end=T + tw[1] if tw else 86400.0,
    )
    base.update(kw)
    return Order(**base)


def decided(v, new, committed=(), t_now=T, travel=tt):
    f = NewOrderFilter([v], travel, t_now, {v.id: list(committed)})
    r = f.check(new)
    return r.decided_by[v.id], r.eligible == [v.id], r


@pytest.mark.parametrize(
    "v,new,committed,expect",
    [
        (veh(load=9), order("n", 2, demand=2), (), ("KR-1", False)),
        (veh(), order("n", 2, release_time=T + 500), (), ("KR-11", False)),
        (veh(), order("n", 9, tw=(0, 100)), (), ("bound", False)),  # 540 s away
        (veh(shift_end=T + 300), order("n", 9), (), ("bound", False)),  # cannot get back
        (veh(), order("n", 3), (order("a", 5),), ("insertion", True)),
    ],
    ids=["capacity", "release", "too-late", "shift-end", "insertion"],
)
def test_each_route(v, new, committed, expect):
    how, ok, _ = decided(v, new, committed)
    assert (how, ok) == expect


def test_unreachable_is_rejected_by_the_bound():
    def no_path_to_7(a, b, t):
        return math.inf if b == 7 else tt(a, b, t)

    assert decided(veh(), order("n", 7), travel=no_path_to_7)[:2] == ("bound", False)


def test_exact_route_finds_a_reordering_insertion_misses():
    """Plan b -> a works (b at 480 s, a at 900 s <= 1000). New c at node 9 must be served by 600 s.
    Slotting c in:  c,b,a -> a at 1080 (late); b,c,a -> a at 1080 (late); b,a,c -> c late.
    Reordering a,c,b works: a 120, c 600, b 720. Only the exact search finds it."""
    a = order("a", 2, tw=(0, 1000))
    b = order("b", 8, tw=(0, 3000))
    c = order("c", 9, tw=(0, 600))
    how, ok, res = decided(veh(), c, committed=[b, a])
    assert (how, ok) == ("exact", True)
    assert [o.id for o in res.sequence["V"]] == ["a", "c", "b"]


def test_exact_route_when_insertion_fails():
    # two orders whose windows force a -> b; committed in the wrong order b -> a is infeasible as
    # given, so the filter re-sequences it at start-up; the new order then needs exact search
    v = veh()
    a = order("a", 3, tw=(0, 200))
    b = order("b", 6, tw=(0, 1000))
    f = NewOrderFilter([v], tt, T, {"V": [b, a]})
    assert [o.id for o in f.plan["V"]] == ["a", "b"]  # fixed at start-up
    new = order("n", 5, tw=(300, 400))
    r = f.check(new)
    assert r.eligible == ["V"] and r.decided_by["V"] in ("insertion", "exact")


def test_assign_updates_the_plan_for_the_next_arrival():
    v = veh(capacity=3)
    f = NewOrderFilter([v], tt, T, {"V": [order("a", 2)]})
    first = order("n1", 4, demand=2)
    r = f.check(first)
    f.assign("V", first, r)
    assert [o.id for o in f.plan["V"]] == [o.id for o in r.sequence["V"]]
    r2 = f.check(order("n2", 6))
    assert r2.eligible == [] and r2.decided_by["V"] == "KR-1"  # 1 + 2 + 1 > 3


def random_case(seed):
    rng = random.Random(seed)
    vehicles = [
        veh(f"V{i}", capacity=rng.randint(3, 8), shift_end=T + rng.randint(1500, 4000))
        for i in range(4)
    ]

    def o(i):
        s = rng.uniform(0, 1800)
        return order(
            f"O{i}",
            rng.randint(1, 10),
            tw=(s, s + rng.uniform(200, 1200)) if rng.random() < 0.7 else None,
            demand=rng.randint(1, 3),
            release_time=T + rng.choice([0, 0, 0, 300]),
        )

    committed, k = {}, 0
    for v in vehicles:
        mine = []
        for _ in range(20):
            if len(mine) == 4:
                break
            cand = o(k)
            k += 1
            if set_feasible(v, mine + [cand], tt, T + 300):
                mine.append(cand)
        committed[v.id] = find_sequence(v, mine, tt, T + 300)[0]
    return vehicles, committed, [o(100 + i) for i in range(8)]


@pytest.mark.parametrize("seed", range(25))
def test_same_answer_as_the_exact_check(seed):
    vehicles, committed, new = random_case(seed)
    f = NewOrderFilter(vehicles, tt, T + 300, committed)
    for n in new:
        fast = f.check(n)
        exact = eligible_vehicles(n, vehicles, tt, T + 300, committed=committed)[0]
        assert sorted(fast.eligible) == sorted(exact), (n.id, fast.decided_by)
        for vid in fast.eligible:  # every accepted sequence really works
            assert set_feasible(vehicles[int(vid[1:])], fast.sequence[vid], tt, T + 300)


def test_with_r1_models():
    from src.sim.models import Order as SimOrder, Vehicle as SimVehicle

    v = SimVehicle("V1", depot_node=0, capacity=5.0, current_node=0)
    f = NewOrderFilter([v], tt, T, {"V1": [SimOrder("a", 3, 2.0, release_time=T)]})
    assert f.check(SimOrder("b", 4, 2.0, release_time=T)).eligible == ["V1"]
    assert f.check(SimOrder("c", 4, 4.0, release_time=T)).eligible == []


def test_benchmark_agrees_with_exact():
    r = benchmark(n_vehicles=6, per_vehicle=3, n_new=10, seed=5)
    assert r["same_answer"] == "10/10" and r["fast_ms_per_order"] > 0


def test_delivered_orders_are_not_counted_twice():
    """R1 adds a delivered order's demand to vehicle.load; the plan must then drop it."""
    from src.sim.models import Order as SimOrder, Vehicle as SimVehicle
    v = SimVehicle("V", 0, 5.0, current_node=0)
    a = SimOrder("a", 2, 3.0, release_time=T)
    f = NewOrderFilter([v], tt, T, {"V": [a]})
    v.load += a.demand                       # what R1's environment does on order_delivered
    v.current_node = 2
    new = SimOrder("n", 3, 2.0, release_time=T)          # 3 on board + 2 = 5 <= 5
    assert f.check(new).eligible == []                   # without the sync: counted twice
    assert f.mark_delivered("V", "a") and f.plan["V"] == []
    assert f.check(new).eligible == ["V"]
    assert not f.mark_delivered("V", "a")                # already gone


def test_failed_orders_free_their_room():
    from src.sim.models import Order as SimOrder, Vehicle as SimVehicle
    v = SimVehicle("V", 0, 4.0, current_node=0)
    f = NewOrderFilter([v], tt, T, {"V": [SimOrder("a", 2, 3.0, release_time=T)]})
    new = SimOrder("n", 3, 2.0, release_time=T)
    assert f.check(new).eligible == []                   # 3 + 2 > 4
    assert f.mark_failed("V", "a")
    assert f.check(new).eligible == ["V"]
