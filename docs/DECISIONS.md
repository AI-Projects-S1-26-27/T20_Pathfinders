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
* **Closed Roads:** A vehicle whose next road is closed waits at its node and re-checks every 60 s (`blocked_wait`) until the road reopens. Re-routing around closures is R7's job (TODO(R7)).
* **Closure Connectivity Guard (D11):** A closure is only placed if every vehicle can still reach each pending order node it could reach just before. Otherwise another random road is drawn, up to `max_closure_attempts` (default 20); if all fail the closure is skipped and logged as `closure_skipped`. Destinations already unreachable do not block closures. A vehicle on a road counts as being at the road's end. Spikes are not guarded.
* **Disruption Parameters:** Closure/spike duration (default 600-1800 s) and spike multiplier (default x1.2-3.0, must be >= 1.0) are set in the `disruptions:` config block.
* **Travel Time:** edge time = R2 `free_flow_time` x R6 traffic multiplier x active spike multipliers; infinite while the road is closed (closures/spikes active for start <= t < expiry). Free-flow only when R6's model is unavailable.
* **Time Windows:** Absolute seconds from midnight. A vehicle arriving early waits until max(tw_start, release_time); arriving after tw_end fails the order (`time_window_missed`); service takes `service_time`.
* **Replan Hysteresis (R7, frozen W7):** a vehicle switches to a repaired route only if old cost - new cost >= max(10 s, 5% of the old cost); a blocked old route always switches (`planner: min_gain_s: 10.0, min_gain_frac: 0.05`, the defaults in `src/sim/run.py`). Pilot: `small`/`medium` x `uniform`/`clustered`/`hotspot`, pilot seeds 7-9, 10 and 20 disruptions/h, independent, 36 runs per setting, run twice. (1) With R4's contract-net dispatch (PR #38, merged locally): 0.13 route switches/h without a margin, 0.06-0.07/h for every margin from 5 s/2% to 60 s/20%; all 5,400 orders delivered, mean delivery time identical (2,502 s) in every setting. (2) With `run.py`'s route assignment (all orders on one vehicle): 2.76/h without, 1.15-1.17/h with any margin; deliveries identical. Any margin removes the near-equal flip-flops, about half of all switches, at no cost to deliveries, and results are flat from 5 s to 60 s, so 10 s/5% is frozen. Seeds 7-9 should stay out of the main sweep.
* **Unreachable Stops (R7, W7 fix):** a stop is failed only if the vehicle cannot reach it at all from where it can next decide (end of its current road), with the costs it knows. A stop cut off only from the stop before it (that stop became a dead end) is moved to where it fits, not dropped (`stop_unreachable.reordered`). Weeks 5-6 dropped every stop after a cut-off one; on `medium_clustered_s9` at 20/h this failed about 115 reachable orders and crashed R1's environment (an order being served was removed).
