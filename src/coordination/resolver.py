"""Sequential Conflict Resolver, Rate Limiter & Route Integration Adapter."""

from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from src.coordination.conflict import (
    Conflict,
    Edge,
    VehicleRoute,
    detect_conflicts,
    prepare_occupancies,
)
from src.coordination.messages import MessageCounter
from src.coordination.negotiation import VirtualCreditBudget, run_vickrey_auction
from src.coordination.trigger import NeverTrigger, TriggerPolicy

D5_CLASS_CAPACITIES: Dict[str, int] = {
    "residential": 1,
    "secondary": 2,
    "primary": 3,
}


@dataclass
class ResolutionOutcome:
    edge: Edge
    winner_id: Optional[str]
    loser_ids: List[str]
    clearing_price: float
    action_taken: str


class NegotiationRateLimiter:
    """
    Re-Negotiation Rate Limiter:
    Prevents negotiation spam when vehicles replan repeatedly on the same
    contested edge within a cooldown window (cooldown_s).
    """

    def __init__(self, cooldown_s: float = 15.0):
        if cooldown_s < 0:
            raise ValueError("cooldown_s cannot be negative")
        self.cooldown_s = cooldown_s
        self._last_negotiated: Dict[Edge, float] = {}

    def can_negotiate(self, edge: Edge, t_now: float) -> bool:
        last_t = self._last_negotiated.get(edge)
        if last_t is None:
            return True
        return (t_now - last_t) >= self.cooldown_s

    def record_negotiation(self, edge: Edge, t_now: float) -> None:
        self._last_negotiated[edge] = t_now

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
) -> VehicleRoute:
    """
    Glue adapter converting R1/R2 Route tuples [(node, planned_eta, order_id), ...]
    into R5's VehicleRoute dataclass.
    """
    if not eta_steps:
        return VehicleRoute(
            vehicle_id=vehicle_id,
            nodes=[],
            start_time=0.0,
            travel_times={},
            priority_weight=priority_weight,
            detour_extra_time=detour_extra_time,
        )

    nodes = [step[0] for step in eta_steps]
    start_time = float(eta_steps[0][1])
    travel_times: Dict[Edge, float] = {}

    for (u, t_u, _), (v, t_v, _) in zip(eta_steps[:-1], eta_steps[1:]):
        duration = max(1e-3, float(t_v) - float(t_u))
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
    alt_paths_fn: Optional[Callable[[str, str, Edge], List[Tuple[List[str], float]]]] = None,
) -> float:
    """
    Computes extra delay if yielding on contested_edge:
    minimum of (waiting until conflict_end_time) vs (taking R2's fastest alternative path).
    Falls back to route.detour_extra_time when R2's path helper is not attached.
    """
    if len(route.nodes) < 2:
        return route.detour_extra_time

    arrival_at_edge = _arrival_time_at_edge(route, contested_edge)
    wait_delay = max(0.0, conflict_end_time - arrival_at_edge)

    if alt_paths_fn is None:
        return wait_delay if wait_delay > 0.0 else route.detour_extra_time

    origin, dest = route.nodes[0], route.nodes[-1]
    direct_time = sum(
        route.travel_times.get((u, v), 0.0)
        for u, v in zip(route.nodes[:-1], route.nodes[1:])
    )
    alt_candidates = alt_paths_fn(origin, dest, contested_edge)
    if not alt_candidates:
        return wait_delay

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
    raise ValueError(f"Route {route.vehicle_id} does not traverse contested edge {edge}")


def _apply_yield_delay(route: VehicleRoute, delay_s: float) -> VehicleRoute:
    """Shifts vehicle's start_time forward by delay_s so it yields the contested slot."""
    return replace(route, start_time=route.start_time + delay_s)


