"""Unit Tests for Sequential Resolver, Rate Limiter & Route Helpers."""

import pytest

from src.coordination.conflict import (
    VehicleRoute,
    detect_conflicts,
    prepare_occupancies,
)
from src.coordination.messages import MessageCounter
from src.coordination.negotiation import VirtualCreditBudget
from src.coordination.resolver import (
    NegotiationRateLimiter,
    build_edge_capacities_from_classes,
    compute_real_detour_extra_time,
    resolve_conflicts_sequentially,
    route_etas_to_vehicle_route,
)
from src.coordination.trigger import ThresholdTrigger


def test_two_vehicle_auction_reduces_queue_delay():
    """Auction order reduces queue delay and records hold_until."""
    routes = {
        "V1": VehicleRoute(
            "V1", ["A", "B"], 0.0, {("A", "B"): 10.0}, priority_weight=1.0
        ),
        "V2": VehicleRoute(
            "V2", ["A", "B"], 0.0, {("A", "B"): 2.0}, priority_weight=4.0
        ),
    }
    caps = {("A", "B"): 1}
    budgets = {v_id: VirtualCreditBudget(v_id, 50.0) for v_id in routes}

    updated, outcomes = resolve_conflicts_sequentially(
        routes,
        ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets,
        t_now=0.0,
        edge_capacities=caps,
    )

    assert len(outcomes) == 1
    assert outcomes[0].winner_id == "V2"
    assert outcomes[0].loser_ids == ["V1"]
    assert outcomes[0].hold_until == 2.0
    assert updated["V1"].start_time == 2.0
    assert updated["V2"].start_time == 0.0
    assert (
        detect_conflicts(
            prepare_occupancies(updated.values()), edge_capacities=caps
        )
        == []
    )


def test_d5_road_class_capacities():
    """Verifies residential=1, secondary=2, primary=3 per Decision D5."""
    classes = {
        ("A", "B"): "residential",
        ("B", "C"): "secondary",
        ("C", "D"): "primary",
        ("D", "E"): "unknown_class",
    }
    caps = build_edge_capacities_from_classes(classes)
    assert caps == {
        ("A", "B"): 1,
        ("B", "C"): 2,
        ("C", "D"): 3,
        ("D", "E"): 1,
    }


def test_route_etas_adapter_trims_past_segments():
    """Verify conversion and trimming of segments driven before t_now."""
    etas = [
        ("Depot", 0.0, None),
        ("N1", 10.0, "ORD-1"),
        ("N2", 22.0, "ORD-2"),
        ("N3", 35.0, None),
    ]
    vr = route_etas_to_vehicle_route(
        "V1", etas, priority_weight=3.0, detour_extra_time=7.5, t_now=12.0
    )

    assert vr.vehicle_id == "V1"
    assert vr.nodes == ["N1", "N2", "N3"]
    assert vr.start_time == 10.0
    assert vr.travel_times == {("N1", "N2"): 12.0, ("N2", "N3"): 13.0}
    assert vr.priority_weight == 3.0
    assert vr.detour_extra_time == 7.5


def test_multi_stop_route_preserves_planner_detour_cost():
    """Multi-stop routes must not query shortcuts that skip stops."""
    route = VehicleRoute(
        "V1",
        ["Depot", "Stop1", "Stop2"],
        0.0,
        {("Depot", "Stop1"): 10.0, ("Stop1", "Stop2"): 10.0},
        priority_weight=2.0,
        detour_extra_time=14.0,
    )

    called = False

    def fake_alt_paths(origin, dest, blocked_edge):
        nonlocal called
        called = True
        return [(["Depot", "Stop2"], 12.0)]

    cost = compute_real_detour_extra_time(
        route,
        contested_edge=("Depot", "Stop1"),
        conflict_end_time=20.0,
        alt_paths_fn=fake_alt_paths,
    )

    assert called is False
    assert cost == 14.0


def test_sequential_resolution_three_vehicles():
    """Clear a three-vehicle bottleneck through sequential auctions."""
    routes = {
        "V1": VehicleRoute(
            "V1",
            ["A", "B"],
            start_time=0.0,
            travel_times={("A", "B"): 10.0},
            priority_weight=3.0,
        ),
        "V2": VehicleRoute(
            "V2",
            ["A", "B"],
            start_time=1.0,
            travel_times={("A", "B"): 10.0},
            priority_weight=2.0,
        ),
        "V3": VehicleRoute(
            "V3",
            ["A", "B"],
            start_time=2.0,
            travel_times={("A", "B"): 10.0},
            priority_weight=1.0,
        ),
    }
    budgets = {
        "V1": VirtualCreditBudget("V1", 50.0),
        "V2": VirtualCreditBudget("V2", 50.0),
        "V3": VirtualCreditBudget("V3", 50.0),
    }
    counter = MessageCounter()
    caps = {("A", "B"): 1}

    updated, outcomes = resolve_conflicts_sequentially(
        routes,
        ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets,
        t_now=0.0,
        edge_capacities=caps,
        msg_counter=counter,
    )

    remaining = detect_conflicts(
        prepare_occupancies(updated.values()), edge_capacities=caps
    )
    assert len(remaining) == 0
    assert len(outcomes) >= 1
    assert counter.total_negotiation_messages > 0


