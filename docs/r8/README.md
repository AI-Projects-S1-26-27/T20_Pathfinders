# R8 evaluation toolkit — integration package

This is an overlay package for `f20241014/T20_Pathfinders`. Copy the folders into the repository root; it does not replace R1–R7 modules.

## Integration order

1. Create the branch `r8/evaluation-integration` from the latest `main`.
2. Copy `src/eval`, `tests/test_eval_metrics.py`, `configs/sweeps/r8_main.yaml`, and the R8 documentation into the same paths in the repository.
3. Apply `patches/environment_wait_and_service_events.patch` from the repository root with `git apply patches/environment_wait_and_service_events.patch`. This adds observational `wait_start`/`wait_end` and `service_start`/`service_end` log rows only; it does not change routing, allocation, or timing.
4. Run the checks below before starting the sweep.

## Launcher compatibility

The current `src.sim.launcher` accepts exactly `sweep_id`, `configs`, `modes`, `seeds`, `rates`, `predictor`, `overrides`, `out_dir`, and `workers`. The included YAML uses those supported keys and expands to 3 scenarios × 3 modes × 10 seeds × 5 rates = **450 runs**.

```powershell
python -m src.sim.launcher configs/sweeps/r8_main.yaml --dry-run
```

Expected final line: `450 runs`. Dry-run validates expansion only; it does not execute simulations.

## Tests and pilot

```powershell
python -m pytest -q tests/test_eval_metrics.py
python -m pytest -q
python -m src.sim.check_log <one_pilot_log.jsonl> --config <matching_scenario.yaml>
```

Run a small pilot (one scenario, one rate, at least 3 seeds and all modes) before the 450-run sweep. Freeze success thresholds in `docs/DECISIONS.md` before looking at main-sweep results.

## Metrics and interpretation

- Delivery response time = `order_delivered.t` − configured or `new_order` release time. It includes travel, waiting and service time. Both total and mean are exported.
- On-time delivery uses explicit `service_start` timestamps when the patch is applied. Legacy logs fall back to delivery time minus configured service time and are labelled as an estimate.
- Waiting uses paired explicit `wait_start`/`wait_end` events for time-window and blocked-road waits. Unclosed waits at the simulation horizon are censored. Legacy logs without these events report waiting as unavailable rather than zero.
- Distance sums GraphML `length` attributes for traversed edges. If any traversed edge lacks a usable length, distance is null.
- Peak congestion is maximum simultaneous occupancy derived from `edge_enter`/`edge_exit`. Mean overload uses provisional road-class capacities and must be reviewed with the team before reporting.
- Workload fairness uses observed edge travel durations plus service durations.
- `allocation_complete` is classified as contract-net when the payload has `order_id` (the repository's independent/full format); otherwise it is classified as centralized solver work.
- Negotiation message totals use each event's actual `payload.count`. Allocation message rows count one each; `allocation_complete.payload.messages` is deliberately not added because it is an aggregate and could double count.
- Unresolved conflicts remain null because the existing schema does not link predicted conflicts to subsequent realized violations.

## Commands after the sweep

```powershell
python -m src.sim.launcher configs/sweeps/r8_main.yaml --workers 4
python -m src.eval.runner --logs runs/r8_main/logs --configs configs/scenarios --out reports/r8 --map data/processed/secunderabad.graphml
```

Do not describe the 450-run sweep as completed until the launcher reports all runs done, failures have been resolved/reported, and logs pass the repository checker.