def resolve_conflicts_sequentially(
    routes: Dict[str, VehicleRoute],
    trigger_policy: TriggerPolicy,
    budgets: Dict[str, VirtualCreditBudget],
    t_now: float,
    edge_capacities: Optional[Dict[Edge, int]] = None,
    delta_t: float = 0.0,
    msg_counter: Optional[MessageCounter] = None,
    alt_paths_fn: Optional[Callable[[str, str, Edge], List[Tuple[List[str], float]]]] = None,
    rate_limiter: Optional[NegotiationRateLimiter] = None,
    max_rounds: int = 20,
) -> Tuple[Dict[str, VehicleRoute], List[ResolutionOutcome]]:
    """
    Multi-Agent Sequential Conflict Resolution with rate-limiting:
    1. Detect conflicts across current planned routes.
    2. Filter conflicts where at least one vehicle triggers negotiation and cooldown allows.
    3. Sort triggered conflicts by earliest TTC.
    4. Update real detour costs and run a Vickrey auction on the earliest conflict.
    5. Losers yield via start-time backoff; repeat until no triggered conflicts remain.
    """
    updated_routes = dict(routes)
    outcomes: List[ResolutionOutcome] = []
    resolved_intervals: set = set()
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")

    for _ in range(max_rounds):
        occs = prepare_occupancies(updated_routes.values())
        conflicts = detect_conflicts(occs, edge_capacities=edge_capacities, delta_t=delta_t)

        triggered: List[Conflict] = []
        for c in conflicts:
            key = (
                c.edge,
                frozenset(c.vehicles),
                round(c.overlap_start, 6),
                round(c.overlap_end, 6),
            )
            if key in resolved_intervals:
                continue
            if rate_limiter is not None and not rate_limiter.can_negotiate(c.edge, t_now):
                continue
            if any(
                v_id in updated_routes
                and trigger_policy.should_negotiate(
                    updated_routes[v_id], updated_routes, t_now, conflict=c
                )
                for v_id in c.vehicles
            ):
                triggered.append(c)

        if not triggered:
            break

        triggered.sort(key=lambda c: (c.time_to_conflict(t_now), c.edge))
        target = triggered[0]
        resolved_intervals.add((
            target.edge,
            frozenset(target.vehicles),
            round(target.overlap_start, 6),
            round(target.overlap_end, 6),
        ))

        if rate_limiter is not None:
            rate_limiter.record_negotiation(target.edge, t_now)

        contestants: List[VehicleRoute] = []
        for v_id in sorted(target.vehicles):
            if v_id in updated_routes:
                base_r = updated_routes[v_id]
                real_detour = compute_real_detour_extra_time(
                    base_r, target.edge, target.overlap_end, alt_paths_fn=alt_paths_fn
                )
                contestants.append(replace(base_r, detour_extra_time=real_detour))

        winner_id, loser_ids, price = run_vickrey_auction(
            contestants, budgets, msg_counter=msg_counter
        )

        if winner_id is not None:
            for loser_id in loser_ids:
                loser_route = updated_routes[loser_id]
                loser_arrival = _arrival_time_at_edge(loser_route, target.edge)
                yield_wait = max(0.0, target.overlap_end - loser_arrival)
                updated_routes[loser_id] = _apply_yield_delay(loser_route, yield_wait)
            action = "winner_passes_losers_yield"
        else:
            sorted_ids = sorted(v_id for v_id in target.vehicles if v_id in updated_routes)
            if len(sorted_ids) > 1:
                fallback_winner = sorted_ids[0]
                loser_ids = sorted_ids[1:]
                for loser_id in loser_ids:
                    loser_route = updated_routes[loser_id]
                    loser_arrival = _arrival_time_at_edge(loser_route, target.edge)
                    yield_wait = max(0.0, target.overlap_end - loser_arrival)
                    updated_routes[loser_id] = _apply_yield_delay(loser_route, yield_wait)
                winner_id = fallback_winner
            action = "nobody_can_pay_fallback_lowest_id"

        outcomes.append(ResolutionOutcome(
            edge=target.edge,
            winner_id=winner_id,
            loser_ids=loser_ids,
            clearing_price=price,
            action_taken=action,
        ))

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
            rate_limiter is None
            or rate_limiter.can_negotiate(conflict.edge, t_now)
        )
        and any(
            v_id in updated_routes
            and trigger_policy.should_negotiate(
                updated_routes[v_id], updated_routes, t_now, conflict=conflict
            )
            for v_id in conflict.vehicles
        )
    ]
    if unresolved:
        raise RuntimeError(
            f"Conflict resolution stopped with {len(unresolved)} triggered "
            f"conflict(s) after {len(outcomes)} round(s)"
        )

    return updated_routes, outcomes


