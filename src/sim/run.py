"""Headless run: python -m src.sim.run --config configs/sim_default.yaml

The config's ``mode`` picks the condition (plan section 7.1):

* ``centralized``: R4's AllocationSimulation; R4 re-solves the whole fleet
  (OR-Tools) on every event. Vehicles' hand-written routes are not used.
* ``independent``: R1's environment + R7's AgentPlanner; each vehicle
  replans locally with D* Lite, never negotiates.
* ``full``: as independent, plus R5's trigger + negotiation after every
  replan (src/sim/coordination_hook.py).
"""
import argparse
import logging
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.sim.environment import DEFAULT_DURATION_S, DeliverySimEnvironment
from src.sim.models import Order, Vehicle
from src.sim.world import DEFAULT_MAP_PATH, World

MODES = ("centralized", "independent", "full")

# Optional `planner:` block (independent/full): R7's AgentPlanner settings.
PLANNER_DEFAULTS: Dict[str, Any] = {
    "unreachable": "fail",
    "min_gain_s": 10.0,       # R7's W5 hysteresis defaults (to be frozen, D12)
    "min_gain_frac": 0.05,
}
# Optional `coordination:` block (full): R5's trigger + auction settings.
COORDINATION_DEFAULTS: Dict[str, Any] = {
    "tau": 600.0,             # D6: negotiate iff time-to-conflict < tau (s)
    "rate_limit_s": 60.0,
    "initial_credits": 100.0,
}


def _block(config: Dict[str, Any], name: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Defaults overlaid with the config block; a typo must not silently fall back."""
    block = dict(config.get(name) or {})
    unknown = set(block) - set(defaults)
    if unknown:
        raise ValueError(
            f"unknown keys in {name} config: {sorted(unknown)}; allowed: {sorted(defaults)}"
        )
    return {**defaults, **block}


def build_vehicle(cfg: Dict[str, Any]) -> Vehicle:
    route = [int(n) for n in cfg.get("route", [])]
    depot = int(cfg["depot_node"])
    return Vehicle(
        id=str(cfg["id"]),
        depot_node=depot,
        capacity=float(cfg["capacity"]),
        current_node=int(cfg.get("current_node", depot)),
        remaining_route=route,
        load=float(cfg.get("load", 0.0)),
        allowed_road_classes=cfg.get("allowed_road_classes"),
    )


def build_order(cfg: Dict[str, Any]) -> Order:
    return Order(**cfg)   # unknown keys raise TypeError: nothing is silently ignored


def assign_orders(vehicles: List[Vehicle], orders: List[Order]) -> Dict[str, List[Order]]:
    """Give each order to the first vehicle whose route visits its node.

    TODO(R4/R5): replace with the real planner / allocation.
    An order no route visits goes to the first vehicle (R7's planner then
    adds its stop). With no vehicles nothing is assigned; run.py fails those
    orders as "no_vehicle". Orders blocked by capacity end as
    "capacity_exceeded_unservable", unfinished ones as "horizon_exceeded".
    """
    assignment: Dict[str, List[Order]] = {v.id: [] for v in vehicles}
    for order in orders:
        for vehicle in vehicles:
            if order.node == vehicle.current_node or order.node in vehicle.remaining_route:
                assignment[vehicle.id].append(order)
                break
        else:
            if vehicles:
                assignment[vehicles[0].id].append(order)
    return assignment


def build_simulation(config: Dict[str, Any], world=None) -> Tuple[Any, Optional[Any]]:
    """The environment for the config's mode, ready to run, and R7's planner
    (None in centralized mode). ``world`` defaults to the config's map."""
    mode = str(config.get("mode", "independent"))
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    planner_cfg = _block(config, "planner", PLANNER_DEFAULTS)
    coord_cfg = _block(config, "coordination", COORDINATION_DEFAULTS)
    if world is None:
        map_path = config.get("map_path", DEFAULT_MAP_PATH)
        world = World.from_defaults(map_path, seed=int(config.get("seed", 42)))

    vehicles = [build_vehicle(v) for v in config.get("vehicles", [])]
    orders = [build_order(o) for o in config.get("orders", [])]

    if mode == "centralized":
        # Imported here: R4's allocation pulls in OR-Tools.
        from src.allocation.sim_runner import AllocationSimulation

        env = AllocationSimulation(world, config)
        for vehicle in vehicles:
            env.add_vehicle(vehicle)          # R4 owns the assignment
        for order in orders:
            env.add_order(order)
        return env, None

    from src.search.heuristics import make_heuristic
    from src.sim.agent_planner import AgentPlanner

    env = DeliverySimEnvironment(world, config)
    hook = None
    if mode == "full":
        from src.coordination.trigger import ThresholdTrigger
        from src.sim.coordination_hook import TriggerCoordination

        hook = TriggerCoordination(
            ThresholdTrigger(tau=float(coord_cfg["tau"])),
            rate_limit_s=float(coord_cfg["rate_limit_s"]),
            initial_credits=float(coord_cfg["initial_credits"]),
        )
    planner = AgentPlanner(
        env, mode=mode, heuristic=make_heuristic(world.graph()), coordination=hook,
        unreachable=str(planner_cfg["unreachable"]),
        min_gain_s=float(planner_cfg["min_gain_s"]),
        min_gain_frac=float(planner_cfg["min_gain_frac"]),
    )
    for order in orders:
        env.add_order(order)
    # TODO(R4): independent/full could take R4's contract-net assignment instead.
    assignment = assign_orders(vehicles, orders)
    for vehicle in vehicles:
        planner.register(vehicle, assignment[vehicle.id])   # sets remaining_route
        env.add_vehicle(vehicle, assignment[vehicle.id])
    # Every order must end delivered or failed: one no vehicle got (only
    # possible with no vehicles) fails as no_vehicle right after sim_start.
    given = {o.id for mine in assignment.values() for o in mine}
    env.unassigned = [o for o in orders if o.id not in given]
    env.add_finisher(planner.finish)          # replan_summary before sim_complete
    return env, planner


def run_from_config(config: Dict[str, Any], world=None) -> str:
    """Build the world, vehicles and orders from a config and run. Returns the log path."""
    env, _ = build_simulation(config, world)
    env.run(float(config.get("duration_seconds", DEFAULT_DURATION_S)))
    return env.log_path


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Run headless delivery simulation.")
    parser.add_argument("--config", default="configs/sim_default.yaml", help="Path to config")
    parser.add_argument(
        "--profile", type=int, nargs="?", const=20, default=0, metavar="N",
        help="Profile the run and print the N slowest functions (default 20)",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if args.profile:
        import cProfile
        import pstats

        profiler = cProfile.Profile()
        log_path = profiler.runcall(run_from_config, config)
        pstats.Stats(profiler).sort_stats("cumulative").print_stats(args.profile)
    else:
        log_path = run_from_config(config)
    print(f"Simulation completed. Log saved to: {log_path}")


if __name__ == "__main__":
    main()
