"""Coordination Module: Conflict Detection, Triggers, Auctions, and Sequential Resolution."""

from src.coordination.conflict import (
    Conflict,
    RoadOccupancy,
    VehicleRoute,
    detect_conflicts,
    prepare_occupancies,
    verify_conflicts_brute_force,
)
from src.coordination.messages import CoordinationMessage, MessageCounter
from src.coordination.negotiation import (
    VirtualCreditBudget,
    calculate_delay_cost,
    run_vickrey_auction,
)
from src.coordination.resolver import (
    D5_CLASS_CAPACITIES,
    LocalReplanCoordinator,
    NegotiationRateLimiter,
    ResolutionOutcome,
    build_edge_capacities_from_classes,
    compute_real_detour_extra_time,
    coordination_hook,
    on_local_replan_coordination,
    resolve_conflicts_sequentially,
    route_etas_to_vehicle_route,
)
from src.coordination.trigger import (
    AlwaysTrigger,
    ConflictTriggerEvent,
    NeverTrigger,
    ThresholdTrigger,
    TriggerPolicy,
    filter_and_trigger_conflicts,
)

__all__ = [
    "VehicleRoute",
    "RoadOccupancy",
    "Conflict",
    "ConflictTriggerEvent",
    "CoordinationMessage",
    "MessageCounter",
    "VirtualCreditBudget",
    "TriggerPolicy",
    "NeverTrigger",
    "AlwaysTrigger",
    "ThresholdTrigger",
    "D5_CLASS_CAPACITIES",
    "LocalReplanCoordinator",
    "NegotiationRateLimiter",
    "ResolutionOutcome",
    "prepare_occupancies",
    "detect_conflicts",
    "verify_conflicts_brute_force",
    "calculate_delay_cost",
    "run_vickrey_auction",
    "filter_and_trigger_conflicts",
    "build_edge_capacities_from_classes",
    "route_etas_to_vehicle_route",
    "compute_real_detour_extra_time",
    "resolve_conflicts_sequentially",
    "on_local_replan_coordination",
    "coordination_hook",
]
