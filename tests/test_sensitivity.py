"""Unit Tests for R5 Week 6-7 Pilot Tuning & Sensitivity Sweep."""

import pytest

from scripts.generate_r5_figures import build_synthetic_bottleneck_scenario
from src.coordination.sensitivity import (
    SweepPointResult,
    evaluate_tau_delta_t_cell,
    run_tau_delta_t_sweep,
    select_operating_point,
    verify_seed_disjointness,
)
from src.coordination.conflict import VehicleRoute
from src.coordination.negotiation import VirtualCreditBudget


def test_verify_seed_disjointness_rejects_overlap():
    """Pilot and test seeds must be strictly disjoint per Section 7.1."""
    verify_seed_disjointness([1, 2, 3], [4, 5, 6])
    with pytest.raises(ValueError, match="disjoint"):
        verify_seed_disjointness([1, 2, 3], [3, 4, 5])


def test_select_operating_point_prefers_zero_unresolved_and_low_cost():
    """Operating point selector minimizes unresolved conflicts then cost."""
    candidates = [
        SweepPointResult(30.0, 10.0, 5, 6.0, 1.2, 10.0, 1.0),
        SweepPointResult(120.0, 20.0, 5, 18.0, 0.0, 22.0, 3.0),
        SweepPointResult(300.0, 40.0, 5, 30.0, 0.0, 22.0, 5.0),
    ]
    chosen = select_operating_point(candidates)
    assert chosen.tau == 120.0
    assert chosen.delta_t == 20.0


def test_sensitivity_sweep_messages_non_decreasing_with_tau():
    """Larger lookahead tau triggers at least as many negotiations."""
    results = run_tau_delta_t_sweep(
        pilot_or_test_seeds=[11, 12, 13],
        scenario_builder=build_synthetic_bottleneck_scenario,
        taus=(10.0, 120.0, 300.0),
        delta_ts=(20.0,),
    )
    by_tau = {r.tau: r for r in results}
    assert by_tau[10.0].mean_messages <= by_tau[120.0].mean_messages
    assert by_tau[120.0].mean_messages <= by_tau[300.0].mean_messages


def test_sensitivity_sweep_applies_delta_t_to_resolution_and_validation():
    """Use delta_t for resolution and unresolved-conflict counts."""
    edge = ("A", "B")

    def scenario_builder(seed):
        routes = {
            "V1": VehicleRoute("V1", ["A", "B"], 0.0, {edge: 5.0}),
            "V2": VehicleRoute("V2", ["A", "B"], 8.0, {edge: 5.0}),
        }
        budgets = {
            vehicle_id: VirtualCreditBudget(vehicle_id, 100.0)
            for vehicle_id in routes
        }
        return routes, budgets, {edge: 1}

    no_gap_rule = evaluate_tau_delta_t_cell(
        tau=20.0,
        delta_t=0.0,
        seeds=[1],
        scenario_builder=scenario_builder,
    )
    resolved_gap_rule = evaluate_tau_delta_t_cell(
        tau=20.0,
        delta_t=10.0,
        seeds=[1],
        scenario_builder=scenario_builder,
    )
    untriggered_gap_rule = evaluate_tau_delta_t_cell(
        tau=0.0,
        delta_t=10.0,
        seeds=[1],
        scenario_builder=scenario_builder,
    )

    assert no_gap_rule.mean_resolutions == 0
    assert resolved_gap_rule.mean_resolutions == 1
    assert resolved_gap_rule.mean_messages > no_gap_rule.mean_messages
    assert resolved_gap_rule.mean_unresolved_conflicts == 0
    assert untriggered_gap_rule.mean_resolutions == 0
    assert untriggered_gap_rule.mean_unresolved_conflicts == 1
