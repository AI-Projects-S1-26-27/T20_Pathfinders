# Simulation Core (R1, `src/sim`)

Discrete-event simulator (SimPy) for the delivery fleet on R2's real Secunderabad road graph.

## Running

```bash
python -m src.sim.run --config configs/sim_default.yaml
```

Writes `logs/<run_id>.jsonl` (schema: `docs/log_schema.md`). Without R6's data/torch you
will see `Traffic model unavailable, using free-flow times` - this is expected.

## Modes (W4)

The config's `mode` picks the condition; `run.py` builds the matching simulation
(`build_simulation(config, world=None)` returns `(env, planner)`). **The default mode is now
`independent`**: a config without `mode` runs with R7's replanning agents, not fixed routes.

| mode | simulation | who decides routes |
|------|------------|--------------------|
| `centralized` | R4's `AllocationSimulation` (`src/allocation/sim_runner.py`) | R4 re-solves the whole fleet (OR-Tools) on every event and order arrival. Hand-written `route`s are ignored. |
| `independent` | `DeliverySimEnvironment` + R7's `AgentPlanner` | each vehicle replans locally with D* Lite on every disruption; never negotiates. |
| `full` | as `independent` + R5's `TriggerCoordination` | after every replan, R5's trigger (`ThresholdTrigger(tau)`) and Vickrey auction. |

Optional config blocks (unknown keys raise, like `disruptions:`):

```yaml
planner:          # independent / full (R7)
  unreachable: fail       # or wait
  min_gain_s: 10.0        # hysteresis
  min_gain_frac: 0.05
coordination:     # full (R5)
  tau: 600.0              # D6: negotiate iff time-to-conflict < tau (s)
  rate_limit_s: 60.0
  initial_credits: 100.0
```

**Who gets which order (independent/full, W5)**: `allocation: contract_net` (default, D8) runs
R4's contract-net (`src/sim/dispatch.py`): orders released by the start are auctioned at t0, every
later order at its `release_time` (no vehicle knows it earlier), each vehicle bidding its cheapest
insertion from where it really is. The winner gets the order (`env.assign_order`) and R7's planner
replans it (`AgentPlanner.set_stops`); idle vehicles wait for work (`env.idle_wait`). Cut-off
stops are re-auctioned through R7's `reassign` hook before failing. Hand-written `route`s are not
used. `allocation: route` keeps W4's rule (first vehicle whose route visits the order) for
hand-made demo configs. R7's `replan_summary` is logged just before `sim_complete`.

**Check-in demo**: one scenario, same seed, every mode, table from the logs:

```bash
python -m src.sim.demo_modes --rate 300             # configs/viz_demo.yaml, 3 modes
python -m src.sim.demo_modes --modes independent full --rate 600   # force replans
python -m src.sim.demo_modes --csv reports/checkin_modes.csv
```

