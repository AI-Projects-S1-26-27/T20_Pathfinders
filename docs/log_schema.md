# Simulation Log Schema

One JSON object per line, written to `logs/<run_id>.jsonl` (or `log_path` from the config).
Each run overwrites the old file. To be confirmed with R8 (metrics/plots).

## Keys (always all nine, in this order)

| # | key        | type         | meaning |
|---|------------|--------------|---------|
| 1 | `run_id`   | str          | from config `run_id` |
| 2 | `mode`     | str          | `independent` / `centralized` / `full` (label until R4/R5 land) |
| 3 | `seed`     | int          | RNG seed of the run |
| 4 | `scenario` | str          | from config `scenario` |
| 5 | `rate`     | float        | `disruption_rate_per_hour` (D11 lambda) |
| 6 | `t`        | float        | absolute seconds from midnight, `start_time_s + env.now`, rounded to 2 decimals (default start 21600 = 06:00) |
| 7 | `agent_id` | str          | vehicle id for vehicle events, `"env"` otherwise |
| 8 | `event`    | str          | event name (below) |
| 9 | `payload`  | object       | event details (below) |

Example:

```json
{"run_id": "sim_demo", "mode": "independent", "seed": 42, "scenario": "secunderabad_demo", "rate": 5.0, "t": 21600.0, "agent_id": "V1", "event": "edge_enter", "payload": {"edge": [12829715342, 5436549587, 0], "expected_duration": 9.05}}
```

## Events and payloads

Edges are `[u, v, key]` (OSM node ids, key of the road actually used).

