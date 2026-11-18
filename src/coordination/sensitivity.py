"""Stand-alone (tau, delta_t) sensitivity analysis on a synthetic bottleneck."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import random
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

from src.coordination.conflict import (
    Edge,
    VehicleRoute,
    detect_conflicts,
    prepare_occupancies,
)
from src.coordination.messages import MessageCounter
from src.coordination.negotiation import VirtualCreditBudget
from src.coordination.resolver import NegotiationRateLimiter
from src.coordination.resolver import resolve_conflicts_sequentially
from src.coordination.trigger import ThresholdTrigger

DEFAULT_TAU_GRID: Tuple[float, ...] = (30.0, 60.0, 120.0, 300.0)
DEFAULT_DELTA_T_GRID: Tuple[float, ...] = (10.0, 20.0, 40.0)
DEFAULT_PILOT_SEEDS: Tuple[int, ...] = (301, 302, 303, 304, 305)
DEFAULT_TEST_SEEDS: Tuple[int, ...] = (
    401,
    402,
    403,
    404,
    405,
    406,
    407,
    408,
    409,
    410,
)


@dataclass(frozen=True)
class SweepCellResult:
    """Aggregated metrics for one (tau, delta_t) grid cell."""

    tau: float
    delta_t: float
    num_seeds: int
    mean_messages: float
    mean_unresolved_conflicts: float
    mean_total_delay_s: float
    mean_resolutions: float

    def to_dict(self) -> Dict[str, float | int]:
        """Serialize cell metrics to a JSON-compatible dictionary."""
        return asdict(self)


def verify_seed_disjointness(
    pilot_seeds: Sequence[int],
    test_seeds: Sequence[int],
) -> None:
    """Ensure pilot and evaluation seed sets have zero overlap."""
    overlap = set(pilot_seeds) & set(test_seeds)
    if overlap:
        raise ValueError(
            f"Pilot and test seeds must be strictly disjoint; overlap: {sorted(overlap)}"
        )


def build_synthetic_bottleneck_scenario(
    seed: int,
) -> Tuple[
    Dict[str, VehicleRoute],
    Dict[str, VirtualCreditBudget],
    Dict[Edge, int],
]:
    """Build a reproducible 6-vehicle, 3-road synthetic bottleneck scenario."""
    rng = random.Random(seed)
    north_edge = ("N0", "C0")
    south_edge = ("S0", "C0")
    center_edge = ("C0", "C1")
    capacities: Dict[Edge, int] = {
        north_edge: 1,
        south_edge: 1,
        center_edge: 2,
    }
    routes: Dict[str, VehicleRoute] = {}
    budgets: Dict[str, VirtualCreditBudget] = {}

    for idx in range(6):
        vid = f"V{idx + 1}"
        t0 = round(rng.uniform(0.0, 45.0), 1)
        leg1_dur = round(rng.uniform(8.0, 16.0), 1)
        leg2_dur = round(rng.uniform(10.0, 20.0), 1)
        entry_edge = north_edge if idx % 2 == 0 else south_edge
        routes[vid] = VehicleRoute(
            vehicle_id=vid,
            nodes=[entry_edge[0], entry_edge[1], center_edge[1]],
            start_time=t0,
            travel_times={
                entry_edge: leg1_dur,
                center_edge: leg2_dur,
            },
            priority_weight=round(rng.uniform(1.0, 5.0), 2),
            detour_extra_time=round(rng.uniform(12.0, 30.0), 1),
        )
        budgets[vid] = VirtualCreditBudget(vid)

    return routes, budgets, capacities


def _compute_total_delay_s(
    initial_routes: Dict[str, VehicleRoute],
    resolved_routes: Dict[str, VehicleRoute],
) -> float:
    """Compute total arrival delay across all vehicles in seconds."""
    total_delay = 0.0
    for vid, initial_route in initial_routes.items():
        initial_finish = initial_route.start_time + sum(
            initial_route.travel_times.values()
        )
        resolved_route = resolved_routes[vid]
        resolved_finish = resolved_route.start_time + sum(
            resolved_route.travel_times.values()
        )
        total_delay += max(0.0, resolved_finish - initial_finish)
    return total_delay


def evaluate_tau_delta_t_cell(
    tau: float,
    delta_t: float,
    seeds: Sequence[int],
    cooldown_s: float = 60.0,
    scenario_builder: Callable[
        [int],
        Tuple[
            Dict[str, VehicleRoute],
            Dict[str, VirtualCreditBudget],
            Dict[Edge, int],
        ],
    ] = build_synthetic_bottleneck_scenario,
) -> SweepCellResult:
    """Evaluate a single (tau, delta_t) pair across the provided scenario seeds.

    Triggering and sequential resolution use the cell's ``delta_t`` value, while
    post-resolution unresolved capacity overlaps are counted using a fixed
    ``delta_t=0.0`` definition so counts are strictly comparable across cells.
    All auctions within a seed occur at a single planning timestamp ``t_now``,
    meaning a positive ``cooldown_s`` in ``NegotiationRateLimiter`` blocks repeat
    auctions for the same ``(edge, frozenset(vehicles))`` group during that pass.
    """
    if not seeds:
        raise ValueError("seeds must be non-empty")

    total_messages = 0
    total_unresolved = 0
    total_delay_s = 0.0
    total_resolutions = 0
    t_now = 0.0

    for seed in seeds:
        routes, budgets, capacities = scenario_builder(seed)
        trigger = ThresholdTrigger(
            tau=tau,
            delta_t=delta_t,
            edge_capacities=capacities,
        )
        counter = MessageCounter()
        rate_limiter = NegotiationRateLimiter(cooldown_s=cooldown_s)
        resolved_routes, outcomes = resolve_conflicts_sequentially(
            routes=routes,
            trigger_policy=trigger,
            budgets=budgets,
            t_now=t_now,
            edge_capacities=capacities,
            delta_t=delta_t,
            msg_counter=counter,
            rate_limiter=rate_limiter,
        )
        total_resolutions += len(outcomes)

        # Fixed delta_t=0.0 definition so unresolved counts are comparable across cells.
        unresolved = detect_conflicts(
            prepare_occupancies(resolved_routes.values()),
            edge_capacities=capacities,
            delta_t=0.0,
        )

        total_messages += counter.total_negotiation_messages
        total_unresolved += len(unresolved)
        total_delay_s += _compute_total_delay_s(routes, resolved_routes)

    n = len(seeds)
    return SweepCellResult(
        tau=float(tau),
        delta_t=float(delta_t),
        num_seeds=n,
        mean_messages=round(total_messages / n, 4),
        mean_unresolved_conflicts=round(total_unresolved / n, 4),
        mean_total_delay_s=round(total_delay_s / n, 4),
        mean_resolutions=round(total_resolutions / n, 4),
    )


def run_tau_delta_t_sweep(
    tau_grid: Iterable[float] = DEFAULT_TAU_GRID,
    delta_t_grid: Iterable[float] = DEFAULT_DELTA_T_GRID,
    seeds: Sequence[int] = DEFAULT_PILOT_SEEDS,
    cooldown_s: float = 60.0,
) -> List[SweepCellResult]:
    """Run a full grid sweep over (tau, delta_t) pairs."""
    results: List[SweepCellResult] = []
    for tau in tau_grid:
        for delta_t in delta_t_grid:
            results.append(
                evaluate_tau_delta_t_cell(
                    tau=tau,
                    delta_t=delta_t,
                    seeds=seeds,
                    cooldown_s=cooldown_s,
                )
            )
    return results


def select_operating_point(
    pilot_results: Sequence[SweepCellResult],
    target_unresolved: float = 0.0,
) -> Tuple[SweepCellResult, bool]:
    """Select the (tau, delta_t) cell minimizing unresolved conflicts then messages.

    Returns ``(selected_cell, target_met)`` where ``target_met`` is True iff
    ``selected_cell.mean_unresolved_conflicts <= target_unresolved``.
    """
    if not pilot_results:
        raise ValueError("pilot_results must be non-empty")

    best = min(
        pilot_results,
        key=lambda r: (
            max(0.0, r.mean_unresolved_conflicts - target_unresolved),
            r.mean_unresolved_conflicts,
            r.mean_messages,
            r.tau,
            r.delta_t,
        ),
    )
    target_met = best.mean_unresolved_conflicts <= target_unresolved
    return best, target_met