Logs go to `logs/<run_id>_<mode>.jsonl` (R2's replay/multipane viewers read them).
At viz_demo's own rate (10/h) no disruption hits a planned road, so the demo uses `--rate 300`.
Measured here: independent and full take ~5-7 s each at `--rate 300` (30/30 delivered);
**centralized is slow at this rate** (R4 re-solves on every disruption: 44.8 of 240 simulated
minutes after ~10 min), so for a quick live demo add `--modes independent full`.
The demo warns (stderr) if delivered + failed is less than the number of orders released.

Every order ends `order_delivered` or `order_failed` with a reason (`docs/log_schema.md`):
`not_on_route` / `capacity_exceeded_unservable` at the end of a vehicle's route, `unreachable`
(R7: cut off by a closure and not re-auctioned), `no_feasible_bid` (contract-net: no vehicle could
take it, e.g. capacity, or the config has no vehicles), `no_vehicle` (`allocation: route` with no
vehicles, logged right after `sim_start`), `horizon_exceeded` if still pending when the run ends.

## Sweeps (W5)

`python -m src.sim.launcher <sweep.yaml> [--workers N] [--dry-run]` runs every combination of
configs x modes x seeds (x rates x predictor) in parallel worker processes:

```yaml
sweep_id: pilot
configs: [configs/scenarios/large_*_s0.yaml]   # paths or globs
modes: [centralized, independent, full]
seeds: [101, 102, 103]      # not pilot seeds: 7-9 (R7's hysteresis pilot), 9001-9005 (R6's ablation)
rates: [5.0, 10.0]          # optional: disruption_rate_per_hour
predictor: [true, false]    # optional: R6 forecast on/off
overrides: {}               # optional: applied to every config
workers: 4                  # optional, default CPUs - 1
```

- **Seed-controlled**: a run is fully set by its config + seed (`run_id` like
  `large_uniform_s0__full__r10__s101`), never by which worker ran it.
- **Resumable**: a finished run writes `done/<run_id>.json` (summary + config hash) and is skipped
  next launch; edit its config and it reruns. Logs are written as `.jsonl.part` and renamed only on
  success. A crash writes `failed/<run_id>.txt` and is retried on the next launch.
- Output in `runs/<sweep_id>/`: `logs/`, `done/`, `failed/`, `results.csv` (one row per finished run).
- **Code version (W7)**: every result row has the git `commit` it ran on and `dirty` (tracked files
  had uncommitted changes). The launcher warns if the results mix commits (a resumed sweep skips
  finished runs even after a code change) or any run was dirty. Commit before a sweep.

**Predictor** (`predictor: false` in a config) removes R6's forecast, so planners use free-flow
times (the R6 ablation). Default `true` uses it when R6's data is present. `sim_start` logs
whether it was actually on.

**Traffic day (W7)**: `traffic_day: 0` (default, 5 June 2012, as before), a day offset, or
`per_seed` (R6's `day_for_seed(seed)`: a weekday per seed, skipping the day with no data).
`sim_start` logs `traffic` (R6's traffic model loaded) and `traffic_day`.

**Required traffic (W7)**: without `data/raw/metr-la.h5` (or torch / h5py) the world only warns
and every road runs at free-flow speed, so a run finishes normally with no traffic in it.
`require_traffic: true` makes it fail instead; the main sweep config sets it.

**Horizon**: when the run reaches `duration_seconds`, orders still pending on a vehicle are
failed with reason `horizon_exceeded`, so every order ends delivered or failed.

**Profile (W5 target: <= ~60 s per run at 20 vehicles)**, R3's `large_uniform_s0` (20 vehicles,
300 orders, 83 released during the run), 10 disruptions/h, free-flow (no METR-LA), one Windows
laptop:

| mode | wall | delivered | slowest fixed, in order |
|------|------|-----------|-------------------------|
| independent | 20 s | 300/300 | R4 snapshot rebuilt per released order (90 s -> 47 s: reuse while closures, spikes and R6's forecast step are unchanged); `World.edge_key` / `free_flow_time` recomputed ~1M times (47 -> 38 s: cached, the map is fixed); log file reopened per row (kept open, flushed per row) |
| full | 23 s | 300/300 | same |
| centralized | > 20 min | not finished | R4's OR-Tools re-solve per event (227 solves for 11.6 of 240 simulated minutes). R4's code: to raise with R4 |

Under the profiler the independent run is ~38 s; the plain wall time is above.

## Determinism and log checks (W6)

**Same config + seed = same log**, in every mode, apart from CPU / wall-clock timings
(payload keys ending in `cpu_seconds` / `wall_seconds`). `tests/test_determinism.py` checks it
in one process, in two processes with different `PYTHONHASHSEED` (as the launcher's workers
are), and through the launcher's `results.csv`. Checked by hand as well, same hash for every
`PYTHONHASHSEED` tried: viz_demo at `--rate 300` (independent, full), viz_demo at 10/h
(centralized) and R3's `large_uniform_s0` (full, 17,108 rows). If you add randomness, draw it
from the run's seeded generator (`env.rng`, or a `random.Random(seed)` of your own), never from
`random` / `np.random` module functions, and do not let the order of a `set` decide anything.

**With R6's traffic model and predictor (W7).** `test_same_log_with_traffic_and_predictor` runs
R3's `small_uniform_s7` (20 disruptions/h) with `predictor: true` in two processes with different
`PYTHONHASHSEED`, in independent and full mode: the logs are identical. It needs
`data/raw/metr-la.h5` and torch, so it is skipped in CI (no data there); run it on the machine
that runs the sweep. Centralized mode with the predictor is not covered by it.

**Log checker**: is a log complete and consistent?

```bash
python -m src.sim.check_log logs/sim_demo.jsonl
python -m src.sim.check_log runs/pilot/logs                       # every log in a folder
python -m src.sim.check_log logs/x.jsonl --config configs/x.yaml  # + every order accounted
```

Errors (exit 1): not the nine keys in order; run_id/mode/seed/scenario/rate changing; not
exactly one `sim_start` (first) and one `sim_complete`; rows after `sim_complete` (except R4's
`allocation_summary`); time going backwards or outside the run; an order ending twice or failing
with an unknown reason; `sim_complete` counts not matching the rows; a vehicle entering a road
before leaving the last one; with `--config`, a released order that never ended. Event names not
in `docs/log_schema.md` are only warnings. The launcher runs it on every finished run: column
`log_errors` in `results.csv` (details in `done/<run_id>.json`), and lists flagged runs at the end.

## Known open issues (W7)

Bug triage list for the final week: what is known not to work, or is not done, and whose it is.

| # | issue | effect | owner |
|---|-------|--------|-------|
| 1 | **Known limitation (W7, `docs/DECISIONS.md` "Dynamic Orders in Simulated Runs").** `new_order` disruption events (D11) only announce `dyn_N`; no order is created. R3's `generate_order` needs the Amazon profile (`data/profile/`, not in the repo) | 30% of disruption events change nothing in independent/full; in centralized each costs a re-solve. Dynamic orders come from the scenarios' `release_time`s (about 29% of orders) | closed (documented) |
| 2 | Centralized mode re-solves (OR-Tools, 2 s limit per solve, frozen by R4 in W7) on every event | ~1.5-2 min for viz_demo at 10/h; > 20 min at 20 vehicles (profile above), too slow for a full sweep; the main sweep runs centralized on a subset (`configs/sweeps/`) | R4 (accepted as a cost of the baseline) |
| 3 | R4's `allocation_summary` is logged after `sim_complete` (could use `env.add_finisher`) | allowed as an exception in `check_log.py` | R4 |
| 4 | R4's `central_state_report` / `central_plan_broadcast` events are not in `docs/log_schema.md` | `check_log` warns | R4 |
| 5 | No depot unloading: load never decreases (`TODO(R4/R5)` in `environment.py`) | an order blocked by capacity can never be served later | R4/R5 (scope decision) |
| 6 | **Known limitation (W7, `docs/DECISIONS.md` "KR Road Rules in Simulated Runs").** R3's `RoadRules` (KR-3/6) are not built in `run.py`: no config restricts any road and R7's / R4's planners take no rules (`TODO(R7)`, `TODO(R4)` in `src/kr/planner_rules.py`). `kr_check` is now in `docs/log_schema.md` | road-class / zone rules are checked in R3's log audit, not used in planning | closed (documented) |
| 7 | `AgentPlanner.set_stops`: with `unreachable="wait"` the vehicle keeps its old route until the next disruption (R7: needs a `hold()` in R1's environment). The bare `KeyError` for an unknown vehicle id is fixed (R7, PR #50) | none with the defaults (contract-net uses `fail`) | R1 + R7 (not needed for the sweep) |
| 8 | ~~R6's clock convention not confirmed; predictor-on determinism not checked~~ Done (W7): clock checked on METR-LA (`tests/test_traffic_clock.py`), runs with the predictor are deterministic in independent and full mode (above). Both tests need the data, so CI skips them | none | R1 for R6 |


## Files

| file | what it does |
|------|--------------|
| `world.py` | `World`: road graph, travel times, closures, spikes, R6 traffic model hook. `FakeWorld` for tests. |
| `events.py` | `EventGenerator`: D11 Poisson disruptions (spikes, closures, new orders). |
| `environment.py` | `DeliverySimEnvironment`: vehicles drive routes, serve orders, log events. |
| `models.py` | `Order`, `Vehicle`, `Event` dataclasses. |
| `logger.py` | `SimulationLogger`: 9-key JSONL lines. |
| `interfaces.py` | Abstract `Planner`, `Predictor`, `Trigger`, `Negotiator`, `MetricsLogger`, and `Route`. |
| `run.py` | Builds world/vehicles/orders from a YAML config and runs it in the config's mode. |
| `demo_modes.py` | W4 check-in demo: same scenario in centralized / independent / full, summary table. |
| `launcher.py` | W5 sweep launcher: parallel, seed-controlled, resumable runs + `results.csv`. |
| `dispatch.py` | W5: R4's contract-net hands out orders in independent/full (D8), incl. orders released mid-run. |
| `check_log.py` | W6: checks a run's JSONL log is complete and consistent (CLI; used by the launcher). |
| `scenarios.py` | `make_demo_config(world)` random demo config. |

## Clock

Every time is **absolute seconds from midnight**: `t = start_time_s + env.now`.
Default `start_time_s = 21600` (06:00); default run length 14400 s (until 10:00, D4).
Order time windows, release times, closure/spike start and expiry all use this clock.

## Travel time

```
edge_time(u, v, t, key=None) = inf                              if the road is closed at t
                        = free_flow x traffic_mult x spike_mult  otherwise
```

- `free_flow`: edge attribute `free_flow_time` from R2's `load_road_graph`
  (fallbacks: `length / speed`, `weight / speed`, 100 m / speed with one warning).
- `traffic_mult`: R6 `TrafficModel.actual_multiplier_uv(str(u), str(v), t)`; 1.0 if the
  model is missing or the edge is unknown.
- `spike_mult`: product of all active spikes on the edge.
- A closure / spike is active for `start <= t < expiry`.
- `edge_key(u, v)` picks the fastest parallel road (5 real roads have key 1).
- The travel time is fixed when the vehicle **enters** an edge.
- `predicted_time(u, v, t, horizon_min)` uses R6's `Predictor`, else free-flow.
- `cost_fn_at(t)` gives a `cost_fn(u, v, data)` for R2's `astar` / `dijkstra`.

## Vehicles and orders

- A vehicle first serves orders at its start node, then drives `remaining_route`
  (a route that repeats the current node is fine).
- Closed road: the vehicle logs `blocked_wait` and re-checks every 60 s.
- At each node, for each pending order there:
  1. now > `tw_end` -> `order_failed` (`time_window_missed`)
  2. `max(tw_start, release_time) > tw_end` -> `order_failed` (`invalid_time_window`)
  3. `load + demand > capacity` -> `capacity_blocked`, order deferred
  4. else wait until `max(tw_start, release_time)`, serve for `service_time`,
     log `order_delivered`, `load += demand`.
- At the end of the route, leftovers fail with `capacity_exceeded_unservable`
  (deferred) or `not_on_route`. Load never decreases yet, so a deferred order cannot
  succeed later.

## Disruptions (D11)

Poisson arrivals at `disruption_rate_per_hour` (mean gap 3600 / rate s). Each event is
a traffic spike (40%), a closure (30%) or a new order (30%). Spikes and closures hit a
random road starting at the event time. Events are logged and passed to every
`env.subscribe(callback)` listener.

Durations and spike sizes come from the optional `disruptions:` config block
(defaults shown; unknown keys or bad values raise `ValueError` when the
environment is built):

```yaml
disruptions:
  min_duration_s: 600.0      # spike/closure length, uniform in [min, max]
  max_duration_s: 1800.0
  min_spike: 1.2             # spike multiplier, uniform in [min, max]; must be >= 1.0
  max_spike: 3.0
  max_closure_attempts: 20   # closure guard redraws, see below
```

**Closure guard** (D11 "closures never disconnect pending destinations"): before a
closure is placed, `EventGenerator.closure_disconnects` checks it against
`env.pending_targets()`, which gives each vehicle's position (the end of the road it is on
while driving) and its undelivered order nodes. A closure that would cut a vehicle off
from a destination it can reach right now is redrawn on another random road; after
`max_closure_attempts` draws it is skipped and logged as `closure_skipped`. Destinations
already unreachable (one-way streets, earlier closures) never block a closure. Fast
path: if `v` is still reachable from `u` without the road, nobody can be cut off.
Spikes are never blocked. Subclasses that drive vehicles their own way (R4's
`AllocationSimulation`) should override `pending_targets()`; until then it returns
nothing for them and the guard allows every closure.

## Profiling

```bash
python -m src.sim.run --config configs/sim_default.yaml --profile      # top 20
python -m src.sim.run --config configs/sim_default.yaml --profile 40   # top 40
```

Prints a `cProfile` table sorted by cumulative time.

## Hand-offs

- **R3 (orders)**: `env.add_order(order)`; `new_order` events only announce `dyn_N`
  for now (`TODO(R3)` in `events.py`). `Vehicle.current_load` is available for rules.
- **R4/R5 (planning, negotiation)**: wired in by the mode switch and `dispatch.py` (above).
  Still open: centralized is far over the time target at 20 vehicles (profile above); depot
  unloading; `AllocationSimulation` logs `allocation_summary` after `sim_complete` (could use
  `env.add_finisher`).
- **R7**: `ReplanningAgent.set_stops` / `AgentPlanner.set_stops` were added by R1 in W5 (modelled
  on `drop_stops`) so contract-net awards can change a running vehicle's stops; please review.
- **R6 (traffic)**: `World.from_defaults` loads `TrafficModel` + `Predictor` when
  available (with R6's `day_offset`); the clock convention is checked in
  `tests/test_traffic_clock.py` (sim t in seconds from midnight, table step 0 = 00:00).
- **R7 (triggers / re-routing)**: R7's `AgentPlanner` subscribes to `disruption` events and
  replans; a vehicle waiting at a closed road follows the new route as soon as the planner
  replaces it (W5; the old "wait at closures" `TODO(R7)` is done).
- **R8 (metrics)**: read the JSONL log, schema in `docs/log_schema.md`
  (new env event `closure_skipped`).
