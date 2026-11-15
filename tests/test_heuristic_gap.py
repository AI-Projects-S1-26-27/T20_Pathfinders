"""docs/heuristic_gap.md measures the real code path, and the above-limit heuristic is sound:
it can miss a route but never accepts one that does not exist."""

import pytest

from src.kr.csp import EXACT_LIMIT, find_sequence, set_feasible
from src.kr.heuristic_gap import heuristic_yes, instance, measure
from src.kr.ontology import SIM_START_S as T


@pytest.mark.parametrize("seed", range(30))
def test_heuristic_is_what_set_feasible_runs_above_the_limit(seed):
    v, orders, tt = instance(seed, EXACT_LIMIT + 2, width=(600, 1800))
    assert heuristic_yes(v, orders, tt) == set_feasible(v, orders, tt, T)


@pytest.mark.parametrize("n", [10, 12])
def test_heuristic_never_says_yes_without_a_route(n):
    r = measure(n, 40, width=(600, 1800))
    assert r["heuristic_says_yes_but_no_route"] == 0
    assert r["heuristic_says_no_but_route_exists"] <= r["feasible"]


def test_a_known_miss():
    """At least one instance where a route exists and the heuristic rejects it (the gap is real)."""
    for k in range(200):
        v, orders, tt = instance(k, 12, width=(600, 1800))
        if find_sequence(v, orders, tt, T)[0] is not None and not heuristic_yes(v, orders, tt):
            return
    pytest.fail("no miss found in 200 instances")
