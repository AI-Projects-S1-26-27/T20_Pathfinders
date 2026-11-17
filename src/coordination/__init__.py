"""Multi-Agent Coordination Package (Conflict Detection, Trigger, Auction
& Resolver).
"""

from src.coordination.conflict import (
    Conflict,
    Edge,
    RoadOccupancy,
    VehicleRoute,
    detect_conflicts,
    prepare_occupancies,
)
from src.coordination.messages import MessageCounter
from src.coordination.negotiation import (
    VirtualCreditBudget,
    calculate_delay_cost,
    run_vickrey_auction,
)
from src.coordination.resolver import (
    D5_CLASS_CAPACITIES,
    NegotiationRateLimiter,
    ResolutionOutcome,
    build_edge_capacities_from_classes,
    compute_real_detour_extra_time,
    resolve_conflicts_sequentially,
    route_etas_to_vehicle_route,
)
from src.coordination.trigger import (
    AlwaysTrigger,
    NeverTrigger,
    ThresholdTrigger,
    TriggerPolicy,
)

__all__ = [
    "AlwaysTrigger",
    "Conflict",
    "D5_CLASS_CAPACITIES",
    "Edge",
    "MessageCounter",
    "NegotiationRateLimiter",
    "NeverTrigger",
    "ResolutionOutcome",
    "RoadOccupancy",
    "ThresholdTrigger",
    "TriggerPolicy",
    "VehicleRoute",
    "VirtualCreditBudget",
    "build_edge_capacities_from_classes",
    "calculate_delay_cost",
    "compute_real_detour_extra_time",
    "detect_conflicts",
    "prepare_occupancies",
    "resolve_conflicts_sequentially",
    "route_etas_to_vehicle_route",
    "run_vickrey_auction",
]
