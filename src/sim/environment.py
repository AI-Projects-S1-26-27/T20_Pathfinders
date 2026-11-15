"""SimPy delivery environment: vehicles drive routes, serve orders, log events.

All times are absolute seconds from midnight: t = start_time_s + env.now.
"""
import os
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
import simpy

from src.sim.events import DISRUPTION_DEFAULTS, EventGenerator, check_disruption_params
from src.sim.logger import SimulationLogger
from src.sim.models import Event, Order, Vehicle

BLOCKED_POLL_S = 60.0
DEFAULT_START_TIME_S = 21600.0     # 06:00
DEFAULT_DURATION_S = 14400.0       # 4 hours


class DeliverySimEnvironment:
    def __init__(self, world, config: Dict[str, Any]):
        self.world = world
        self.config = config
        self.env = simpy.Environment()
        self.run_id = str(config.get("run_id", "sim"))
        self.mode = str(config.get("mode", "independent"))
        self.scenario = str(config.get("scenario", "default"))
        self.seed = int(config.get("seed", 42))
        self.disruption_rate = float(config.get("disruption_rate_per_hour", 0.0))
        self.start_time_s = float(config.get("start_time_s", DEFAULT_START_TIME_S))
        self.disruption_params = self._disruption_params(config.get("disruptions"))
        self.rng = np.random.default_rng(self.seed)
        self.log_path = config.get("log_path") or os.path.join(
            "logs", f"{self.run_id}.jsonl"
        )
        self.logger = SimulationLogger(
            self.log_path, self.run_id, self.mode, self.seed,
            self.scenario, self.disruption_rate,
        )
        self.vehicles: Dict[str, Vehicle] = {}
        self.orders: List[Order] = []
        self.subscribers: List[Callable[[Event], None]] = []
        self.finishers: List[Callable[[], Any]] = []
        # vehicle id -> its undelivered orders (same list the vehicle process edits)
        self._pending: Dict[str, List[Order]] = {}
        # W5: True = a vehicle that finishes its route waits for new orders
        # (assign_order) instead of ending; set by run.py for contract-net mode.
        self.idle_wait = False
        self._wakeups: Dict[str, simpy.Event] = {}
        # vehicle id -> absolute time it reaches the end of its current road
        self.free_at: Dict[str, float] = {}
        self.n_delivered = 0
        self.n_failed = 0

    @staticmethod
    def _disruption_params(block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """D11 settings from the optional `disruptions:` config block."""
        block = dict(block or {})
        unknown = set(block) - set(DISRUPTION_DEFAULTS)
        if unknown:   # a typo must not silently fall back to the default
            raise ValueError(
                f"unknown keys in disruptions config: {sorted(unknown)}; "
                f"allowed: {sorted(DISRUPTION_DEFAULTS)}"
            )
        params = {**DISRUPTION_DEFAULTS, **block}
        params["max_closure_attempts"] = int(params["max_closure_attempts"])
        for name in ("min_duration_s", "max_duration_s", "min_spike", "max_spike"):
            params[name] = float(params[name])
        check_disruption_params(**params)     # fail at setup, not mid-run
        return params

    def pending_targets(self) -> List[Tuple[Any, List[Any]]]:
        """(source, pending order nodes) per vehicle, for the closure guard.

        Source is the node the vehicle is at, or the end of the road it is on
        (traversal is fixed at edge entry, so it will get there). Subclasses
        that drive vehicles differently (e.g. R4's AllocationSimulation)
        should override this.
        """
        targets = []
        for vid, pending in self._pending.items():
            if not pending:
                continue
            vehicle = self.vehicles[vid]
            source = (
                vehicle.current_edge[1] if vehicle.current_edge else vehicle.current_node
            )
            targets.append((source, [o.node for o in pending]))
        return targets

    # ------------------------------------------------------------------
    # Clock, logging, events
    # ------------------------------------------------------------------
    def get_current_time(self) -> float:
        return self.start_time_s + self.env.now

    def log_event(self, event_type: str, payload: dict, agent_id: str = "env") -> None:
        self.logger.log(event_type, self.get_current_time(), payload, agent_id=agent_id)

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        """Register a listener for world events (disruption, new_order)."""
        self.subscribers.append(callback)

    def add_finisher(self, callback: Callable[[], Any]) -> None:
        """Run callback after the clock stops, before sim_complete is logged
        (e.g. R7's planner.finish), so sim_complete stays the last row."""
        self.finishers.append(callback)

    def handle_event(self, event: Event) -> None:
        self.logger.log(event.event_type, event.t, event.payload)
        for callback in self.subscribers:
            callback(event)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def add_order(self, order: Order) -> None:
        """Register an order (R3 hand-off for dynamic orders)."""
        if order not in self.orders:
            self.orders.append(order)

    def add_vehicle(self, vehicle: Vehicle, assigned_orders: List[Order]) -> None:
        self.vehicles[vehicle.id] = vehicle
        for order in assigned_orders:
            self.add_order(order)
        self.env.process(self._vehicle_process(vehicle, list(assigned_orders)))

    def assign_order(self, vehicle_id: str, order: Order) -> None:
        """Give a vehicle one more order mid-run (R4's contract-net award).

        The caller sets the vehicle's new route first (R7's planner); this
        adds the order to the vehicle's pending list and wakes it if idle.
        """
        self.add_order(order)
        pending = self._pending.setdefault(vehicle_id, [])
        if order not in pending:
            pending.append(order)
        wake = self._wakeups.pop(vehicle_id, None)
        if wake is not None and not wake.triggered:
            wake.succeed()

    # ------------------------------------------------------------------
    # Vehicle behaviour
    # ------------------------------------------------------------------
    def _fail(self, vehicle: Optional[Vehicle], order: Order, reason: str) -> None:
        """Log order_failed; vehicle None = no vehicle had it (e.g. no bid)."""
        self.n_failed += 1
        self.log_event(
            "order_failed",
            {"order_id": order.id, "node": order.node, "reason": reason},
            agent_id=vehicle.id if vehicle is not None else "env",
        )

    def _serve_node(self, vehicle: Vehicle, pending: List[Order], deferred: Set[str]):
        """Serve every pending order at the vehicle's current node."""
        for order in [o for o in pending if o.node == vehicle.current_node]:
            if order not in pending:      # taken away while we served another one
                continue
            now = self.get_current_time()
            if now > order.tw_end:
                self._fail(vehicle, order, "time_window_missed")
                pending.remove(order)
                continue
            ready_t = max(order.tw_start, order.release_time)
            if ready_t > order.tw_end:
                self._fail(vehicle, order, "invalid_time_window")
                pending.remove(order)
                continue
            if vehicle.load + order.demand > vehicle.capacity:
                self.log_event(
                    "capacity_blocked",
                    {
                        "order_id": order.id,
                        "demand": order.demand,
                        "load": vehicle.load,
                        "capacity": vehicle.capacity,
                    },
                    agent_id=vehicle.id,
                )
                deferred.add(order.id)    # retried if the route revisits this node
                continue
            if ready_t > now:             # early: wait for the window / release
                yield self.env.timeout(ready_t - now)
            yield self.env.timeout(order.service_time)
            if order not in pending:      # taken away during service: not ours now
                continue
            vehicle.load += order.demand
            self.n_delivered += 1
            self.log_event(
                "order_delivered",
                {"order_id": order.id, "node": order.node, "demand": order.demand},
                agent_id=vehicle.id,
            )
            pending.remove(order)
            deferred.discard(order.id)

    def _drive_route(self, vehicle: Vehicle, pending: List[Order], deferred: Set[str]):
        """Drive remaining_route edge by edge, serving orders at each node."""
        while vehicle.remaining_route:
            route = vehicle.remaining_route
            v = route.pop(0)
            u = vehicle.current_node
            if v == u:                    # route lists the current node: skip it
                continue
            key = self.world.edge_key(u, v)

            # Wait at a closed road; but if a planner replaces the route while
            # we wait (R7 replans around the closure), follow the new route.
            rerouted = False
            while self.world.is_blocked(u, v, self.get_current_time(), key):
                self.log_event("blocked_wait", {"edge": [u, v, key]}, agent_id=vehicle.id)
                yield self.env.timeout(BLOCKED_POLL_S)
                if vehicle.remaining_route is not route:
                    rerouted = True
                    break
            if rerouted:
                continue

            travel_s = self.world.edge_time(u, v, self.get_current_time(), key)
            vehicle.current_edge = (u, v, key)
            self.free_at[vehicle.id] = self.get_current_time() + travel_s
            self.log_event(
                "edge_enter",
                {"edge": [u, v, key], "expected_duration": travel_s},
                agent_id=vehicle.id,
            )
            yield self.env.timeout(travel_s)
            vehicle.current_node = v
            vehicle.current_edge = None
            self.log_event("edge_exit", {"node": v}, agent_id=vehicle.id)

            yield from self._serve_node(vehicle, pending, deferred)

    def _vehicle_process(self, vehicle: Vehicle, assigned_orders: List[Order]):
        pending = self._pending.setdefault(vehicle.id, [])
        pending.extend(o for o in assigned_orders if o not in pending)
        deferred: Set[str] = set()

        while True:
            # Orders at the current node are served before driving.
            yield from self._serve_node(vehicle, pending, deferred)
            yield from self._drive_route(vehicle, pending, deferred)

            # End of route: whatever is left could not be delivered.
            # Load never decreases yet (no depot unloading), so a deferred order
            # can never succeed on a retry. TODO(R4/R5): unload at depot.
            for order in list(pending):
                reason = (
                    "capacity_exceeded_unservable" if order.id in deferred else "not_on_route"
                )
                self._fail(vehicle, order, reason)
            pending.clear()               # nothing left to protect from closures
            if not self.idle_wait:
                return
            wake = self.env.event()       # idle until assign_order gives new work
            self._wakeups[vehicle.id] = wake
            yield wake

    def _fail_unfinished(self) -> None:
        """At the horizon, orders still pending on a vehicle are failed, so
        every order ends delivered or failed (same reason as R4's runner).
        Without this, a vehicle still driving at the end loses its orders."""
        for vid, pending in self._pending.items():
            for order in list(pending):
                self._fail(self.vehicles[vid], order, "horizon_exceeded")
            pending.clear()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(self, duration_seconds: Optional[float] = None) -> None:
        if duration_seconds is None:
            duration_seconds = float(self.config.get("duration_seconds", DEFAULT_DURATION_S))
        self.log_event("sim_start", {
            "start_time_s": self.start_time_s,
            "duration_seconds": duration_seconds,
            "n_vehicles": len(self.vehicles),
            "n_orders": len(self.orders),
            # was R6's forecast available to planners (D10)? false = free-flow
            "predictor": getattr(self.world, "predictor", None) is not None,
        })
        if self.disruption_rate > 0:
            gen = EventGenerator(
                self.env, self.rng, self.world, self.handle_event,
                start_time_s=self.start_time_s,
                pending_targets=self.pending_targets,
                log_fn=self.logger.log,
                **self.disruption_params,
            )
            self.env.process(gen.schedule_disruptions(self.disruption_rate, duration_seconds))
        self.env.run(until=duration_seconds)
        # Only at the configured end: run() may be called in steps (run(5); run(40)).
        if self.env.now >= float(self.config.get("duration_seconds", DEFAULT_DURATION_S)):
            self._fail_unfinished()
        for callback in self.finishers:
            callback()
        self.log_event("sim_complete", {
            "delivered": self.n_delivered,
            "failed": self.n_failed,
        })
        self.logger.close()