| event | agent_id | payload |
|-------|----------|---------|
| `sim_start` | env | `{"start_time_s", "duration_seconds", "n_vehicles", "n_orders", "predictor": bool}` (`predictor`: R6's forecast was available to planners) |
| `edge_enter` | vehicle | `{"edge": [u, v, key], "expected_duration": float s}` (time fixed on entry) |
| `edge_exit` | vehicle | `{"node": int}` (node just reached) |
| `blocked_wait` | vehicle | `{"edge": [u, v, key]}` logged every 60 s while the next road is closed |
| `order_delivered` | vehicle | `{"order_id", "node", "demand"}`; `t` = end of service |
| `capacity_blocked` | vehicle | `{"order_id", "demand", "load", "capacity"}`; order is deferred |
| `order_failed` | vehicle | `{"order_id", "node", "reason"}` |
| `disruption` | env | `{"type": "traffic_spike" \| "closure", "edge": [u, v, key], "magnitude": float \| null, "duration": float s, "expiry": float t}`; `magnitude` is the spike multiplier, `null` for a closure |
| `closure_skipped` | env | `{"attempts": int}`; a closure was due but every road drawn would cut a vehicle off from a pending destination (closure guard, D11), so no road was closed. Not passed to `subscribe` listeners. |
| `new_order` | env | `{"order_id": "dyn_N", "release_time": float t}` |
| `sim_complete` | env | `{"delivered": int, "failed": int}` |

### `order_failed` reasons

| reason | when |
|--------|------|
| `time_window_missed` | vehicle arrives after `tw_end` |
| `invalid_time_window` | `max(tw_start, release_time) > tw_end` (can never be served) |
| `capacity_exceeded_unservable` | was `capacity_blocked` and still undelivered at the end of the route |
| `not_on_route` | the vehicle's route never reached the order's node |
| `unreachable` | a closure cut the order's stop off; R7's planner failed it (`unreachable="fail"`, the default) and R4's `reassign` hook did not take it |
| `no_vehicle` | no vehicle was given the order (the config has no vehicles); logged right after `sim_start`, `agent_id` `env` |
| `no_feasible_bid` | contract-net (independent/full): no vehicle could insert the order (capacity, time window or unreachable); `agent_id` is `env` |
| `horizon_exceeded` | still pending on a vehicle when the run ended (`duration_seconds`); logged by R1's environment just before `sim_complete`, and by R4's centralized runner. Every order now ends `order_delivered` or `order_failed` |
| `central_unassigned` | centralized (R4, `src/allocation/runtime.py`): the OR-Tools re-solve left the order out of every route |
| `service_infeasible` | centralized (R4, `src/allocation/sim_runner.py`): the vehicle reached the order but its time window had passed or it would exceed capacity |

`python -m src.sim.check_log <log> [--config <cfg>]` (R1, W6) checks a log against this page:
keys, one `sim_start`/`sim_complete`, time order, each order ending once with a reason listed
above, counts, and edge enter/exit pairs. In centralized mode R4's runner also writes
`central_state_report`, `central_plan_broadcast` and, after `sim_complete`, `allocation_summary`
(not yet described here: TODO(R4)).

## Events from R7's agent planner (`src/sim/agent_planner.py`, `src/sim/coordination_hook.py`)

Written only when an `AgentPlanner` is attached (modes `independent` and `full`). Times are in seconds;
`cpu_seconds` is `time.process_time` (D9), `wall_seconds` is `time.perf_counter`. On Windows, use
`wall_seconds` for one decision: the CPU timer there moves in 15.6 ms steps, so one replan reads 0.0 or 15.62.

| event | agent_id | payload |
|-------|----------|---------|
| `replan` | vehicle | `{"trigger", "edge": [u, v], "from_node", "route_found": bool, "unreachable_stops": [node], "expanded": int, "cpu_seconds", "wall_seconds", "gain_s": float \| null, "mode", "trigger_enabled": bool}`. The vehicle's route **changed** (since W5; a cost change that leaves the path the same writes nothing), or a stop became unreachable (`route_found: false`). `trigger` = `closure` / `traffic_spike` (a disruption), `unreachable` (re-plan after dropping cut-off stops; `edge` null), `negotiation_yield` (the vehicle lost an auction and detours), or `new_order` (R1 W5: R4's contract-net awarded the vehicle an order, or the opening auction at t0; `edge` null). `gain_s` = old route cost - new route cost, null when the old route was blocked |
| `replan_kept` | vehicle | `{"trigger", "edge": [u, v], "gain_s", "cpu_seconds", "wall_seconds", "mode"}`. A better route existed but not by the hysteresis margin, max(`min_gain_s`, `min_gain_frac` x old cost); the route is kept with new ETAs |
| `stop_unreachable` | vehicle | `{"stops": [node], "failed": [order_id], "reassigned": [order_id]}`. Stops dropped from the plan; their orders were taken by R4's `reassign` hook or failed (`order_failed`, reason `unreachable`) |
| `negotiation_request` | vehicle | `{"count": 1, "edge": [u, v, key]}`. One per vehicle in an auction (D7) |
| `negotiation_response` | vehicle | `{"count": 1, "edge": [u, v, key]}`. One per vehicle in an auction (D7) |
| `negotiation_result` | env | `{"count": m, "edge": [u, v, key], "vehicles": [id], "winner": id \| null, "price": float, "ttc": float s, "actions": {loser_id: "detour" \| "wait"}}`. One per auction; `count` = vehicles told the result (D7: 3m messages per auction in all). `"wait"` is only logged: R1's simulator cannot hold a vehicle back yet |
| `trigger_toggle` | env | `{"trigger_enabled": bool}`. Coordination switched on or off during the run |
| `hook_error` | vehicle | `{"hook": "coordination" \| "reassign", "error": "<Type>: <message>"}`. A team hook raised; the run continues (W6) |
| `allocation` | vehicle / `manager` | R4's contract-net message (D7), one row per message: `{"phase": "announcement" \| "bid" \| "award", "recipient", "order_id", "sender"?, "cost"?}`. Independent/full with `allocation: contract_net` (R1's `src/sim/dispatch.py`) |
| `allocation_complete` | env | One per contract-net auction: `{"cpu_seconds", "messages", "mode", "order_id", "winner": vehicle_id \| null}` (R4's centralized runner logs the same event per re-solve, without `order_id`/`winner`) |
| `replan_summary` | env | End of run (`planner.finish()`): `{"mode", "trigger_enabled", "replans", "replans_kept", "replan_cpu_seconds", "kept_cpu_seconds", "agent_cpu_seconds", "coordination_cpu_seconds", "hook_errors", "ignored_events", "negotiation_calls", "negotiation_messages", "yields", "failed_unreachable": [order_id], "reassigned": [order_id], "unreachable_stops": {vehicle_id: [node]}, "min_gain_s", "min_gain_frac"}`. `replan_cpu_seconds` sums `replan` rows only (since W6; W4/W5 summed every check, now `agent_cpu_seconds`) |
