"""W5: R4's contract-net hands out orders in independent / full mode (D8).

R1 glue between three parts, none of which changes its own job:

* R4's ``ContractNet`` (src/allocation/contract_net.py) runs the auction: each
  vehicle bids the cheapest feasible insertion of the order into its stop
  sequence (travel time from R4's WorldTravel snapshot, capacity, windows);
  the lowest bid wins.
* R7's ``AgentPlanner.set_stops`` replans the winner through its new stops.
* R1's environment (``assign_order``) gives the winner the order and wakes it
  if it was idle.

When orders are known:

* orders released at or before the start are auctioned before the run, one
  by one in (release_time, id) order;
* every later order is auctioned at its release_time, from where each vehicle
  really is then (end of its current road, at the time it gets there). No
  vehicle knows an order before it is released.

R7's ``reassign`` hook is ``ContractNetDispatcher.reassign``: when a closure
cuts off a vehicle's stop, its orders are re-auctioned among the other vehicles
before being failed.

Logged: R4's ``allocation`` message rows (announcement / bid / award, D7) and
one ``allocation_complete`` row per auction, the same rows R4's own runner logs.
An order no vehicle can take fails with reason ``no_feasible_bid``.
"""
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional

from src.allocation.contract_net import ContractNet
from src.allocation.cost_model import VehicleState
from src.allocation.world_adapter import WorldTravel


