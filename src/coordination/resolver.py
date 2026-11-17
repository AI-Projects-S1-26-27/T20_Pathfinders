"""Sequential Conflict Resolver, Rate Limiter & Route Conversion Helpers."""

from dataclasses import dataclass, replace
import math
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from src.coordination.conflict import (
    Conflict,
    Edge,
    VehicleRoute,
    detect_conflicts,
    prepare_occupancies,
)
from src.coordination.messages import MessageCounter
from src.coordination.negotiation import (
    VirtualCreditBudget,
    run_vickrey_auction,
)
from src.coordination.trigger import TriggerPolicy

D5_CLASS_CAPACITIES: Dict[str, int] = {
    "residential": 1,
    "secondary": 2,
    "primary": 3,
}

RateLimitKey = Tuple[Edge, FrozenSet[str]]


@dataclass
class ResolutionOutcome:
    edge: Edge
    vehicles: List[str]
    winner_id: Optional[str]
    loser_ids: List[str]
    clearing_price: float
    action_taken: str
    overlap_start: float = 0.0
    overlap_end: float = 0.0
    ttc: float = 0.0
    hold_until: Optional[float] = None


class NegotiationRateLimiter:
    """
    Per-(edge, participant-set) cooldown limiter (default 60.0 s) so repeated
    replans for the same contested edge and vehicle group do not spam auctions.
    """

    def __init__(self, cooldown_s: float = 60.0):
        if cooldown_s < 0:
            raise ValueError("cooldown_s cannot be negative")
        self.cooldown_s = float(cooldown_s)
        self._last_negotiated: Dict[RateLimitKey, float] = {}

    @staticmethod
    def _make_key(edge: Edge, vehicles: Sequence[str]) -> RateLimitKey:
        return (edge, frozenset(vehicles))

    def can_negotiate(
        self, edge: Edge, t_now: float, vehicles: Sequence[str] = ()
    ) -> bool:
        key = self._make_key(edge, vehicles)
        last_t = self._last_negotiated.get(key)
        if last_t is None:
            return True
        return (t_now - last_t) >= self.cooldown_s

    def record_negotiation(
        self, edge: Edge, t_now: float, vehicles: Sequence[str] = ()
    ) -> None:
        key = self._make_key(edge, vehicles)
        self._last_negotiated[key] = t_now

    def reset(self) -> None:
        self._last_negotiated.clear()


def build_edge_capacities_from_classes(
    edge_classes: Dict[Edge, str],
) -> Dict[Edge, int]:
    """Map road highway classes to D5 fleet-slot capacities (default 1)."""
    return {
        edge: D5_CLASS_CAPACITIES.get(road_class.lower(), 1)
        for edge, road_class in edge_classes.items()
    }


def route_etas_to_vehicle_route(
    vehicle_id: str,
    eta_steps: Sequence[Tuple[str, float, Optional[str]]],
    priority_weight: float = 1.0,
    detour_extra_time: float = 5.0,
    t_now: Optional[float] = None,
) -> VehicleRoute:
    """
    Convert Route steps [(node, planned_eta, order_id), ...] into VehicleRoute.
    When t_now is provided, trims steps completed at or before t_now so past
    segments already driven do not generate spurious historical conflicts.
    """
    steps = list(eta_steps)
    if t_now is not None and len(steps) >= 2:
        first_active_idx = 0
        for idx in range(len(steps) - 1):
            if float(steps[idx + 1][1]) <= t_now:
                first_active_idx = idx + 1
            else:
                break
        steps = steps[first_active_idx:]

    if not steps:
        return VehicleRoute(
            vehicle_id=vehicle_id,
            nodes=[],
            start_time=0.0 if t_now is None else float(t_now),
            travel_times={},
            priority_weight=priority_weight,
            detour_extra_time=detour_extra_time,
        )

    nodes = [step[0] for step in steps]
    start_time = float(steps[0][1])
    travel_times: Dict[Edge, float] = {}

    for (u, t_u, _), (v, t_v, _) in zip(steps[:-1], steps[1:]):
        duration = max(1e-6, float(t_v) - float(t_u))
        travel_times[(u, v)] = duration

    return VehicleRoute(
        vehicle_id=vehicle_id,
        nodes=nodes,
        start_time=start_time,
        travel_times=travel_times,
        priority_weight=priority_weight,
        detour_extra_time=detour_extra_time,
    )


