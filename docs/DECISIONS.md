# Project Architecture Decisions

Defaults taken from the project plan; change a line here if the team decides otherwise.

* **D1 Map region and size:** Secunderabad, 2,377 nodes; 6,077 raw edges, 6,056 after keeping the fastest parallel edge.
* **D2 Fleet and depots:** 10-20 vehicles, 2-3 depots, identical vehicles first, capacity in abstract units.
* **D3 Orders:** 100-300 per scenario, 3 priority tiers, time windows from Amazon distributions, dynamic order arrivals.
* **D4 Simulated period:** 4-hour window covering morning peak (06:00 to 10:00 AM); headless execution at >=200x real-time speed.
* **D5 Definition of a conflict:** Fleet-slot capacity per road class (Residential: 1, Secondary: 2, Primary: 3). Conflict = predicted concurrent vehicles above capacity.
* **D6 Tau and Delta-T:** Delta-t = arrival time gap triggering resource clash; Tau = time horizon justifying active negotiation.
* **D7 Message counting:** Point-to-point message = 1 count; broadcast to m agents = m count.
* **D8 New-order allocation:** Contract-net protocol for decentralized execution; re-optimization for centralized mode.
* **D9 CPU time:** Evaluated using `time.process_time()` across replanning cycles on single thread.
* **D10 Agent Information:** Shared real-time road-status feed (closures and spikes) plus predicted weights.
* **D11 Disruption process:** Poisson arrivals (lambda in {0, 2, 5, 10, 20} / hr); 40% traffic spikes, 30% closures, 30% new orders. Closures never disconnect pending destinations.
* **D12 Metric definitions:** Evaluated as defined in Section 7 of the project specification.
* **Traversal Policy:** Traversal time is fixed when a vehicle enters an edge (mid-edge disruptions affect subsequent edge entries).
* **Clock Alignment:** Standardized all simulation event logging and disruption checking to use absolute time in seconds from midnight ($t = \text{start\_time\_s} + \text{env.now}$).
* **Closure Connectivity Guard:** Deferred to Week 3 (TODO(R1)).
* **Travel Time:** edge time = R2 `free_flow_time` x R6 traffic multiplier x active spike multipliers; infinite while the road is closed (closures/spikes active for start <= t < expiry). Free-flow only when R6's model is unavailable.
* **Time Windows:** Absolute seconds from midnight. A vehicle arriving early waits until max(tw_start, release_time); arriving after tw_end fails the order (`time_window_missed`); service takes `service_time`.
* **Directed Edge Conflicts:** Edges are directed `(u, v)`; `A->B` and `B->A` do not conflict with each other.
* **Ongoing Conflict Triggering:** A conflict that has already started ($\text{TTC} < 0$) still triggers negotiation under `ThresholdTrigger`.
* **Negotiation Result Counting (D7):** The negotiation result is sent to every involved vehicle and counted once per recipient (`3m` total negotiation messages for `m` vehicles).
* **R5 Metric Comparability:** Results from the updated directed-edge, capacity-overload, and delta-t conflict definition are not directly comparable to R8 results produced with the earlier conflict detector; rerun both conditions with the same definition before comparing.
* **D5 Road-Class Capacity Mapping:** Default edge capacities map by road class (`residential`: 1, `secondary`: 2, `primary`: 3) via `build_edge_capacities_from_classes()`, while `trunk`, `tertiary`, `living_street`, `unclassified`, and `motorway` roads default to capacity 1 until the team decides otherwise.
* **Sequential Conflict Resolution & Detour Valuation:** When multiple edge conflicts exist, `resolve_conflicts_sequentially()` resolves triggered conflicts in earliest time-to-conflict order (`(time_to_conflict(t_now), edge)`). Contesting vehicles bid `priority_weight * detour_extra_time` computed via `compute_real_detour_extra_time()`, and losing vehicles yield by shifting their `start_time` forward until after `overlap_end` (with deterministic lowest-`vehicle_id` fallback if no bidder can afford the clearing price).
* **Negotiation Rate Limiter:** `NegotiationRateLimiter(cooldown_s=60.0)` enforces a 60.0 s cooldown keyed by `(edge, frozenset(vehicles))` so congested edges do not trigger repeated back-to-back auctions for the same vehicle group.
* **Simulator Integration Status:** `resolver.py`, `NegotiationRateLimiter`, `route_etas_to_vehicle_route`, and `build_edge_capacities_from_classes` are not called by `src/sim` yet; `full` mode uses `TriggerCoordination` in `src/sim/coordination_hook.py` with all roads at capacity 1, so the D5 class capacities are not in effect in any simulator run.
* **Freeze $(\tau, \Delta t)$ operating point via disjoint pilot sweep:** Selected `tau = 300.0` s and `delta_t = 20.0` s via `select_operating_point()` on pilot seeds `[101, 102, 103, 104, 105]` (`pilot_mean_unresolved_conflicts = 0.2`, `pilot_mean_messages = 36.6`) prior to evaluating on disjoint test seeds `[201, 202, 203, 204, 205, 206, 207, 208, 209, 210]` (`mean_unresolved_conflicts = 1.0`, `mean_messages = 34.5`, `mean_total_delay_s = 79.40` s), enforced by `verify_seed_disjointness()`.