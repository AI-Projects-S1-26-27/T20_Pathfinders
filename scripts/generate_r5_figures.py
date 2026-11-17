"""Generate R5 Report Figures F5 & F8 and Export Sensitivity Results."""

import json
from pathlib import Path
import random
from typing import Dict, Tuple

import matplotlib.pyplot as plt

from src.coordination.conflict import Edge, VehicleRoute
from src.coordination.negotiation import VirtualCreditBudget
from src.coordination.resolver import resolve_conflicts_sequentially
from src.coordination.sensitivity import (
    DELTA_T_GRID_DEFAULT,
    TAU_GRID_DEFAULT,
    run_tau_delta_t_sweep,
    select_operating_point,
    verify_seed_disjointness,
)
from src.coordination.trigger import ThresholdTrigger

PILOT_SEEDS = (101, 102, 103, 104, 105)
TEST_SEEDS = (201, 202, 203, 204, 205, 206, 207, 208, 209, 210)


def build_synthetic_bottleneck_scenario(
    seed: int,
) -> Tuple[
    Dict[str, VehicleRoute],
    Dict[str, VirtualCreditBudget],
    Dict[Edge, int],
]:
    """Generate a deterministic multi-vehicle bottleneck for sensitivity."""
    rng = random.Random(seed)
    caps: Dict[Edge, int] = {
        ("N1", "N2"): 1,
        ("N2", "N3"): 2,
        ("N3", "N4"): 1,
    }
    routes: Dict[str, VehicleRoute] = {}
    budgets: Dict[str, VirtualCreditBudget] = {}

    for idx in range(1, 7):
        vid = f"V{idx}"
        start_t = rng.uniform(0.0, 180.0)
        t12 = rng.uniform(8.0, 18.0)
        t23 = rng.uniform(10.0, 20.0)
        t34 = rng.uniform(8.0, 18.0)
        prio = rng.choice([1.0, 2.0, 4.0])
        routes[vid] = VehicleRoute(
            vehicle_id=vid,
            nodes=["N1", "N2", "N3", "N4"],
            start_time=start_t,
            travel_times={
                ("N1", "N2"): t12,
                ("N2", "N3"): t23,
                ("N3", "N4"): t34,
            },
            priority_weight=prio,
            detour_extra_time=rng.uniform(12.0, 30.0),
        )
        budgets[vid] = VirtualCreditBudget(vid, 100.0)

    return routes, budgets, caps


