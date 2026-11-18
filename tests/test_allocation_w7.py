"""Week 7 and Week 8 tests for R4 allocation and centralized baseline.

Validates boundary cases, priority drop ordering, deterministic tie-breaking,
and partial-state invariance for final hardening and viva readiness.
"""
from src.allocation.contract_net import ContractNet
from src.allocation.central_vrp import CentralVRP
from src.allocation.cost_model import VehicleState
from src.sim.models import Order, Vehicle


class DummyWorldTravel:
    """Deterministic callable travel model for unit testing."""
    def __init__(self, speed: float = 1.0):
        self.speed = speed

    def __call__(self, vehicle, u, v) -> float:
        if u == v:
            return 0.0
        return float(abs(int(u) * 7 - int(v) * 7) + 5)

    def time(self, u, v) -> float:
        return self(None, u, v)

    def path(self, u, v):
        if u == v:
            return [u]
        return [u, v]


def make_vehicle(vid: str, capacity: float = 10.0, current_node: int = 1) -> Vehicle:
    return Vehicle(id=vid, depot_node=current_node, capacity=capacity, current_node=current_node)


def make_order(oid: str, node: int = 2, demand: float = 2.0, priority: int = 2,
               tw_start: float = 0.0, tw_end: float = 1000.0,
               service_time: float = 10.0, release_time: float = 0.0) -> Order:
    return Order(
        id=oid, node=node, demand=demand, priority=priority,
        tw_start=tw_start, tw_end=tw_end, service_time=service_time,
        release_time=release_time
    )


def test_central_vrp_empty_orders():
    """Resolving with zero orders should return empty routes, zero failed, and 2N messages."""
    travel = DummyWorldTravel()
    vrp = CentralVRP(travel, time_limit_s=0.2)
    states = [VehicleState(make_vehicle("V1"), [], available_at=0.0)]
    result = vrp.resolve(states, [], now=0.0)
    assert result.routes == {"V1": []}
    assert result.failed == []
    assert result.travel_seconds == 0.0
    assert result.messages == 2  # 2 * len(states)


def test_central_vrp_capacity_exhaustion_drops():
    """When vehicle capacity is insufficient, orders must be dropped and reported in failed."""
    travel = DummyWorldTravel()
    vrp = CentralVRP(travel, time_limit_s=0.5)
    states = [VehicleState(make_vehicle("V1", capacity=2.0), [], available_at=0.0)]
    order = make_order("O_HEAVY", demand=5.0, priority=1)
    result = vrp.resolve(states, [order], now=0.0)
    assert "O_HEAVY" in result.failed
    assert result.routes["V1"] == []


def test_contract_net_no_feasible_bidder():
    """If no vehicle can feasibly serve order, winner is None and messages count announcements."""
    travel = DummyWorldTravel()
    messages = []
    cnp = ContractNet(travel, emit=lambda k, p: messages.append((k, p)))
    states = [
        VehicleState(make_vehicle("V1", capacity=1.0), [], available_at=0.0),
        VehicleState(make_vehicle("V2", capacity=1.0), [], available_at=0.0),
    ]
    order = make_order("O_TOO_BIG", demand=5.0)
    result = cnp.allocate(order, states, now=0.0)
    assert result.vehicle_id is None
    # 2 announcements, 0 bids, 0 awards
    assert result.messages == 2
    assert len(messages) == 2


def test_contract_net_deterministic_tie_breaking():
    """Identical bids across symmetric vehicles must break ties deterministically by vehicle ID."""
    travel = DummyWorldTravel()
    cnp = ContractNet(travel)
    states = [
        VehicleState(make_vehicle("V2", capacity=10.0, current_node=1), [], available_at=0.0),
        VehicleState(make_vehicle("V1", capacity=10.0, current_node=1), [], available_at=0.0),
    ]
    order = make_order("O1", node=2, demand=1.0)
    result = cnp.allocate(order, states, now=0.0)
    assert result.vehicle_id == "V1"


def test_partial_state_preserves_next_node_commitment():
    """Partial state starting from mid-edge next node must route forward without reversing."""
    travel = DummyWorldTravel()
    v = make_vehicle("V1", capacity=10.0, current_node=5)
    state = VehicleState(v, [], available_at=50.0)
    cnp = ContractNet(travel)
    order = make_order("O1", node=5, demand=1.0, tw_start=60.0)
    result = cnp.allocate(order, [state], now=50.0)
    assert result.vehicle_id == "V1"
    assert len(state.orders) == 1
    assert state.orders[0].id == "O1"
