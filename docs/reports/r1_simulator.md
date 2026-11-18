# R1 report sections: Simulation Core (draft for R8 to merge)

Every number below comes from a committed file or test, named in brackets. Sections follow the report
outline (guide 9.1): a course concepts row, related work, Method, Results (validation of the simulator)
and Limitations. The simulator's results are the other roles' results: this section describes the
testbed they were measured on and shows that it is correct and reproducible. The main sweep is not
described here.

---

## Course concepts (one row of the table)

| Concept | Where in the system | What it does in the experiments |
|---|---|---|
| Discrete-event simulation; Poisson process; reproducible experiments | `src/sim/` (R1): `environment.py`, `events.py`, `world.py`, `run.py`, `launcher.py`, `check_log.py` | Every experiment runs in it. It moves vehicles over the real road graph, injects Poisson disruptions, hands events to the planners of the condition under test (centralized, independent, full) and writes one JSONL log per run, from which every metric is computed. Same config and seed give the same log. |

## Related work (simulation; about three sentences)

The simulator is a process-oriented discrete-event simulation in the sense of Law (2015) and Banks et
al. (2010), written with SimPy, in which time jumps from event to event instead of advancing in fixed
steps. Disruptions arrive as a homogeneous Poisson process, the standard model for independent random
arrivals. Dynamic vehicle routing studies of the kind surveyed by Pillac et al. (2013) evaluate policies
by replaying the same stream of dynamic events against each policy; our contribution here is not a
simulation method but a seeded, logged, checkable testbed in which the three coordination conditions see
identical disruptions on a real road network.

References (to verify before submission; Pillac et al. was verified by R4):
- Banks, J., Carson, J. S., Nelson, B. L., & Nicol, D. M. (2010). *Discrete-Event System Simulation*
  (5th ed.). Pearson.
- Law, A. M. (2015). *Simulation Modeling and Analysis* (5th ed.). McGraw-Hill.
- Pillac, V., Gendreau, M., Guéret, C., & Medaglia, A. L. (2013). A review of dynamic vehicle routing
  problems. *European Journal of Operational Research*, 225(1), 1-11.
  https://doi.org/10.1016/j.ejor.2012.08.015
- SimPy documentation. https://simpy.readthedocs.io

---

## Method: the simulation core

### 1. World, clock and travel time

The world is R2's Secunderabad road graph: 2,377 nodes and 6,056 directed roads after keeping the fastest
of parallel roads [`src/world/graph_loader.py`; `docs/DECISIONS.md` D1]. Every time in the system is
absolute seconds from midnight, `t = start_time_s + env.now`; a run covers 06:00-10:00 (D4). The time to
drive a road entered at time `t` is

    edge_time(u, v, t) = free_flow(u, v) x traffic(u, v, t) x spikes(u, v, t),   or infinity if closed,

