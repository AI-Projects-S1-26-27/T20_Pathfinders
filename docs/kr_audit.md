# R3 · Constraint-violation audit

Code: `src/kr/audit.py` · Tests: `tests/test_audit.py` · Demo: `scripts/run_audit_demo.py`

Week 7 target: **zero violations on the checks below in every logged run.** The audit covers the hard
rules in the table, not every rule; see "Not checked".

## Run it

```
python -m src.kr.audit logs/run.jsonl --config configs/scenarios/medium_hotspot_s0.yaml
python -m src.kr.audit logs/*.jsonl --config-dir configs/scenarios --md reports/audit.md --json reports/audit.json
```

- `--config-dir` finds each log's scenario file as `<scenario>_s<seed>.yaml` (the log rows carry both),
  or else the YAML whose `run_id` matches.
- The map named by the config's `map_path` is loaded once per map for KR-3, KR-5 and KR-6; `--no-map` skips them.
- Exit code: `0` no violations, `1` violations found, `2` a log had no config.
- `--md` writes one row per run plus an **all runs** total (table T1 in the report); `--json` lists every
  violation with time, vehicle and reason.

## What is checked

The audit replays R1's log (`docs/log_schema.md`) against the scenario config, with R1's own semantics:
service starts at `t - service_time` of the `order_delivered` row; the simulator never unloads, so
everything a vehicle delivered counts against its capacity; a closure logged at `t` blocks that exact road
`(u, v, key)` for `t <= time < expiry`.

| Check | Rule | Violation |
|---|---|---|
| KR-12 | each order served once | an order delivered twice, or both delivered and failed |
| NODE | delivered where it belongs | delivered at another node, or where the vehicle is not |
| KR-11 | release time | service started before `release_time` |
| KR-7 | wait for the window | service started before `tw_start` |
| KR-8 | window end | service started after `tw_end` |
| KR-1 | capacity | initial load + delivered demand > capacity |
| KR-9 | service after arrival | service started before the vehicle reached the order's node |
| KR-4 | closures | a vehicle entered a road while it was closed |
| KR-5 | one-way roads | a road entered does not exist in that direction in the map (needs the map) |
| KR-3 | road class | a vehicle entered a road whose class its `allowed_road_classes` excludes (needs the map) |
| KR-6 | zones | a large vehicle entered a `kr_zones` zone during its hours (needs the map) |
| PATH | log integrity | a road entered does not start at the vehicle's current node |