def on_local_replan_coordination(
    vehicle_id: str,
    route_etas: Sequence[Tuple[str, float, Optional[str]]],
    all_routes_etas: Dict[str, Sequence[Tuple[str, float, Optional[str]]]],
    t_now: float,
    trigger_policy: TriggerPolicy,
    budgets: Dict[str, VirtualCreditBudget],
    edge_capacities: Optional[Dict[Edge, int]] = None,
    delta_t: float = 0.0,
    msg_counter: Optional[MessageCounter] = None,
    rate_limiter: Optional[NegotiationRateLimiter] = None,
    trigger_enabled: bool = True,
    priority_weights: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, VehicleRoute], List[ResolutionOutcome]]:
    """
    
    - When trigger_enabled=False (independent mode), uses NeverTrigger and logs 0
      negotiation messages.
    - When trigger_enabled=True (full mode), checks trigger_policy with rate-limiting
      to prevent re-negotiation spam after consecutive replans.
    """
    effective_policy: TriggerPolicy = trigger_policy if trigger_enabled else NeverTrigger()
    weights = priority_weights or {}

    converted_routes: Dict[str, VehicleRoute] = {
        v_id: route_etas_to_vehicle_route(
            v_id, steps, priority_weight=weights.get(v_id, 1.0)
        )
        for v_id, steps in all_routes_etas.items()
    }
    if vehicle_id not in converted_routes:
        converted_routes[vehicle_id] = route_etas_to_vehicle_route(
            vehicle_id, route_etas, priority_weight=weights.get(vehicle_id, 1.0)
        )

    return resolve_conflicts_sequentially(
        routes=converted_routes,
        trigger_policy=effective_policy,
        budgets=budgets,
        t_now=t_now,
        edge_capacities=edge_capacities,
        delta_t=delta_t,
        msg_counter=msg_counter,
        rate_limiter=rate_limiter,
    )


class LocalReplanCoordinator:
    """Persistent coordination session for use after each local route replan."""

    def __init__(
        self,
        trigger_policy: TriggerPolicy,
        budgets: Dict[str, VirtualCreditBudget],
        edge_capacities: Optional[Dict[Edge, int]] = None,
        delta_t: float = 0.0,
        cooldown_s: float = 15.0,
        trigger_enabled: bool = True,
    ) -> None:
        self.trigger_policy = trigger_policy
        self.budgets = budgets
        self.edge_capacities = edge_capacities
        self.delta_t = delta_t
        self.trigger_enabled = trigger_enabled
        self.rate_limiter = NegotiationRateLimiter(cooldown_s)

    def after_replan(
        self,
        vehicle_id: str,
        route_etas: Sequence[Tuple[str, float, Optional[str]]],
        all_routes_etas: Dict[str, Sequence[Tuple[str, float, Optional[str]]]],
        t_now: float,
        msg_counter: Optional[MessageCounter] = None,
        priority_weights: Optional[Dict[str, float]] = None,
    ) -> Tuple[Dict[str, VehicleRoute], List[ResolutionOutcome]]:
        """Coordinate the current routes while retaining edge cooldown state."""
        return on_local_replan_coordination(
            vehicle_id=vehicle_id,
            route_etas=route_etas,
            all_routes_etas=all_routes_etas,
            t_now=t_now,
            trigger_policy=self.trigger_policy,
            budgets=self.budgets,
            edge_capacities=self.edge_capacities,
            delta_t=self.delta_t,
            msg_counter=msg_counter,
            rate_limiter=self.rate_limiter,
            trigger_enabled=self.trigger_enabled,
            priority_weights=priority_weights,
        )


def coordination_hook(
    vehicle_id: str,
    route_etas: Sequence[Tuple[str, float, Optional[str]]],
    all_routes_etas: Dict[str, Sequence[Tuple[str, float, Optional[str]]]],
    t_now: float,
    trigger_policy: TriggerPolicy,
    budgets: Dict[str, VirtualCreditBudget],
    edge_capacities: Optional[Dict[Edge, int]] = None,
    delta_t: float = 0.0,
    rate_limiter: Optional[NegotiationRateLimiter] = None,
    trigger_enabled: bool = True,
    priority_weights: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, VehicleRoute], int]:
    """
    Backward-compatible R7/R1 Integration Hook returning
    (updated_routes, negotiation_messages_sent).
    """
    counter = MessageCounter()
    updated_routes, _ = on_local_replan_coordination(
        vehicle_id=vehicle_id,
        route_etas=route_etas,
        all_routes_etas=all_routes_etas,
        t_now=t_now,
        trigger_policy=trigger_policy,
        budgets=budgets,
        edge_capacities=edge_capacities,
        delta_t=delta_t,
        msg_counter=counter,
        rate_limiter=rate_limiter,
        trigger_enabled=trigger_enabled,
        priority_weights=priority_weights,
    )
    return updated_routes, counter.total_negotiation_messages