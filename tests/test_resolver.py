"""Unit Tests for Sequential Resolver, Rate Limiter & Integration Adapters."""

import pytest

from src.coordination.conflict import VehicleRoute, detect_conflicts, prepare_occupancies
from src.coordination.messages import MessageCounter
from src.coordination.negotiation import VirtualCreditBudget
from src.coordination.resolver import (
    LocalReplanCoordinator,
    NegotiationRateLimiter,
    build_edge_capacities_from_classes,
    coordination_hook,
    on_local_replan_coordination,
    resolve_conflicts_sequentially,
    route_etas_to_vehicle_route,
)
from src.coordination.trigger import ThresholdTrigger


def test_two_vehicle_auction_reduces_queue_delay():
    """Auction order cuts the input-order FCFS queue delay from 10s to 2s."""
    routes = {
        "V1": VehicleRoute("V1", ["A", "B"], 0.0, {("A", "B"): 10.0}, priority_weight=1.0),
        "V2": VehicleRoute("V2", ["A", "B"], 0.0, {("A", "B"): 2.0}, priority_weight=4.0),
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
    assert updated["V1"].start_time == 2.0
    assert updated["V2"].start_time == 0.0
    coordinated_wait = sum(
        updated[v].start_time - routes[v].start_time for v in routes
    )
    fcfs_wait = routes["V1"].travel_times[("A", "B")]
    assert coordinated_wait == 2.0
    assert fcfs_wait == 10.0
    assert coordinated_wait < fcfs_wait
    assert detect_conflicts(prepare_occupancies(updated.values()), edge_capacities=caps) == []


def test_d5_road_class_capacities():
    """Verifies residential=1, secondary=2, primary=3 per Decision D5."""
    classes = {
        ("A", "B"): "residential",
        ("B", "C"): "secondary",
        ("C", "D"): "primary",
    }
    caps = build_edge_capacities_from_classes(classes)
    assert caps == {("A", "B"): 1, ("B", "C"): 2, ("C", "D"): 3}


def test_route_etas_adapter():
    """Verifies conversion from R1/R2 [(node, eta, order_id)] tuples to VehicleRoute."""
    etas = [("Depot", 10.0, None), ("N1", 22.0, "ORD-1"), ("N2", 35.0, None)]
    vr = route_etas_to_vehicle_route("V1", etas, priority_weight=3.0)

    assert vr.vehicle_id == "V1"
    assert vr.nodes == ["Depot", "N1", "N2"]
    assert vr.start_time == 10.0
    assert vr.travel_times == {("Depot", "N1"): 12.0, ("N1", "N2"): 13.0}
    assert vr.priority_weight == 3.0


def test_sequential_resolution_three_vehicles():
    """Verifies sequential auctions clear a 3-vehicle bottleneck on a 1-slot road."""
    routes = {
        "V1": VehicleRoute(
            "V1", ["A", "B"], start_time=0.0, travel_times={("A", "B"): 10.0},
            priority_weight=3.0,
        ),
        "V2": VehicleRoute(
            "V2", ["A", "B"], start_time=1.0, travel_times={("A", "B"): 10.0},
            priority_weight=2.0,
        ),
        "V3": VehicleRoute(
            "V3", ["A", "B"], start_time=2.0, travel_times={("A", "B"): 10.0},
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

    remaining = detect_conflicts(prepare_occupancies(updated.values()), edge_capacities=caps)
    assert len(remaining) == 0
    assert len(outcomes) >= 1
    assert counter.total_negotiation_messages > 0


def test_yield_delay_is_based_on_contested_edge_arrival():
    """Upstream travel time is not incorrectly counted as waiting at the contested edge."""
    routes = {
        "V1": VehicleRoute(
            "V1", ["X", "A", "B"], 0.0,
            {("X", "A"): 100.0, ("A", "B"): 10.0}, priority_weight=2.0,
        ),
        "V2": VehicleRoute(
            "V2", ["X", "A", "B"], 2.0,
            {("X", "A"): 100.0, ("A", "B"): 10.0}, priority_weight=1.0,
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
    assert updated["V2"].start_time == 10.0
    assert detect_conflicts(
        prepare_occupancies(updated.values()), edge_capacities=caps
    ) == []


def test_resolution_limit_reports_triggered_conflicts():
    """The resolver must not silently return while triggered conflicts remain."""
    routes = {
        f"V{i}": VehicleRoute(
            f"V{i}", ["A", "B"], float(i - 1), {("A", "B"): 10.0},
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


def test_coordination_hook_end_to_end():
    """Verifies R7 integration hook returns updated routes and message count."""
    all_etas = {
        "V1": [("A", 0.0, None), ("B", 10.0, "O1")],
        "V2": [("A", 2.0, None), ("B", 12.0, "O2")],
    }
    budgets = {
        "V1": VirtualCreditBudget("V1", 50.0),
        "V2": VirtualCreditBudget("V2", 50.0),
    }
    caps = {("A", "B"): 1}

    updated, msgs = coordination_hook(
        vehicle_id="V1",
        route_etas=all_etas["V1"],
        all_routes_etas=all_etas,
        t_now=0.0,
        trigger_policy=ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets=budgets,
        edge_capacities=caps,
    )

    assert msgs == 6
    assert updated["V2"].start_time >= 10.0
    assert detect_conflicts(
        prepare_occupancies(updated.values()), edge_capacities=caps
    ) == []


def test_independent_mode_zero_negotiation_messages():
    """Week 5 Exit Criterion: independent mode (trigger_enabled=False) sends 0 messages."""
    all_etas = {
        "V1": [("A", 0.0, None), ("B", 10.0, "O1")],
        "V2": [("A", 2.0, None), ("B", 12.0, "O2")],
    }
    budgets = {
        "V1": VirtualCreditBudget("V1", 50.0),
        "V2": VirtualCreditBudget("V2", 50.0),
    }
    caps = {("A", "B"): 1}
    counter = MessageCounter()

    # Simulate an order allocation message logged separately
    counter.log_message("allocation")

    updated, outcomes = on_local_replan_coordination(
        vehicle_id="V1",
        route_etas=all_etas["V1"],
        all_routes_etas=all_etas,
        t_now=0.0,
        trigger_policy=ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets=budgets,
        edge_capacities=caps,
        msg_counter=counter,
        trigger_enabled=False,
    )

    assert outcomes == []
    assert counter.requests == 0
    assert counter.responses == 0
    assert counter.results == 0
    assert counter.total_negotiation_messages == 0
    assert counter.allocations == 1
    assert updated["V1"].start_time == 0.0
    assert updated["V2"].start_time == 2.0


def test_rate_limiter_prevents_renegotiation_spam():
    """Week 5 Task: rate-limits repeated negotiations on the same resource within cooldown."""
    all_etas = {
        "V1": [("A", 0.0, None), ("B", 10.0, "O1")],
        "V2": [("A", 2.0, None), ("B", 12.0, "O2")],
    }
    budgets = {
        "V1": VirtualCreditBudget("V1", 50.0),
        "V2": VirtualCreditBudget("V2", 50.0),
    }
    caps = {("A", "B"): 1}
    limiter = NegotiationRateLimiter(cooldown_s=20.0)
    counter = MessageCounter()

    # First replan at t = 0.0 triggers negotiation
    _, outcomes_1 = on_local_replan_coordination(
        vehicle_id="V1",
        route_etas=all_etas["V1"],
        all_routes_etas=all_etas,
        t_now=0.0,
        trigger_policy=ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets=budgets,
        edge_capacities=caps,
        msg_counter=counter,
        rate_limiter=limiter,
        trigger_enabled=True,
    )
    assert len(outcomes_1) == 1
    assert counter.total_negotiation_messages == 6

    # Replan 5 seconds later (within 20s cooldown) is suppressed
    _, outcomes_2 = on_local_replan_coordination(
        vehicle_id="V1",
        route_etas=all_etas["V1"],
        all_routes_etas=all_etas,
        t_now=5.0,
        trigger_policy=ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets=budgets,
        edge_capacities=caps,
        msg_counter=counter,
        rate_limiter=limiter,
        trigger_enabled=True,
    )
    assert outcomes_2 == []
    assert counter.total_negotiation_messages == 6

    # Replan at t = 25.0 (cooldown elapsed) allows negotiation again
    _, outcomes_3 = on_local_replan_coordination(
        vehicle_id="V1",
        route_etas=all_etas["V1"],
        all_routes_etas=all_etas,
        t_now=25.0,
        trigger_policy=ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets=budgets,
        edge_capacities=caps,
        msg_counter=counter,
        rate_limiter=limiter,
        trigger_enabled=True,
    )
    assert len(outcomes_3) == 1
    assert counter.total_negotiation_messages == 12


def test_replan_coordinator_keeps_edge_cooldown_across_participants():
    """Persistent replan coordinator rate-limits the edge despite a changed vehicle set."""
    routes_v1_v2 = {
        "V1": [("A", 0.0, None), ("B", 10.0, "O1")],
        "V2": [("A", 2.0, None), ("B", 12.0, "O2")],
    }
    routes_v1_v3 = {
        "V1": [("A", 0.0, None), ("B", 10.0, "O1")],
        "V3": [("A", 2.0, None), ("B", 12.0, "O3")],
    }
    caps = {("A", "B"): 1}
    budgets = {
        vehicle_id: VirtualCreditBudget(vehicle_id, 50.0)
        for vehicle_id in ("V1", "V2", "V3")
    }
    counter = MessageCounter()
    coordinator = LocalReplanCoordinator(
        ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets,
        edge_capacities=caps,
        cooldown_s=20.0,
    )

    _, initial = coordinator.after_replan(
        "V1", routes_v1_v2["V1"], routes_v1_v2, t_now=0.0, msg_counter=counter
    )
    _, during_cooldown = coordinator.after_replan(
        "V1", routes_v1_v3["V1"], routes_v1_v3, t_now=5.0, msg_counter=counter
    )
    _, after_cooldown = coordinator.after_replan(
        "V1", routes_v1_v3["V1"], routes_v1_v3, t_now=20.0, msg_counter=counter
    )

    assert len(initial) == 1
    assert during_cooldown == []
    assert len(after_cooldown) == 1
    assert counter.total_negotiation_messages == 12


def test_compatibility_hook_accepts_persistent_rate_limiter():
    """The existing hook can also reuse limiter state across calls."""
    routes = {
        "V1": [("A", 0.0, None), ("B", 10.0, "O1")],
        "V2": [("A", 2.0, None), ("B", 12.0, "O2")],
    }
    caps = {("A", "B"): 1}
    budgets = {v_id: VirtualCreditBudget(v_id, 50.0) for v_id in routes}
    limiter = NegotiationRateLimiter(cooldown_s=20.0)
    policy = ThresholdTrigger(tau=30.0, edge_capacities=caps)

    _, first_messages = coordination_hook(
        "V1", routes["V1"], routes, 0.0, policy, budgets,
        edge_capacities=caps, rate_limiter=limiter,
    )
    _, suppressed_messages = coordination_hook(
        "V1", routes["V1"], routes, 5.0, policy, budgets,
        edge_capacities=caps, rate_limiter=limiter,
    )

    assert first_messages == 6
    assert suppressed_messages == 0


def test_replan_hook_respects_priority_weights():
    """Higher-priority vehicle wins the contested slot during live replan coordination."""
    all_etas = {
        "V1": [("A", 0.0, None), ("B", 10.0, "O1")],
        "V2": [("A", 0.0, None), ("B", 10.0, "O2")],
    }
    budgets = {
        "V1": VirtualCreditBudget("V1", 50.0),
        "V2": VirtualCreditBudget("V2", 50.0),
    }
    caps = {("A", "B"): 1}

    updated, outcomes = on_local_replan_coordination(
        vehicle_id="V1",
        route_etas=all_etas["V1"],
        all_routes_etas=all_etas,
        t_now=0.0,
        trigger_policy=ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets=budgets,
        edge_capacities=caps,
        priority_weights={"V1": 1.0, "V2": 5.0},
    )

    assert len(outcomes) == 1
    assert outcomes[0].winner_id == "V2"
    assert updated["V1"].start_time == 10.0
    assert updated["V2"].start_time == 0.0


def test_secondary_overlap_on_multi_slot_edge_resolves_cleanly():
    """Shifted vehicles creating a new overlap window on a capacity-2 edge get resolved."""
    routes = {
        "V1": VehicleRoute("V1", ["A", "B"], 0.0, {("A", "B"): 10.0}, priority_weight=4.0),
        "V2": VehicleRoute("V2", ["A", "B"], 2.0, {("A", "B"): 10.0}, priority_weight=3.0),
        "V3": VehicleRoute("V3", ["A", "B"], 4.0, {("A", "B"): 10.0}, priority_weight=1.0),
        "V4": VehicleRoute("V4", ["A", "B"], 11.0, {("A", "B"): 10.0}, priority_weight=2.0),
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
    assert detect_conflicts(prepare_occupancies(updated.values()), edge_capacities=caps) == []