where `free_flow` comes from R2's graph, `traffic` from R6's model (METR-LA test-period speeds; 1.0 when
the model is not loaded) and `spikes` is the product of the disruption spikes active on the road. The time
is fixed when a vehicle enters a road; a disruption that starts while it is on the road affects only later
roads (DECISIONS, Traversal Policy). R6's traffic table has one column per 5-minute step: step 0 is sim
00:00 and reads Los Angeles one hour earlier (rush hours shifted +1 h, R6's assumption). `traffic_day`
selects the test-period day (default 5 June 2012, or one weekday per seed) [`src/sim/run.py`].

### 2. Vehicles, orders and disruptions

Each vehicle is a SimPy process that drives its route road by road. At each node it serves its pending
orders there: an order whose window has passed fails (`time_window_missed`); one that would exceed capacity
is deferred (`capacity_blocked`); otherwise the vehicle waits for the window to open, serves for the order's
service time and logs `order_delivered`. A vehicle whose next road is closed waits and re-checks every 60 s
until the road reopens or its planner gives it a new route. Every order ends delivered or failed with one of
ten logged reasons, including `horizon_exceeded` for orders still pending at 10:00 [`docs/log_schema.md`].

Disruptions follow D11: Poisson arrivals at rate lambda per hour, each a traffic spike (40%, multiplier
1.2-3.0), a road closure (30%) or a new-order announcement (30%), lasting 10-30 min. A closure guard redraws
any closure that would cut a vehicle off from a destination it can reach now, and skips the closure after
20 failed draws (`closure_skipped`). Scenarios are R3's: small (10 vehicles, 100 orders, 2 depots), medium
(15, 200, 3) and large (20, 300, 3), with about 29% of orders released during the run (20-34%) [the 90
files in `configs/scenarios/`].

### 3. The three conditions

`run.py` builds the condition named by the config's `mode`. All three share the world, the scenario and the
seeded disruption stream, so they face identical events.

| mode | who allocates orders | who routes vehicles |
|---|---|---|
| `centralized` | R4's OR-Tools re-solve of the whole fleet on every event | R4's re-solve |
| `independent` | R4's contract-net auction (D8) when an order is released | each vehicle replans locally with R7's D* Lite on every disruption |
| `full` | as independent | as independent, plus R5's conflict trigger and Vickrey auction after every replan |

In `independent` and `full`, R1's dispatcher [`src/sim/dispatch.py`] auctions the orders known at 06:00 at
the start and every later order at its release time, from each vehicle's true position; R7's planner then
replans the winner, and orders cut off by a closure are re-auctioned before they fail.

### 4. Logging, checking and running experiments

Every run writes one JSONL log: one row per event (vehicle on a road, delivery, failure, disruption, replan,
auction message), each with the same nine keys, among them run id, mode, seed, scenario, rate and time
[`docs/log_schema.md`]. All metrics are computed from these logs. `check_log` [`src/sim/check_log.py`]
rejects a log that is incomplete or inconsistent: wrong keys, time going backwards, an order ending twice or
never, counts in `sim_complete` that disagree with the rows, a vehicle entering a road before leaving the
last, or a delivery without its node and demand.

The sweep launcher [`src/sim/launcher.py`] runs every combination of scenarios, modes, seeds, rates and
predictor settings in parallel worker processes. A run is fully determined by its config and seed, never by
the worker or order of completion. Runs are resumable, every finished log is checked with `check_log`, and
every result row records the git commit it ran on. `require_traffic: true` makes a run fail instead of
silently running at free-flow speed when R6's traffic data is missing.

---

## Results: validation of the simulator (table T0)

| check | result | source |
|---|---|---|
| Same config and seed give the same log, all three modes (free-flow) | identical logs, in one process and across processes with different `PYTHONHASHSEED` | `tests/test_determinism.py` |
| Same, with R6's traffic model and predictor (independent, full; `small_uniform_s7`, 20 disruptions/h) | identical logs across two processes (4,698 and 4,789 rows) | `tests/test_determinism.py::test_same_log_with_traffic_and_predictor` |
| Traffic clock (METR-LA) | step 0 = sim 00:00; morning rush at sim 09-10 (strong roads x3.0 at 09h vs x1.1 at 03h); 15-min forecast at 08:00 tracks the table at 08:15 (MAE 0.31) rather than 03:15 (0.77) | `tests/test_traffic_clock.py` |
| Logs are complete and consistent | `check_log` runs on every launcher run; R3's audit of a 42-run pilot found 0 rule violations | `src/sim/check_log.py`; `reports/audit_sweep_pilot.md` (R3) |
| Run time, large scenario (20 vehicles, 300 orders, 10/h, free-flow) | independent 20 s, full 23 s, all 300 delivered; centralized not finished after 20 min | `src/sim/README.md`, Profile |
| Test suite (with METR-LA data) | 1,724 passed, 3 skipped, 0 failed | `pytest --ignore=tests/test_prediction.py`, 18 Nov, branch `r1/week-7-integration` after merging main |

Profiling brought the independent run from 90 s to 38 s (under the profiler) by reusing R4's travel-time
snapshot while the world is unchanged and caching per-road look-ups. Centralized runs are slow because R4's
solver re-solves the fleet on every event with a 2 s budget; this limits how many centralized runs a sweep
can afford (Limitations).

---

## Limitations (simulation core)

- **Dynamic orders.** D11's `new_order` events (30% of disruption events) create no order: generating one
  needs R3's Amazon profile, which is not in the repository. Dynamic orders come from the scenarios'
  release times instead (about 29% of orders). In independent and full mode these events change nothing; in
  centralized mode each triggers a solver re-solve. The reported disruption rates include them
  [`docs/DECISIONS.md`, Dynamic Orders in Simulated Runs].
- **Road rules.** R3's road-class and zone rules are not used in planning: no scenario restricts a road and
  the planners take no rules. They are applied in R3's audit of the logs instead [DECISIONS, KR Road Rules].
- **No depot unloading.** A vehicle's load never decreases, so an order deferred for capacity can never be
  served later.
- **Centralized cost.** The centralized baseline takes over 20 minutes per run at 20 vehicles, so it can
  only be run on small scenarios or few seeds.
- **Traffic.** Travel times use Los Angeles highway patterns mapped onto Hyderabad roads by road class (R6's
  assumption). Runs on a machine without the METR-LA file fall back to free-flow times unless
  `require_traffic` is set; the determinism and clock tests are skipped in CI for the same reason.
- **Determinism scope.** Centralized runs with the predictor on have not been checked for identical logs; R4's
  solver stops on a wall-clock budget, so its plans may depend on machine load.