def compute_real_detour_extra_time(
    route: VehicleRoute,
    contested_edge: Edge,
    conflict_end_time: float,
    alt_paths_fn: Optional[
        Callable[[str, str, Edge], List[Tuple[List[str], float]]]
    ] = None,
) -> float:
    """
    Compute extra delay if yielding on contested_edge:
    - Uses route.detour_extra_time when supplied by D* Lite, bounded by waiting
      until conflict_end_time when an active wait is shorter.
    - Only queries alt_paths_fn on single-leg routes (len(nodes) == 2) so
      multi-stop routes never skip intermediate delivery stops.
    """
    if len(route.nodes) < 2:
        return route.detour_extra_time

    arrival_at_edge = _arrival_time_at_edge(route, contested_edge)
    wait_delay = max(0.0, conflict_end_time - arrival_at_edge)

    if alt_paths_fn is None or len(route.nodes) > 2:
        if route.detour_extra_time > 0.0 and wait_delay > 0.0:
            return min(wait_delay, route.detour_extra_time)
        return wait_delay if wait_delay > 0.0 else route.detour_extra_time

    origin, dest = route.nodes[0], route.nodes[-1]
    direct_time = sum(
        route.travel_times.get((u, v), 0.0)
        for u, v in zip(route.nodes[:-1], route.nodes[1:])
    )
    alt_candidates = alt_paths_fn(origin, dest, contested_edge)
    if not alt_candidates:
        return wait_delay if wait_delay > 0.0 else route.detour_extra_time

    best_alt_time = min(cost for _, cost in alt_candidates)
    detour_delay = max(0.0, best_alt_time - direct_time)
    return min(wait_delay, detour_delay) if wait_delay > 0.0 else detour_delay


def _arrival_time_at_edge(route: VehicleRoute, edge: Edge) -> float:
    """Return the planned entry time for an edge in a route."""
    arrival_time = route.start_time
    for route_edge in zip(route.nodes[:-1], route.nodes[1:]):
        if route_edge == edge:
            return arrival_time
        arrival_time += route.travel_times.get(route_edge, 0.0)
    raise ValueError(
        f"Route {route.vehicle_id} does not traverse contested edge {edge}"
    )


def _apply_yield_delay(route: VehicleRoute, delay_s: float) -> VehicleRoute:
    """Shift vehicle's start_time forward by delay_s in the planned
    schedule."""
    return replace(route, start_time=route.start_time + delay_s)


