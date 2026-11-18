# Experiment protocol and pre-registered criteria (R8)

Owner: R8 (evaluation). Plan references: Section 7 (protocol, metrics, success criteria). Decisions D4, D7, D9, D11, D12.
This file was written **before** `configs/sweeps/main.yaml` and `configs/sweeps/small_three_modes.yaml` were run. Do not edit the
criteria in Section 4 after results exist; if one is missed, the report says so (plan 7.3).

## 1. Claim under test

Selective coordination, triggered only when a road-capacity conflict is predicted soon (tau, D6), should keep a decentralized
fleet close to the quality of full centralized re-optimization while using much less computation and fewer messages, and should
resolve conflicts that independent replanning leaves in place. The study varies one thing, the disruption rate, and compares three
conditions on identical scenarios and seeds.

## 2. Design

| Element | Specification |
|---|---|
| Conditions | `centralized` (OR-Tools re-solve, R4), `independent` (D* Lite, trigger off), `full` (D* Lite + trigger, R5). `independent` against `full` is the **ablation** of selective coordination: nothing differs except `trigger_enabled`. |
| Independent variable | Disruption rate lambda per hour (D11 Poisson process: 40% traffic spikes, 30% closures, 30% new orders). |
| Fixed across conditions | Same map (D1), same scenario file (orders, fleet, depots), same run seed (so the same disruptions), same information feed (D10), same contract-net allocation of new orders (D8) in the two decentralized modes, predictor **off**. |
| Main sweep (`main.yaml`) | independent and full, 9 medium scenarios (3 layouts x 3 instances), 2 run seeds (101, 102), lambda in {0, 20, 60, 150, 300} per hour: 180 runs, 18 matched pairs per rate. |
| Three-mode sweep (`small_three_modes.yaml`) | independent, full, centralized, 3 small scenarios, 2 run seeds (101, 102), lambda = 10 per hour: 18 runs, 6 matched pairs. Centralized appears only here because it needs about 5 minutes per run even on the small scenarios (about 20 minutes or more at 20 vehicles, R1's notes). |
| Pilot seeds | 7, 8, 9 (and R3's 9001-9005). Used only to calibrate and check definitions. Never in a main sweep. |
| Statistics | Mean over runs with a 95% percentile bootstrap CI (10,000 resamples of the runs, seed 0). Comparisons between modes are paired by (scenario file, run seed, rate). |
| Failed or unfinished runs | Listed in `reports/r8/<sweep>/missing.txt` and in `RESULTS.md`. Never dropped silently. |

## 3. Metrics (D12)

Defined in the docstring of `src/eval/metrics.py` and computed only from the run log and its config. Short form:
mean delivery time per order; makespan; on-time rate; priority-weighted delivery time (weights express 3, standard 2, economy 1: our
assumption, D3 gives no weights); fleet distance; waiting time; road-capacity violations (times a directed road went above capacity 1,
the capacity the coordination layer enforces, see DECISIONS.md "Simulator Integration Status") and seconds spent above capacity, also
with D5 class capacities; Jain fairness of vehicle workload; planning CPU per run (D9, replan + coordination + allocation, or solver CPU
for centralized); 95th percentile CPU of one replan or re-solve; messages per run (D7, negotiation + allocation; centralized 2N per
re-solve). Metrics are unit-tested on hand-made logs (`tests/test_eval_metrics.py`).

"Unresolved conflicts" in the plan is measured as **actual capacity violations**, counted whether or not the system predicted them.

## 4. Pre-registered success criteria

H1 and H2 are the targets of plan Section 7.3 (written 29 Sep 2026, before any experiment). They are unchanged. H3 is the plan's third
target with the rates and the metrics made concrete; it was split in two before the main sweep because the pilot (Section 5) showed
the two metrics behave differently.

| ID | Criterion | Evaluated on |
|---|---|---|
| H1 | Mean delivery time per order of `full` is at most 10% above `centralized` (mean over matched runs of full / centralized - 1 is at most 0.10). | `small_three_modes`, lambda = 10 |
| H2a | Planning CPU per run of `full` is at most half of `centralized` (mean over matched runs of full / centralized is at most 0.50). | `small_three_modes`, lambda = 10 |
| H2b | Messages per run of `full` are at most half of `centralized` (same rule). | `small_three_modes`, lambda = 10 |
| H3a | `full` has lower mean delivery time per order than `independent`, with non-overlapping 95% CIs, at **every** lambda in {60, 150, 300}. | `main`, medium and high rates |
| H3b | `full` has fewer capacity violations than `independent`, with non-overlapping 95% CIs, at **every** lambda in {60, 150, 300}. | `main`, medium and high rates |

If a criterion is missed, the report states it and analyses why (plan 7.3). Verdicts are produced by `python -m src.eval.thresholds`.

## 5. What the pilot showed and what it changed

Pilot seeds only, small and medium uniform scenarios, `configs/sweeps/pilot.yaml` and `pilot_stress.yaml`, run on one machine:

* At the D11 rates (0 to 20 per hour) no metric moves: on `small_uniform_s0`, independent mode, mean delivery time is 715.18 s at lambda = 0
  and 715.41 s at lambda = 20. On this 6,056-road map few disruptions touch a vehicle's route.
* Up to 300 per hour (about 345 closures in four hours) every order is still delivered (0 failed, 0 unaccounted for) and mean delivery time
  rises by about 0.1 to 0.4%. Replans rise by 10 to 14%. So 300 per hour is "still solvable"; it is the top of the grid.
* `full` has fewer capacity violations than `independent` at every rate (7 against 10 on small, 8 against 15 on medium) and a slightly
  higher mean delivery time (about +0.8 s on small, +2.7 s on medium).

Change made because of the pilot: the lambda grid of the main sweep is {0, 20, 60, 150, 300} instead of D11's {0, 2, 5, 10, 20}
(0 and 20 are kept as anchors). The criteria values were **not** changed. H3 was split into H3a and H3b, so the verdicts on
delivery time and on conflicts are not mixed.

## 6. Known limits of the design (also in the report's limitations)

* The traffic-multiplier model (METR-LA, R6) is used only if `data/raw/metr-la.h5` is present; `reports/r8/MANIFEST.json` records
  which it was. Without it travel times are free-flow times plus the disruption spikes.
* The predictor is off in every sweep (predictor-on determinism is not verified). R6's ablation is a separate result.
* Centralized is compared on small scenarios and one rate only.
* Both decentralized modes use the same contract-net allocation, whose messages dominate the message counts (see `RESULTS.md`).
* Two seeds per scenario: the run seed only changes the disruptions, so scenario variety, not seed variety, drives the spread.
* One region (Secunderabad), simulated fleet and disruptions, US-derived traffic patterns and obfuscated demand geography.
