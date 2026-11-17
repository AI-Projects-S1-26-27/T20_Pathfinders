"""R5 Week 6-7 Pilot Tuning & (tau, delta_t) Sensitivity Sweep."""

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

from src.coordination.conflict import (
    Edge,
    VehicleRoute,
    detect_conflicts,
    prepare_occupancies,
)
from src.coordination.messages import MessageCounter
from src.coordination.negotiation import VirtualCreditBudget
from src.coordination.resolver import (
    NegotiationRateLimiter,
    resolve_conflicts_sequentially,
)
from src.coordination.trigger import ThresholdTrigger

TAU_GRID_DEFAULT: Tuple[float, ...] = (30.0, 60.0, 120.0, 300.0)
DELTA_T_GRID_DEFAULT: Tuple[float, ...] = (10.0, 20.0, 40.0)


@dataclass(frozen=True)
class SweepPointResult:
    """Aggregated coordination metrics for a single (tau, delta_t) cell."""

    tau: float
    delta_t: float
    runs: int
    mean_messages: float
    mean_unresolved_conflicts: float
    mean_total_delay_s: float
    mean_resolutions: float


ScenarioBuilder = Callable[
    [int],
    Tuple[
        Dict[str, VehicleRoute],
        Dict[str, VirtualCreditBudget],
        Dict[Edge, int],
    ],
]


def evaluate_tau_delta_t_cell(
    tau: float,
    delta_t: float,
    seeds: Sequence[int],
    scenario_builder: ScenarioBuilder,
    t_now: float = 0.0,
    cooldown_s: float = 60.0,
) -> SweepPointResult:
    """Evaluates one (tau, delta_t) pair across a fixed list of seeds."""
    if not seeds:
        raise ValueError("seeds must be a non-empty sequence.")

    total_messages = 0
    total_unresolved = 0
    total_delay = 0.0
    total_resolutions = 0

    for seed in seeds:
        routes, budgets, edge_capacities = scenario_builder(seed)
        baseline_finish = {
            vid: r.start_time + sum(r.travel_times.values())
            for vid, r in routes.items()
        }

        policy = ThresholdTrigger(
            tau=tau,
            delta_t=delta_t,
            edge_capacities=edge_capacities,
        )
        counter = MessageCounter()
        limiter = NegotiationRateLimiter(cooldown_s=cooldown_s)

        updated, outcomes = resolve_conflicts_sequentially(
            routes=routes,
            trigger_policy=policy,
            budgets=budgets,
            t_now=t_now,
            edge_capacities=edge_capacities,
            delta_t=delta_t,
            msg_counter=counter,
            rate_limiter=limiter,
        )

        # Unresolved conflicts: actual capacity overlaps after coordination
        remaining = detect_conflicts(
            prepare_occupancies(updated.values()),
            edge_capacities=edge_capacities,
            delta_t=delta_t,
        )

        added_delay = sum(
            max(
                0.0,
                (r.start_time + sum(r.travel_times.values()))
                - baseline_finish[vid],
            )
            for vid, r in updated.items()
        )

        total_messages += counter.total_negotiation_messages
        total_unresolved += len(remaining)
        total_delay += added_delay
        total_resolutions += len(outcomes)

    n = len(seeds)
    return SweepPointResult(
        tau=tau,
        delta_t=delta_t,
        runs=n,
        mean_messages=total_messages / n,
        mean_unresolved_conflicts=total_unresolved / n,
        mean_total_delay_s=total_delay / n,
        mean_resolutions=total_resolutions / n,
    )


def run_tau_delta_t_sweep(
    pilot_or_test_seeds: Sequence[int],
    scenario_builder: ScenarioBuilder,
    taus: Iterable[float] = TAU_GRID_DEFAULT,
    delta_ts: Iterable[float] = DELTA_T_GRID_DEFAULT,
    t_now: float = 0.0,
    cooldown_s: float = 60.0,
) -> List[SweepPointResult]:
    """Runs the full (tau, delta_t) sensitivity grid across given seeds."""
    results: List[SweepPointResult] = []
    for tau in taus:
        for delta_t in delta_ts:
            results.append(
                evaluate_tau_delta_t_cell(
                    tau=tau,
                    delta_t=delta_t,
                    seeds=pilot_or_test_seeds,
                    scenario_builder=scenario_builder,
                    t_now=t_now,
                    cooldown_s=cooldown_s,
                )
            )
    return results


def select_operating_point(
    pilot_results: Sequence[SweepPointResult],
    max_unresolved_conflicts: float = 0.0,
    message_weight: float = 0.5,
    delay_weight: float = 1.0,
) -> SweepPointResult:
    """Picks the best (tau, delta_t) on pilot seeds before feature freeze."""
    if not pilot_results:
        raise ValueError("pilot_results cannot be empty.")

    feasible = [
        r
        for r in pilot_results
        if r.mean_unresolved_conflicts <= max_unresolved_conflicts
    ]
    candidates = feasible if feasible else list(pilot_results)

    def score(item: SweepPointResult) -> Tuple[float, float, float, float]:
        weighted_cost = (
            delay_weight * item.mean_total_delay_s
            + message_weight * item.mean_messages
        )
        return (
            item.mean_unresolved_conflicts,
            weighted_cost,
            item.tau,
            item.delta_t,
        )

    return min(candidates, key=score)


def verify_seed_disjointness(
    pilot_seeds: Iterable[int], test_seeds: Iterable[int]
) -> None:
    """Enforces Section 7.1 rule: pilot seeds must not overlap test seeds."""
    overlap = set(pilot_seeds) & set(test_seeds)
    if overlap:
        raise ValueError(
            f"Pilot and test seeds must be disjoint; overlap={sorted(overlap)}"
        )
