# R3 · Constraint-violation audit

Code: `src/kr/audit.py` · Tests: `tests/test_audit.py` · Week 7 target: **zero violations on every logged run**.

## Run it

```
python -m src.kr.audit logs/run.jsonl --config configs/scenarios/medium_hotspot_s0.yaml
python -m src.kr.audit logs/*.jsonl --config-dir configs/scenarios --md reports/audit.md --json reports/audit.json
```

- `--config-dir` finds each log's scenario file as `<scenario>_s<seed>.yaml` (the log rows carry both),
  or else the YAML whose `run_id` matches.
- The map named by the config's `map_path` is loaded once per map for KR-3 and KR-6; `--no-map` skips them.
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
| KR-4 | closures | a vehicle entered a road while it was closed |
| KR-3 | road class | a vehicle entered a road whose class its `allowed_road_classes` excludes (needs the map) |
| KR-6 | zones | a large vehicle entered a `kr_zones` zone during its hours (needs the map) |
| PATH | log integrity | a road entered does not start at the vehicle's current node |

Orders without a window (R1's 0.0 / 86400.0) skip KR-7/KR-8. Orders created during the run (`dyn_*`)
have no details in the log, so only KR-12 applies to them.

**Tolerance.** R1's logger rounds `t` to 2 decimals, so `t - service_time` can be up to 0.005 s early.
The audit allows 0.01 s. Without it, a real run showed 11 false KR-7/KR-11 "violations" of a few
milliseconds; with it, the same run audits clean.

## Results so far

| Run | Map | Disruptions | Road entries checked | Deliveries checked | Violations |
|---|---|---|---|---|---|
| `small_clustered_s0` (real Amazon statistics, 10 vehicles, 100 orders) | Secunderabad | 22 | 3,772 | 90 | **0** |
| `medium_hotspot_s3` (real Amazon statistics, 15 vehicles, 200 orders) | Secunderabad | 23 | 6,192 | 150 | **0** |
| 100 placeholder orders, 10 vehicles | Secunderabad (small) | 49 | 1,819 | 66 | **0** |

For these runs each vehicle simply drove its share of the orders in deadline order (R1's simulator, 10-20
disruptions per hour), because R4/R7 allocation is not wired to the scenario files yet; the failed orders
are missed windows, which are not violations (the simulator fails the order instead of serving it late).
The real runs used `python -m src.kr.audit <logs> --config-dir configs/scenarios`, which found each
log's scenario file by name. `tests/test_audit.py` repeats a run with 30 disruptions per hour on every
test run, and plants one violation of each kind in a log to show each check fires (and only that check).

TODO(R8): run it over every experiment log in Week 7 (`--config-dir configs/scenarios --md reports/audit.md`).
