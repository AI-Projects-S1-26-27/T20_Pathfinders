"""Unit tests for stand-alone (tau, delta_t) sensitivity analysis."""

import pytest

from src.coordination.conflict import (
    Edge,
    VehicleRoute,
    detect_conflicts,
    prepare_occupancies,
)
from src.coordination.negotiation import VirtualCreditBudget
from src.coordination.sensitivity import (
    SweepCellResult,
    build_synthetic_bottleneck_scenario,
    evaluate_tau_delta_t_cell,
    select_operating_point,
    verify_seed_disjointness,
)


def test_verify_seed_disjointness_rejects_overlap() -> None:
    """Pilot and test seed sets with shared seeds must raise ValueError."""
    verify_seed_disjointness([301, 302], [401, 402])
    with pytest.raises(ValueError, match="disjoint"):
        verify_seed_disjointness([301, 302], [302, 401])


def test_select_operating_point_prefers_zero_unresolved_then_min_messages() -> None:
    """select_operating_point minimizes unresolved conflicts, breaking ties by messages."""
    cells = [
        SweepCellResult(30.0, 10.0, 5, 10.0, 1.0, 50.0, 2.0),
        SweepCellResult(120.0, 20.0, 5, 25.0, 0.0, 40.0, 4.0),
        SweepCellResult(300.0, 10.0, 5, 18.0, 0.0, 35.0, 4.0),
    ]
    chosen, target_met = select_operating_point(cells, target_unresolved=0.0)
    assert target_met is True
    assert chosen.tau == 300.0
    assert chosen.delta_t == 10.0

    fallback_cells = [
        SweepCellResult(60.0, 10.0, 5, 12.0, 0.6, 55.0, 2.0),
        SweepCellResult(300.0, 20.0, 5, 35.0, 0.2, 45.0, 3.0),
    ]
    fallback, fallback_met = select_operating_point(
        fallback_cells, target_unresolved=0.0
    )
    assert fallback_met is False
    assert fallback.tau == 300.0
    assert fallback.delta_t == 20.0


def test_unresolved_conflicts_counted_with_fixed_zero_delta_t() -> None:
    """Two cells with different delta_t values must count unresolved conflicts at delta_t=0."""
    edge: Edge = ("A", "B")
    routes = {
        "V1": VehicleRoute("V1", ["A", "B"], 0.0, {edge: 10.0}),
        "V2": VehicleRoute("V2", ["A", "B"], 25.0, {edge: 10.0}),
    }
    occupancies = prepare_occupancies(routes.values())
    assert detect_conflicts(occupancies, {edge: 1}, delta_t=0.0) == []
    assert detect_conflicts(occupancies, {edge: 1}, delta_t=40.0)

    def scenario_builder(seed: int):
        budgets = {
            vehicle_id: VirtualCreditBudget(vehicle_id, 100.0)
            for vehicle_id in routes
        }
        return routes, budgets, {edge: 1}

    cell_dt10 = evaluate_tau_delta_t_cell(
        tau=0.0, delta_t=10.0, seeds=[301], cooldown_s=0.0,
        scenario_builder=scenario_builder,
    )
    cell_dt40 = evaluate_tau_delta_t_cell(
        tau=0.0, delta_t=40.0, seeds=[301], cooldown_s=0.0,
        scenario_builder=scenario_builder,
    )

    assert cell_dt10.mean_unresolved_conflicts == 0.0
    assert cell_dt40.mean_unresolved_conflicts == 0.0
    assert cell_dt10.mean_resolutions == 0.0
    assert cell_dt40.mean_resolutions == 0.0


def test_single_t_now_rate_limiter_blocks_repeat_group_auctions() -> None:
    """Single-timestamp evaluation lets cooldown_s=60 block repeat auctions vs cooldown_s=0."""
    limited = evaluate_tau_delta_t_cell(
        tau=300.0,
        delta_t=20.0,
        seeds=[301, 302, 303, 304, 305],
        cooldown_s=60.0,
        scenario_builder=build_synthetic_bottleneck_scenario,
    )
    unlimited = evaluate_tau_delta_t_cell(
        tau=300.0,
        delta_t=20.0,
        seeds=[301, 302, 303, 304, 305],
        cooldown_s=0.0,
        scenario_builder=build_synthetic_bottleneck_scenario,
    )

    assert unlimited.mean_messages > limited.mean_messages
    assert limited.mean_unresolved_conflicts > unlimited.mean_unresolved_conflicts