Orders without a window (R1's 0.0 / 86400.0) skip KR-7/KR-8. Orders created during the run (`dyn_*`)
have no details in the log, so only KR-12 applies to them.

## Not checked

| Rule | Why |
|---|---|
| KR-10, KR-10a (back at the depot by shift end) | R1's `Vehicle` has no `shift_end` and none of the 90 committed scenario files sets one, so there is nothing to check against |
| KR-2, KR-13, KR-14, KR-15 | allocation and stop-ordering decisions (KR-13 is soft): the log shows their outcome, not the decision; R3's unit tests cover them (`tests/test_rule_coverage.py`) |
| KR-16 (replan after a closure) | covered indirectly: KR-4 flags any vehicle that drives onto a closed road instead of replanning |

So "zero violations" means zero on the twelve checks above, not on every rule.

**Tolerance.** R1's logger rounds `t` to 2 decimals, so `t - service_time` can be up to 0.005 s early.
The audit allows 0.01 s. Without it, a real run showed 11 false KR-7/KR-11 "violations" of a few
milliseconds; with it, the same run audits clean.

## Results so far (reproducible)

```
python scripts/run_audit_demo.py
```

This simulates committed real-data scenarios with a stand-in driver and audits the logs; it writes
`reports/audit_demo.md` and `reports/audit_demo.json` (committed) and logs to `logs/audit_demo/`
(git-ignored). The driver sorts orders by (tw_end, priority, id), deals them round-robin to the
vehicles, and each vehicle visits its share in that order along free-flow shortest paths. It stands in for
R4/R7 allocation, which is not wired to the scenario files yet. Runs are deterministic: the same command
gives the same report (`tests/test_audit.py::test_audit_demo_is_reproducible`); only R7's replanner
timing fields (`cpu_seconds`, `wall_seconds`) differ between logs.

| Scenario | Vehicles | Orders | Delivered | Failed | Disruptions | Road entries checked | Deliveries checked | Violations |
|---|---|---|---|---|---|---|---|---|
| small_clustered_s0 | 10 | 100 | 90 | 10 | 22 | 2,870 | 90 | **0** |
| medium_hotspot_s3 | 15 | 200 | 150 | 50 | 23 | 4,113 | 150 | **0** |

10 disruptions per hour. Failed orders are missed windows or the horizon, which the simulator fails
instead of serving late, so they are not violations. A different driver or disruption rate gives different
delivery counts; compare runs made with the same command. `tests/test_audit.py` also plants one violation
of each kind in a log to show each check fires (and only that check).

## Week 7: audit of a whole sweep

Code: `src/kr/audit_sweep.py` · Tests: `tests/test_audit_sweep.py`

```
python -m src.kr.audit_sweep runs/<sweep_id> --sweep configs/sweeps/<sweep>.yaml \
    --md reports/audit_sweep.md --json reports/audit_sweep.json
```

It reads the folder R1's launcher (`src/sim/launcher.py`) writes. Each run is audited against
`configs/scenarios/<name>.yaml`, where `<name>` is the start of the run id
(`large_uniform_s0__full__r10__s9001` -> `large_uniform_s0`), with the sweep's `overrides` applied.
`python -m src.kr.audit <log> --config-dir configs/scenarios` now finds the config of a sweep log the
same way (the log's `seed` is the run seed, 9001, not the scenario's).

Exit code 0 means every run of the sweep was audited and has zero violations. Crashed runs
(`failed/`), unfinished logs (`.jsonl.part`) and logs with no scenario file are listed as "not
audited" and give exit code 2, so a partly finished sweep can never pass as zero. One violation of each
kind is planted in a copy of the first real log of each mode, and the report says whether each was
flagged, so a zero comes from checks that fire on that mode's log format.

A delivery row without the `node` or `demand` field that `docs/log_schema.md` requires is reported
as a log-schema gap, not as a violation. NODE is then checked by the vehicle's position, and KR-1 by
the demand in the scenario file.

### Pilot result (`reports/audit_sweep_pilot.md`)

The main sweep is R1's and R8's and was not finished when this was written. The pilot ran on main at
commit `c0fe8eb` (R1's launcher and contract-net dispatch, R1 Week 7 merged), with 10 disruptions per
hour and run seeds 9001 and 9002, into one folder:
`configs/sweeps/r3_audit_pilot.yaml` (the 9 seed-0 scenarios, independent and full, 36 runs) and
`configs/sweeps/r3_audit_pilot_central.yaml` (the 3 small seed-0 scenarios, centralized, 6 runs). Only
small scenarios were run centralized: one took about 5 minutes, and a large one is far slower.

- **0 violations in 42 runs**: 66,831 road entries and 7,800 deliveries checked (504 of them with a
  time window); every order delivered, none failed, 649 closures in all.
- All 8 planted violations (KR-12, NODE, KR-11, KR-8, KR-1, KR-4, KR-3, PATH) were flagged in all
  three modes.
- **Log-schema gap found (TODO(R4))**: in centralized mode, all 600 `order_delivered` rows have only
  `order_id`, no `node` and no `demand` (`src/allocation/sim_runner.py`, `log_event('order_delivered',
  {'order_id': order.id}, v.id)`). The vehicle was at the order's node in all 600. A first version of
  the audit counted these as 600 NODE violations; it now reports them as schema gaps.
- Not covered in practice: KR-3, because the fleet is small-only, so no vehicle has a road-class
  restriction and KR-3 cannot fail (the planted case shows it fires). KR-6 has no zones to check.
- R3's rule engine is not what kept these runs legal: R4's allocation (contract-net and OR-Tools)
  uses its own feasibility checks and R7's planner gets closures from R1's world. The audit is an independent check of the outcome.

To do when the main sweep is done (R3): run the command above on `runs/<main sweep_id>` and commit
`reports/audit_sweep.md` and `.json`.
