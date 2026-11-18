# Individual contributions

Maintained by R8 (plan Section 9.1: one paragraph per member tied to modules and commits). Counts are **non-merge commits on `main`** at
`1b6c27a` (18 Nov 2026, after PR #50), grouped by GitHub identity; commits that exist only on unmerged branches or in open PRs are not counted.
Directory counts say where the commits landed, not how hard the work was. The commit history is compressed into the last two weeks:
the team ran about a week behind the plan from the start, and several members pushed multiple plan-weeks of work at once (the table
shows first and last commit dates).

| Role | Member (GitHub) | Non-merge commits on main | Where | First - last commit |
|---|---|---|---|---|
| R1 Simulator core and runner | Siddarth Mediratta | 26 | `src/sim` 23, tests 14, docs 12 | 26 Oct - 17 Nov |
| R2 Map, search, visualization | G Prudhvi Raj (PRAJ-50) | 40 | `src/search` 14, `src/viz` 14, `src/world` 5, tests 19, docs 16 | 12 Oct - 17 Nov |
| R3 Knowledge representation, scenarios | Divesh | 20 | `src/kr` 15, `src/data` 8, tests 16, docs 16 | 31 Oct - 18 Nov |
| R4 Allocation and centralized baseline | DarkOrchid19 | 10 | `src/allocation` 7, tests 7, docs 7 | 9 Nov (all) |
| R5 Coordination and game theory | Priya (Priya4448) | 17 | `src/coordination` 10, tests 9, docs 6 | 8 Nov - 17 Nov |
| R6 Traffic prediction | Nandan G Kandra | 47 | `src/prediction` 14, `src/world` 2, tests 5, docs 25 | 2 Oct - 14 Nov |
| R7 Replanning (D* Lite) | Prabhanjan | 30 | `src/search` 21 (D* Lite), `src/sim` 6, tests 17, docs 13 | 27 Oct - 18 Nov |
| R8 Evaluation, report, process | the member assigned R8 | 0 before this work | none on `main` | none |

## Per member

**R1, Siddarth Mediratta.** Built the discrete-event simulator in `src/sim` (SimPy environment, road world with free-flow time,
traffic multipliers, closures and spikes, the Poisson disruption generator of D11 with its closure connectivity guard, the log writer of
`docs/log_schema.md`), the headless runner `src/sim/run.py` that selects the three modes, the sweep launcher `src/sim/launcher.py`
(parallel, seeded, resumable), and the log checker `src/sim/check_log.py`. Merged PRs: #21, #37, #38, #42, #44 and the sim core.

**R2, G Prudhvi Raj.** Chose the map region (Secunderabad, D1) and built the road graph with imputed speeds and free-flow times, wrote
Dijkstra, A*, time-dependent A* and Yen's k-shortest paths from scratch with node-expansion counting, the benchmark, admissibility and FIFO
checks, the travel-time matrix, and the replay and three-pane visualization in `src/viz` (replay from JSONL logs, side-by-side modes,
hardening and final figures). Merged PRs: #12, #22, #23, #25, #26, #45. Also reviewed and merged most of the team's pull requests
(the merge commits on `main` are his account); see below for the evaluation work.

**R3, Divesh.** Wrote the knowledge base and rule engine, the CSP with arc consistency, the planner rules and the constraint-violation audit
in `src/kr`, and the order and scenario generator from Amazon Last Mile statistics in `src/data` (`configs/scenarios/*`, data profile).
Produced the rule-rejection statistics (T1) and the audit of a launcher sweep with zero violations. Merged PRs: #8, #29-#31, #36, #39,
#40, #46, #47.

**R4, DarkOrchid19.** Wrote the allocation layer in `src/allocation`: shared cost model, contract-net allocation of orders (D8),
the OR-Tools centralized re-optimization baseline (CVRP with time windows, partial-state re-solve, 2N message accounting), the mode
integration and the baseline quality and time-limit study in `reports/r4` (T2). Merged PRs: #14-#20, all pushed on 9 Nov.

**R5, Priya.** Wrote conflict detection on directed road segments with capacities, the trigger policy (tau, delta-t), the Vickrey-style
negotiation, the sequential resolver and the negotiation rate limiter in `src/coordination`. Merged PRs: #13, #35, #41 (#48 was still open
on 18 Nov).

**R6, Nandan G Kandra.** Loaded METR-LA, wrote the baselines (persistence, historical average) and the shared LSTM forecaster in
`src/prediction`, the traffic ground-truth model on R2's graph (`src/world/traffic_model.py`, `edge_table.py`), the model freeze and model
card, and the predictor ablation and figures (F4). Merged PRs: #3, #28, #32, #33 (#49 was still open on 18 Nov). The initial commit of the repository came from the `f20241014` account, which is Nandan's BITS e-mail address.

**R7, Prabhanjan.** Wrote D* Lite (`src/search/dstar_lite.py`), the per-vehicle replanner in `src/sim/agent_planner.py` with hysteresis and
unreachable-stop handling, and the replan-rate and latency analysis. Merged PRs: #5, #10, #11, #24, #27, #34, #43, #50.

**R8.** No commits from the member assigned to R8 are on `main` as of 18 Nov. The R8 deliverables of the plan (metrics, experiment runner
and statistics, protocol and pre-registered criteria, sweeps, figures F6, F7, F9, results, the weekly log and this file) were prepared on
18 Nov 2026 with an AI assistant (Claude) and handed over to the R8 member, who is responsible for explaining them; the AI use is in `docs/AI_USAGE_LOG.md`. The experiment runner itself
(`src/sim/launcher.py`) is R1's work and is reused unchanged.

## How these paragraphs were checked

Commit counts: `git log origin/main --no-merges --author=<identity> -- <dir>`. Merged PR numbers: first-parent merge commits on `main`.
Module descriptions: the repository's own docs and sources (`docs/`, module READMEs, DECISIONS.md). They are not a judgment of quality.