class ContractNetDispatcher:
    def __init__(self, env, planner, mode: str):
        self.env = env
        self.planner = planner
        self.mode = mode
        self.sequence: Dict[str, List[Any]] = {}   # vehicle id -> its orders, R4's order
        self.vehicles: Dict[str, Any] = {}
        self.handed: set = set()                    # ids given to a vehicle via the env
        self.n_auctions = 0
        self.n_messages = 0
        self.cpu_seconds = 0.0
        self._travel = None
        self._travel_key = None
        self.n_snapshots = 0

    # -- setup (before env.run) -----------------------------------------
    def setup(self, vehicles: List[Any], orders: List[Any]) -> None:
        """Register every vehicle (idle, no stops) with R7's planner and the
        environment, and schedule the auctions: the orders known at the start
        at t0, every later order at its release time. Auctions run inside the
        simulation, so their log rows come after sim_start."""
        start = self.env.get_current_time()
        self.vehicles = {v.id: v for v in vehicles}
        self.sequence = {v.id: [] for v in vehicles}
        for order in orders:
            self.env.add_order(order)
        for v in vehicles:
            self.planner.register(v, [], stops=[])   # hand-written routes are not used
            self.env.add_vehicle(v, [])
        ordered = sorted(orders, key=lambda o: (o.release_time, o.id))
        self.env.env.process(self._opening([o for o in ordered if o.release_time <= start]))
        for order in ordered:
            if order.release_time > start:
                self.env.env.process(self._release(order, start))

    # -- during the run -------------------------------------------------
    def _opening(self, orders: List[Any]):
        """t0: auction the known orders one by one on one snapshot, then plan
        each vehicle once through everything it won."""
        yield self.env.env.timeout(0)         # after the vehicles' processes started
        t = self.env.get_current_time()
        travel = self.travel_at(t)
        for order in orders:
            if self._auction(order, t, travel) is None:
                self.env._fail(None, order, "no_feasible_bid")
        for vid in sorted(self.vehicles):
            mine = list(self.sequence[vid])
            if mine:
                for order in mine:
                    self._hand(vid, order)
                self.planner.set_stops(vid, mine, t)

    def _release(self, order, start: float):
        yield self.env.env.timeout(order.release_time - start)
        t = self.env.get_current_time()
        winner = self._auction(order, t, self.travel_at(t))
        if winner is None:
            self.env._fail(None, order, "no_feasible_bid")
            return
        self._award(winner, order, t)

    def reassign(self, vid: str, orders: Iterable[Any], t: float) -> List[Any]:
        """R7's hook: re-auction orders cut off from vehicle vid; returns the
        orders another vehicle took (R7 removes them from vid and logs it)."""
        travel = self.travel_at(t)
        taken = []
        for order in orders:
            self.sequence[vid] = [o for o in self.sequence[vid] if o.id != order.id]
            winner = self._auction(order, t, travel, exclude=vid)
            if winner is not None:
                self._award(winner, order, t)
                taken.append(order)
        return taken

    # -- R4's travel snapshot, rebuilt only when it would differ ---------
    def travel_at(self, t: float) -> WorldTravel:
        """R4's WorldTravel for time t, reusing the last one when it is the same.

        A snapshot depends on t only through (a) the closures and spikes
        active at t and (b) R6's forecast, which changes once per STEP_S
        (free-flow, i.e. constant, without a predictor). Same key = same
        travel times, so its shortest-path cache is kept (profiling: building
        a new snapshot for every released order took ~80% of a 20-vehicle run).
        """
        key = (self._forecast_step(t), self._active_disruptions(t))
        if key != self._travel_key:
            self._travel = WorldTravel(self.env.world, t)
            self._travel_key = key
            self.n_snapshots += 1
        return self._travel

    def _forecast_step(self, t: float) -> int:
        if getattr(self.env.world, "predictor", None) is None:
            return 0
        from src.world.traffic_model import STEP_S
        return int(t // STEP_S)

    def _active_disruptions(self, t: float):
        world = self.env.world
        closed = frozenset(e for e, spans in world.closures.items()
                           if any(s <= t < x for s, x in spans))
        spikes = frozenset((e, m) for e, spans in world.spikes.items()
                           for s, x, m in spans if s <= t < x)
        return closed, spikes

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _stops(orders: List[Any]) -> List[Any]:
        stops = []
        for o in orders:
            if not stops or stops[-1] != o.node:
                stops.append(o.node)
        return stops

    def _snapshot(self, vid: str, t: float) -> VehicleState:
        """Vehicle as R4 must see it: from the end of its current road, free
        when it gets there, with its still-undelivered orders in sequence."""
        vehicle = self.vehicles[vid]
        # Forget orders the vehicle was given and no longer has (delivered,
        # failed or taken away). Orders won but not handed over yet (opening
        # auction) stay.
        held = {o.id for o in self.env._pending.get(vid, [])}
        self.sequence[vid] = [o for o in self.sequence[vid]
                              if o.id in held or o.id not in self.handed]
        if vehicle.current_edge is not None:
            node = vehicle.current_edge[1]
            free = max(t, self.env.free_at.get(vid, t))
        else:
            node, free = vehicle.current_node, t
        snap = replace(vehicle, current_node=node, current_edge=None)
        return VehicleState(snap, list(self.sequence[vid]), free)

    def _auction(self, order, t: float, travel, exclude: Optional[str] = None) -> Optional[str]:
        states = [self._snapshot(vid, t) for vid in sorted(self.vehicles) if vid != exclude]
        result = ContractNet(travel, emit=self._emit).allocate(order, states, t)
        self.n_auctions += 1
        self.n_messages += result.messages
        self.cpu_seconds += result.cpu_seconds
        self.env.log_event("allocation_complete", {
            "cpu_seconds": result.cpu_seconds, "messages": result.messages,
            "mode": self.mode, "order_id": order.id, "winner": result.vehicle_id,
        })
        if result.vehicle_id is None:
            return None
        won = next(s for s in states if s.vehicle.id == result.vehicle_id)
        self.sequence[result.vehicle_id] = list(won.orders)
        return result.vehicle_id

    def _award(self, vid: str, order, t: float) -> None:
        # Order first, so if the new stop is cut off R7's unreachable policy
        # sees it; the vehicle only resumes after this call, on the new route.
        self._hand(vid, order)
        self.planner.set_stops(vid, self.sequence[vid], t)

    def _hand(self, vid: str, order) -> None:
        self.handed.add(order.id)
        self.env.assign_order(vid, order)

    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        agent = payload.get("sender") or payload.get("recipient") or "env"
        self.env.log_event(kind, payload, agent_id=str(agent))