def resolve_conflicts_sequentially(
    routes: Dict[str, VehicleRoute],
    trigger_policy: TriggerPolicy,
    budgets: Dict[str, VirtualCreditBudget],
    t_now: float,
    edge_capacities: Optional[Dict[Edge, int]] = None,
    delta_t: float = 0.0,
    msg_counter: Optional[MessageCounter] = None,
    alt_paths_fn: Optional[
        Callable[[str, str, Edge], List[Tuple[List[str], float]]]
    ] = None,
    rate_limiter: Optional[NegotiationRateLimiter] = None,
    max_rounds: int = 20,
    focus_vehicle_id: Optional[str] = None,
    detour_cost_fn: Optional[Callable[[str, Edge, float], float]] = None,
    initial_credits: float = 100.0,
    fallback_on_unpaid: bool = True,
    on_rate_limited: Optional[Callable[[Conflict], None]] = None,
    on_not_triggered: Optional[Callable[[Conflict], None]] = None,
) -> Tuple[Dict[str, VehicleRoute], List[ResolutionOutcome]]:
    """
    Multi-Agent Sequential Conflict Resolution:
    1. Detect conflicts across current planned routes.
    2. Filter conflicts (optionally scoped to focus_vehicle_id) where
       (edge, vehicles) cooldown allows and trigger_policy fires.
    3. Sort triggered conflicts by earliest TTC.
    4. Run a Vickrey auction on the earliest conflict using real detour costs.
    5. Losers yield until conflict.overlap_end (recorded in outcome.hold_until
       for simulator hold(vehicle_id, until_t) / yield_road execution).

    Notes & Simplifications:
    - Raises RuntimeError if triggered conflicts remain after max_rounds.
    - When a NegotiationRateLimiter is supplied, suppressed repeat auctions on
      the same (edge, vehicles) group mean the returned schedule is not
      guaranteed to be conflict-free.
    - Yielding shifts a losing vehicle's entire route start_time forward
      rather than inserting a localized wait at the contested edge.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")

    updated_routes = dict(routes)
    outcomes: List[ResolutionOutcome] = []
    resolved_intervals: set = set()
    seen_skipped: set = set()

    for _ in range(max_rounds):
        occs = prepare_occupancies(updated_routes.values())
        conflicts = detect_conflicts(
            occs, edge_capacities=edge_capacities, delta_t=delta_t
        )

        triggered: List[Conflict] = []
        for c in conflicts:
            if (
                focus_vehicle_id is not None
                and focus_vehicle_id not in c.vehicles
            ):
                continue
            key = (
                c.edge,
                frozenset(c.vehicles),
                round(c.overlap_start, 6),
                round(c.overlap_end, 6),
            )
            if key in resolved_intervals:
                continue
            if rate_limiter is not None and not rate_limiter.can_negotiate(
                c.edge, t_now, vehicles=sorted(c.vehicles)
            ):
                if key not in seen_skipped and on_rate_limited is not None:
                    seen_skipped.add(key)
                    on_rate_limited(c)
                continue

            if (
                focus_vehicle_id is not None
                and focus_vehicle_id in updated_routes
            ):
                should_fire = trigger_policy.should_negotiate(
                    updated_routes[focus_vehicle_id],
                    updated_routes,
                    t_now,
                    conflict=c,
                )
            else:
                should_fire = any(
                    v_id in updated_routes
                    and trigger_policy.should_negotiate(
                        updated_routes[v_id], updated_routes, t_now, conflict=c
                    )
                    for v_id in c.vehicles
                )

            if not should_fire:
                if key not in seen_skipped and on_not_triggered is not None:
                    seen_skipped.add(key)
                    on_not_triggered(c)
                continue

            triggered.append(c)

        if not triggered:
            break

        triggered.sort(key=lambda c: (c.time_to_conflict(t_now), c.edge))
        target = triggered[0]
        resolved_intervals.add(
            (
                target.edge,
                frozenset(target.vehicles),
                round(target.overlap_start, 6),
                round(target.overlap_end, 6),
            )
        )

        if rate_limiter is not None:
            rate_limiter.record_negotiation(
                target.edge, t_now, vehicles=sorted(target.vehicles)
            )

        wait_s = max(target.duration, 0.0)
        contestants: List[VehicleRoute] = []
        for v_id in sorted(target.vehicles):
            if v_id not in updated_routes:
                continue
            budgets.setdefault(
                v_id, VirtualCreditBudget(v_id, initial_credits)
            )
            base_r = updated_routes[v_id]
            if detour_cost_fn is not None:
                planner_detour = detour_cost_fn(v_id, target.edge, wait_s)
                real_detour = (
                    min(planner_detour, wait_s)
                    if math.isfinite(planner_detour)
                    else wait_s
                )
            else:
                real_detour = compute_real_detour_extra_time(
                    base_r,
                    target.edge,
                    target.overlap_end,
                    alt_paths_fn=alt_paths_fn,
                )
            contestants.append(replace(base_r, detour_extra_time=real_detour))

        winner_id, loser_ids, price = run_vickrey_auction(
            contestants, budgets, msg_counter=msg_counter
        )

        hold_until: Optional[float] = None
        if winner_id is not None:
            for loser_id in loser_ids:
                loser_route = updated_routes[loser_id]
                loser_arrival = _arrival_time_at_edge(loser_route, target.edge)
                yield_wait = max(
                    0.0, target.overlap_end - loser_arrival
                )
                updated_routes[loser_id] = _apply_yield_delay(
                    loser_route, yield_wait
                )
            hold_until = target.overlap_end if loser_ids else None
            action = "winner_passes_losers_yield"
        else:
            if fallback_on_unpaid:
                sorted_ids = sorted(
                    v_id for v_id in target.vehicles if v_id in updated_routes
                )
                if len(sorted_ids) > 1:
                    fallback_winner = sorted_ids[0]
                    loser_ids = sorted_ids[1:]
                    for loser_id in loser_ids:
                        loser_route = updated_routes[loser_id]
                        loser_arrival = _arrival_time_at_edge(
                            loser_route, target.edge
                        )
                        yield_wait = max(
                            0.0, target.overlap_end - loser_arrival
                        )
                        updated_routes[loser_id] = _apply_yield_delay(
                            loser_route, yield_wait
                        )
                    winner_id = fallback_winner
                    hold_until = target.overlap_end
            else:
                loser_ids = []
            action = "nobody_can_pay_fallback_lowest_id"

        outcomes.append(
            ResolutionOutcome(
                edge=target.edge,
                vehicles=sorted(target.vehicles),
                winner_id=winner_id,
                loser_ids=loser_ids,
                clearing_price=price,
                action_taken=action,
                overlap_start=target.overlap_start,
                overlap_end=target.overlap_end,
                ttc=target.time_to_conflict(t_now),
                hold_until=hold_until,
            )
        )

    remaining_occupancies = prepare_occupancies(updated_routes.values())
    remaining_conflicts = detect_conflicts(
        remaining_occupancies,
        edge_capacities=edge_capacities,
        delta_t=delta_t,
    )
    unresolved = [
        conflict
        for conflict in remaining_conflicts
        if (
            focus_vehicle_id is None
            or focus_vehicle_id in conflict.vehicles
        )
        and (
            rate_limiter is None
            or rate_limiter.can_negotiate(
                conflict.edge, t_now, vehicles=sorted(conflict.vehicles)
            )
        )
        and (
            trigger_policy.should_negotiate(
                updated_routes[focus_vehicle_id],
                updated_routes,
                t_now,
                conflict=conflict,
            )
            if (
                focus_vehicle_id is not None
                and focus_vehicle_id in updated_routes
            )
            else any(
                v_id in updated_routes
                and trigger_policy.should_negotiate(
                    updated_routes[v_id],
                    updated_routes,
                    t_now,
                    conflict=conflict,
                )
                for v_id in conflict.vehicles
            )
        )
    ]
    if unresolved:
        raise RuntimeError(
            f"Conflict resolution stopped with {len(unresolved)} triggered "
            f"conflict(s) after {len(outcomes)} round(s)"
        )

    return updated_routes, outcomes