def generate_f5_two_vehicle_demo(out_path: Path) -> None:
    """Generates Figure F5: two-vehicle single-slot road without vs with."""
    routes = {
        "V1 (Standard)": VehicleRoute(
            "V1 (Standard)",
            ["A", "B"],
            start_time=0.0,
            travel_times={("A", "B"): 12.0},
            priority_weight=1.0,
        ),
        "V2 (Express)": VehicleRoute(
            "V2 (Express)",
            ["A", "B"],
            start_time=1.0,
            travel_times={("A", "B"): 4.0},
            priority_weight=4.0,
        ),
    }
    caps = {("A", "B"): 1}
    budgets = {vid: VirtualCreditBudget(vid, 50.0) for vid in routes}

    # Without negotiation: V1 enters at 0..12, V2 queues behind V1 (12..16)
    no_neg_intervals = {
        "V1 (Standard)": (0.0, 12.0),
        "V2 (Express)": (12.0, 16.0),
    }
    no_neg_weighted_delay = 1.0 * 0.0 + 4.0 * (12.0 - 1.0)

    # With Vickrey negotiation: V2 wins slot (1..5), V1 yields until 5 (5..17)
    updated, _ = resolve_conflicts_sequentially(
        routes=routes,
        trigger_policy=ThresholdTrigger(tau=30.0, edge_capacities=caps),
        budgets=budgets,
        t_now=0.0,
        edge_capacities=caps,
    )
    with_neg_intervals = {
        vid: (r.start_time, r.start_time + r.travel_times[("A", "B")])
        for vid, r in updated.items()
    }
    with_neg_weighted_delay = 1.0 * (5.0 - 0.0) + 4.0 * 0.0

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    panels = [
        (
            axes[0],
            (
                "Without Negotiation "
                f"(Weighted Delay = {no_neg_weighted_delay:.0f})"
            ),
            no_neg_intervals,
        ),
        (
            axes[1],
            (
                "With Vickrey Auction "
                f"(Weighted Delay = {with_neg_weighted_delay:.0f})"
            ),
            with_neg_intervals,
        ),
    ]

    colors = {"V1 (Standard)": "#4C78A8", "V2 (Express)": "#F58518"}
    for ax, title, intervals in panels:
        for idx, (label, (start, end)) in enumerate(intervals.items()):
            ax.barh(
                idx,
                end - start,
                left=start,
                height=0.45,
                color=colors[label],
                edgecolor="black",
            )
            ax.text(
                start + (end - start) / 2.0,
                idx,
                f"{start:.0f}-{end:.0f}s",
                ha="center",
                va="center",
                color="white",
                fontweight="bold",
                fontsize=9,
            )
        ax.set_yticks([0, 1])
        ax.set_yticklabels(list(intervals.keys()))
        ax.set_xlabel("Simulated Time (s)")
        ax.set_xlim(0, 19)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="x", linestyle="--", alpha=0.4)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def generate_f8_sensitivity_plot(
    out_path: Path, json_out_path: Path
) -> None:
    """Runs pilot tuning + test sweep and generates Figure F8."""
    verify_seed_disjointness(PILOT_SEEDS, TEST_SEEDS)

    pilot_results = run_tau_delta_t_sweep(
        pilot_or_test_seeds=PILOT_SEEDS,
        scenario_builder=build_synthetic_bottleneck_scenario,
        taus=TAU_GRID_DEFAULT,
        delta_ts=DELTA_T_GRID_DEFAULT,
    )
    chosen = select_operating_point(pilot_results)

    test_results = run_tau_delta_t_sweep(
        pilot_or_test_seeds=TEST_SEEDS,
        scenario_builder=build_synthetic_bottleneck_scenario,
        taus=TAU_GRID_DEFAULT,
        delta_ts=DELTA_T_GRID_DEFAULT,
    )
    selected_test_result = next(
        result
        for result in test_results
        if result.tau == chosen.tau and result.delta_t == chosen.delta_t
    )

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for dt in DELTA_T_GRID_DEFAULT:
        subset = sorted(
            [r for r in test_results if r.delta_t == dt],
            key=lambda x: x.tau,
        )
        taus = [r.tau for r in subset]
        msgs = [r.mean_messages for r in subset]
        unresolved = [r.mean_unresolved_conflicts for r in subset]

        selected_label = " (pilot-selected)" if dt == chosen.delta_t else ""
        axes[0].plot(
            taus,
            msgs,
            marker="o",
            label=f"delta_t = {dt:.0f}s{selected_label}",
        )
        axes[1].plot(
            taus,
            unresolved,
            marker="s",
            label=f"delta_t = {dt:.0f}s{selected_label}",
        )

    axes[0].axvline(
        chosen.tau,
        color="gray",
        linestyle="--",
        label=f"Pilot-selected tau = {chosen.tau:.0f}s",
    )
    axes[0].scatter(
        [selected_test_result.tau],
        [selected_test_result.mean_messages],
        color="black",
        marker="*",
        s=100,
        zorder=3,
        label="Selected point on test seeds",
    )
    axes[0].set_title("Negotiation Messages vs. Horizon tau")
    axes[0].set_xlabel("Lookahead Horizon tau (s)")
    axes[0].set_ylabel("Mean Negotiation Messages")
    axes[0].grid(True, linestyle="--", alpha=0.4)
    axes[0].legend()

    axes[1].axvline(
        chosen.tau,
        color="gray",
        linestyle="--",
        label=f"Pilot-selected tau = {chosen.tau:.0f}s",
    )
    axes[1].scatter(
        [selected_test_result.tau],
        [selected_test_result.mean_unresolved_conflicts],
        color="black",
        marker="*",
        s=100,
        zorder=3,
        label="Selected point on test seeds",
    )
    axes[1].set_title("Unresolved Conflicts vs. Horizon tau")
    axes[1].set_xlabel("Lookahead Horizon tau (s)")
    axes[1].set_ylabel("Mean Unresolved Capacity Overlaps")
    axes[1].grid(True, linestyle="--", alpha=0.4)
    axes[1].legend()

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200)
    plt.close(fig)

    payload = {
        "pilot_seeds": list(PILOT_SEEDS),
        "test_seeds": list(TEST_SEEDS),
        "frozen_operating_point": {
            "tau": chosen.tau,
            "delta_t": chosen.delta_t,
            "pilot_mean_messages": chosen.mean_messages,
            "pilot_mean_unresolved_conflicts": (
                chosen.mean_unresolved_conflicts
            ),
        },
        "test_sweep": [
            {
                "tau": r.tau,
                "delta_t": r.delta_t,
                "mean_messages": r.mean_messages,
                "mean_unresolved_conflicts": r.mean_unresolved_conflicts,
                "mean_total_delay_s": r.mean_total_delay_s,
            }
            for r in test_results
        ],
    }
    json_out_path.parent.mkdir(parents=True, exist_ok=True)
    json_out_path.write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    generate_f5_two_vehicle_demo(Path("reports/figures/f5_two_vehicle.png"))
    generate_f8_sensitivity_plot(
        Path("reports/figures/f8_tau_delta_t_sensitivity.png"),
        Path("reports/r5_sensitivity_summary.json"),
    )
