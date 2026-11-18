"""Generate R5 figures (F5 illustration, F8 sensitivity plots) and summary JSON."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src.coordination.conflict import Edge, VehicleRoute, prepare_occupancies  # noqa: E402
from src.coordination.messages import MessageCounter  # noqa: E402
from src.coordination.negotiation import VirtualCreditBudget  # noqa: E402
from src.coordination.resolver import resolve_conflicts_sequentially  # noqa: E402
from src.coordination.sensitivity import (  # noqa: E402
    DEFAULT_DELTA_T_GRID,
    DEFAULT_PILOT_SEEDS,
    DEFAULT_TAU_GRID,
    DEFAULT_TEST_SEEDS,
    run_tau_delta_t_sweep,
    select_operating_point,
    verify_seed_disjointness,
)
from src.coordination.trigger import ThresholdTrigger  # noqa: E402


def _weighted_delay_from_routes(
    baseline_routes: Dict[str, VehicleRoute],
    scheduled_routes: Dict[str, VehicleRoute],
    priorities: Dict[str, float],
) -> float:
    """Compute priority-weighted completion delay directly from route intervals."""
    total = 0.0
    for vid, baseline in baseline_routes.items():
        baseline_finish = baseline.start_time + sum(baseline.travel_times.values())
        scheduled = scheduled_routes[vid]
        scheduled_finish = scheduled.start_time + sum(
            scheduled.travel_times.values()
        )
        delay = max(0.0, scheduled_finish - baseline_finish)
        total += priorities[vid] * delay
    return round(total, 2)


def _schedule_without_negotiation(
    routes: Dict[str, VehicleRoute],
    edge: Edge,
) -> Dict[str, VehicleRoute]:
    """Apply first-come-first-served scheduling to the single shared edge."""
    scheduled: Dict[str, VehicleRoute] = {}
    edge_available = 0.0
    for route in sorted(
        routes.values(), key=lambda item: (item.start_time, item.vehicle_id)
    ):
        duration = route.travel_times[edge]
        edge_start = max(route.start_time, edge_available)
        scheduled[route.vehicle_id] = replace(route, start_time=edge_start)
        edge_available = edge_start + duration
    return scheduled


def generate_f5_two_vehicle_demo(output_path: Path) -> Dict[str, float]:
    """Generate Figure F5 illustrating two-vehicle Vickrey auction scheduling."""
    edge: Edge = ("A", "B")
    ideal_routes = {
        "V1": VehicleRoute("V1", ["A", "B"], 0.0, {edge: 12.0}, 1.0, 1.0),
        "V2": VehicleRoute("V2", ["A", "B"], 1.0, {edge: 4.0}, 4.0, 5.0),
    }
    unnegotiated_routes = _schedule_without_negotiation(ideal_routes, edge)
    capacities = {edge: 1}
    priorities = {vid: route.priority_weight for vid, route in ideal_routes.items()}
    budgets = {
        vid: VirtualCreditBudget(vid, credits=100.0)
        for vid in ideal_routes
    }
    counter = MessageCounter()
    auction_routes, _ = resolve_conflicts_sequentially(
        routes=ideal_routes,
        trigger_policy=ThresholdTrigger(
            tau=60.0,
            delta_t=0.0,
            edge_capacities=capacities,
        ),
        budgets=budgets,
        t_now=0.0,
        edge_capacities=capacities,
        delta_t=0.0,
        msg_counter=counter,
    )

    delay_unneg = _weighted_delay_from_routes(
        ideal_routes, unnegotiated_routes, priorities
    )
    delay_auction = _weighted_delay_from_routes(
        ideal_routes, auction_routes, priorities
    )

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    panels = [
        (
            axes[0],
            unnegotiated_routes,
            f"Without Negotiation (Weighted Delay = {delay_unneg:g})",
        ),
        (
            axes[1],
            auction_routes,
            f"With Vickrey Auction (Weighted Delay = {delay_auction:g})",
        ),
    ]
    colors = {"V1": "#4c72b0", "V2": "#f58518"}

    for ax, panel_routes, subtitle in panels:
        for y_idx, vid in enumerate(["V1", "V2"]):
            occ = prepare_occupancies([panel_routes[vid]])[0]
            width = occ.exit_time - occ.entry_time
            ax.barh(
                y_idx,
                width,
                left=occ.entry_time,
                height=0.45,
                color=colors[vid],
                edgecolor="black",
            )
            ax.text(
                occ.entry_time + width / 2.0,
                y_idx,
                f"{occ.entry_time:g}-{occ.exit_time:g}s",
                va="center",
                ha="center",
                color="white",
                fontweight="bold",
            )
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["V1 (Standard)", "V2 (Express)"])
        ax.set_xlim(0, 19)
        ax.set_xlabel("Simulated Time (s)")
        ax.set_title(subtitle)
        ax.grid(axis="x", linestyle="--", alpha=0.4)

    fig.suptitle(
        "Illustration: Two-Vehicle Single-Slot Scheduling",
        fontsize=12,
        fontweight="bold",
    )
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output_file:
        fig.savefig(output_file, format="png", dpi=200)
    plt.close(fig)

    return {
        "unnegotiated_weighted_delay": delay_unneg,
        "auction_weighted_delay": delay_auction,
    }


def generate_f8_sensitivity_and_summary(
    fig_path: Path,
    summary_path: Path,
) -> Dict[str, object]:
    """Run rate-limited and unthrottled sweeps, plot Figure F8, and save summary JSON."""
    verify_seed_disjointness(DEFAULT_PILOT_SEEDS, DEFAULT_TEST_SEEDS)

    # 1. Default rate-limited sweep (cooldown_s = 60.0)
    pilot_limited = run_tau_delta_t_sweep(
        tau_grid=DEFAULT_TAU_GRID,
        delta_t_grid=DEFAULT_DELTA_T_GRID,
        seeds=DEFAULT_PILOT_SEEDS,
        cooldown_s=60.0,
    )
    selected_limited, target_met_limited = select_operating_point(
        pilot_limited, target_unresolved=0.0
    )
    test_limited = run_tau_delta_t_sweep(
        tau_grid=DEFAULT_TAU_GRID,
        delta_t_grid=DEFAULT_DELTA_T_GRID,
        seeds=DEFAULT_TEST_SEEDS,
        cooldown_s=60.0,
    )
    test_selected_limited = next(
        r
        for r in test_limited
        if r.tau == selected_limited.tau and r.delta_t == selected_limited.delta_t
    )

    # 2. Unthrottled sweep (cooldown_s = 0.0)
    pilot_unlimited = run_tau_delta_t_sweep(
        tau_grid=DEFAULT_TAU_GRID,
        delta_t_grid=DEFAULT_DELTA_T_GRID,
        seeds=DEFAULT_PILOT_SEEDS,
        cooldown_s=0.0,
    )
    selected_unlimited, target_met_unlimited = select_operating_point(
        pilot_unlimited, target_unresolved=0.0
    )
    test_unlimited = run_tau_delta_t_sweep(
        tau_grid=DEFAULT_TAU_GRID,
        delta_t_grid=DEFAULT_DELTA_T_GRID,
        seeds=DEFAULT_TEST_SEEDS,
        cooldown_s=0.0,
    )
    test_selected_unlimited = next(
        r
        for r in test_unlimited
        if r.tau == selected_unlimited.tau and r.delta_t == selected_unlimited.delta_t
    )

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    markers = ["o", "s", "^"]
    for idx, dt in enumerate(DEFAULT_DELTA_T_GRID):
        rows = sorted(
            [r for r in test_limited if r.delta_t == dt],
            key=lambda r: r.tau,
        )
        taus = [r.tau for r in rows]
        msgs = [r.mean_messages for r in rows]
        unres = [r.mean_unresolved_conflicts for r in rows]
        label = (
            f"delta_t = {dt:g}s (pilot-selected)"
            if dt == selected_limited.delta_t
            else f"delta_t = {dt:g}s"
        )
        axes[0].plot(taus, msgs, marker=markers[idx], label=label)
        axes[1].plot(taus, unres, marker=markers[idx], label=label)

    for ax in axes:
        ax.axvline(
            selected_limited.tau,
            color="gray",
            linestyle="--",
            label=f"Pilot-selected tau = {selected_limited.tau:g}s",
        )
        ax.set_xlabel("Lookahead Horizon tau (s)")
        ax.grid(True, linestyle="--", alpha=0.4)

    axes[0].scatter(
        [test_selected_limited.tau],
        [test_selected_limited.mean_messages],
        marker="*",
        s=150,
        color="black",
        zorder=5,
        label="Selected point on test seeds",
    )
    axes[1].scatter(
        [test_selected_limited.tau],
        [test_selected_limited.mean_unresolved_conflicts],
        marker="*",
        s=150,
        color="black",
        zorder=5,
        label="Selected point on test seeds",
    )

    axes[0].set_ylabel("Mean Negotiation Messages")
    axes[0].set_title("Negotiation Messages vs. Horizon tau")
    axes[0].legend(loc="upper left")

    axes[1].set_ylabel("Mean Unresolved Overlaps (delta_t = 0s)")
    axes[1].set_title("Unresolved Conflicts vs. Horizon tau")
    axes[1].legend(loc="upper right")

    fig.tight_layout()
    fig_path.parent.mkdir(parents=True, exist_ok=True)
    with fig_path.open("wb") as output_file:
        fig.savefig(output_file, format="png", dpi=200)
    plt.close(fig)

    summary: Dict[str, object] = {
        "study_scope": "stand_alone_synthetic_bottleneck",
        "frozen_in_simulator": False,
        "unresolved_conflict_definition_delta_t_s": 0.0,
        "tau_grid_s": list(DEFAULT_TAU_GRID),
        "delta_t_grid_s": list(DEFAULT_DELTA_T_GRID),
        "pilot_seeds": list(DEFAULT_PILOT_SEEDS),
        "test_seeds": list(DEFAULT_TEST_SEEDS),
        "with_rate_limiter_cooldown_60s": {
            "cooldown_s": 60.0,
            "target_unresolved": 0.0,
            "target_met": target_met_limited,
            "selected_tau_s": selected_limited.tau,
            "selected_delta_t_s": selected_limited.delta_t,
            "pilot_selected_cell": selected_limited.to_dict(),
            "test_selected_cell": test_selected_limited.to_dict(),
            "pilot_grid": [r.to_dict() for r in pilot_limited],
            "test_grid": [r.to_dict() for r in test_limited],
        },
        "without_rate_limiter_cooldown_0s": {
            "cooldown_s": 0.0,
            "target_unresolved": 0.0,
            "target_met": target_met_unlimited,
            "selected_tau_s": selected_unlimited.tau,
            "selected_delta_t_s": selected_unlimited.delta_t,
            "pilot_selected_cell": selected_unlimited.to_dict(),
            "test_selected_cell": test_selected_unlimited.to_dict(),
            "pilot_grid": [r.to_dict() for r in pilot_unlimited],
            "test_grid": [r.to_dict() for r in test_unlimited],
        },
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    """Generate F5, F8, and sensitivity summary JSON."""
    f5_metrics = generate_f5_two_vehicle_demo(
        Path("reports/figures/f5_two_vehicle.png")
    )
    summary = generate_f8_sensitivity_and_summary(
        fig_path=Path("reports/figures/f8_tau_delta_t_sensitivity.png"),
        summary_path=Path("reports/r5_sensitivity_summary.json"),
    )
    lim = summary["with_rate_limiter_cooldown_60s"]
    unlim = summary["without_rate_limiter_cooldown_0s"]
    print("F5 metrics:", f5_metrics)
    print("With cooldown=60s:", lim["pilot_selected_cell"], lim["test_selected_cell"])
    print(
        "With cooldown=0s:",
        unlim["pilot_selected_cell"],
        unlim["test_selected_cell"],
    )


if __name__ == "__main__":
    main()