def test_yield_delay_is_based_on_contested_edge_arrival():
    """Upstream travel time is not counted as waiting at the contested edge."""
    routes = {
        "V1": VehicleRoute(
            "V1",
            ["X", "A", "B"],
            0.0,
            {("X", "A"): 100.0, ("A", "B"): 10.0},
            priority_weight=2.0,
        ),
        "V2": VehicleRoute(
            "V2",
            ["X", "A", "B"],
            2.0,
            {("X", "A"): 100.0, ("A", "B"): 10.0},
            priority_weight=1.0,
        ),
    }
    caps = {("X", "A"): 2, ("A", "B"): 1}
    budgets = {v_id: VirtualCreditBudget(v_id, 50.0) for v_id in routes}

    updated, outcomes = resolve_conflicts_sequentially(
        routes,
        ThresholdTrigger(tau=200.0, edge_capacities=caps),
        budgets,
        t_now=0.0,
        edge_capacities=caps,
    )

    assert outcomes[0].edge == ("A", "B")
    assert outcomes[0].hold_until == 110.0
    assert updated["V2"].start_time == 10.0
    assert (
        detect_conflicts(
            prepare_occupancies(updated.values()), edge_capacities=caps
        )
        == []
    )


def test_resolution_limit_reports_triggered_conflicts():
    """Raise RuntimeError when max_rounds ends with triggered conflicts."""
    routes = {
        f"V{i}": VehicleRoute(
            f"V{i}",
            ["A", "B"],
            float(i - 1),
            {("A", "B"): 10.0},
            priority_weight=float(4 - i),
        )
        for i in range(1, 4)
    }
    caps = {("A", "B"): 1}
    budgets = {v_id: VirtualCreditBudget(v_id, 50.0) for v_id in routes}

    with pytest.raises(RuntimeError, match="triggered conflict"):
        resolve_conflicts_sequentially(
            routes,
            ThresholdTrigger(tau=30.0, edge_capacities=caps),
            budgets,
            t_now=0.0,
            edge_capacities=caps,
            max_rounds=1,
        )


def test_rate_limiter_keyed_by_edge_and_vehicles_with_60s_cooldown():
    """Rate limiter enforces 60s cooldown per (edge, vehicle_set) pair."""
    routes = {
        "V1": VehicleRoute(
            "V1", ["A", "B"], 100.0, {("A", "B"): 10.0}, priority_weight=2.0
        ),
        "V2": VehicleRoute(
            "V2", ["A", "B"], 102.0, {("A", "B"): 10.0}, priority_weight=1.0
        ),
    }
    caps = {("A", "B"): 1}
    budgets = {v_id: VirtualCreditBudget(v_id, 50.0) for v_id in routes}
    limiter = NegotiationRateLimiter(cooldown_s=60.0)
    counter = MessageCounter()
    policy = ThresholdTrigger(tau=120.0, edge_capacities=caps)

    _, outcomes_1 = resolve_conflicts_sequentially(
        routes,
        policy,
        budgets,
        t_now=0.0,
        edge_capacities=caps,
        msg_counter=counter,
        rate_limiter=limiter,
    )
    assert len(outcomes_1) == 1
    assert counter.total_negotiation_messages == 6

    _, outcomes_2 = resolve_conflicts_sequentially(
        routes,
        policy,
        budgets,
        t_now=30.0,
        edge_capacities=caps,
        msg_counter=counter,
        rate_limiter=limiter,
    )
    assert outcomes_2 == []
    assert counter.total_negotiation_messages == 6

    routes_v3 = {
        "V1": routes["V1"],
        "V3": VehicleRoute(
            "V3", ["A", "B"], 102.0, {("A", "B"): 10.0}, priority_weight=1.0
        ),
    }
    budgets["V3"] = VirtualCreditBudget("V3", 50.0)
    _, outcomes_3 = resolve_conflicts_sequentially(
        routes_v3,
        policy,
        budgets,
        t_now=30.0,
        edge_capacities=caps,
        msg_counter=counter,
        rate_limiter=limiter,
    )
    assert len(outcomes_3) == 1
    assert counter.total_negotiation_messages == 12

    _, outcomes_4 = resolve_conflicts_sequentially(
        routes,
        policy,
        budgets,
        t_now=60.0,
        edge_capacities=caps,
        msg_counter=counter,
        rate_limiter=limiter,
    )
    assert len(outcomes_4) == 1
    assert counter.total_negotiation_messages == 18


def test_secondary_overlap_on_multi_slot_edge_resolves_cleanly():
    """Resolve a new overlap window on a capacity-two edge."""
    routes = {
        "V1": VehicleRoute(
            "V1", ["A", "B"], 0.0, {("A", "B"): 10.0}, priority_weight=4.0
        ),
        "V2": VehicleRoute(
            "V2", ["A", "B"], 2.0, {("A", "B"): 10.0}, priority_weight=3.0
        ),
        "V3": VehicleRoute(
            "V3", ["A", "B"], 4.0, {("A", "B"): 10.0}, priority_weight=1.0
        ),
        "V4": VehicleRoute(
            "V4", ["A", "B"], 11.0, {("A", "B"): 10.0}, priority_weight=2.0
        ),
    }
    caps = {("A", "B"): 2}
    budgets = {v_id: VirtualCreditBudget(v_id, 50.0) for v_id in routes}

    updated, outcomes = resolve_conflicts_sequentially(
        routes,
        ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets,
        t_now=0.0,
        edge_capacities=caps,
    )

    assert len(outcomes) >= 1
    assert (
        detect_conflicts(
            prepare_occupancies(updated.values()), edge_capacities=caps
        )
        == []
    )
