# Weekly log

Maintained by R8 (plan W1). Written on 18 Nov 2026 from the git history of `main` (first-parent merge commits, state `1b6c27a`) and the
files in the repository; it was not kept week by week. The team started about a week behind the plan, then merged most work in the
last two weeks, so the plan's week labels below are calendar weeks, not completed plan-weeks.

| Plan week | Pull requests merged into `main` that week | Notes |
|---|---|---|
| Week 1 (5-9 Oct) | none | Repository created 2 Oct. Team decisions D1-D12 were written into `docs/DECISIONS.md` from the plan defaults. Proposal submitted (due 2 Oct). |
| Week 2 (12-16 Oct) | #1 (R2 road graph and region selection) | R2 chose Secunderabad (D1). |
| Week 3 (19-23 Oct) | #3 (R6 METR-LA loading and prediction setup) | |
| Week 4 (26-30 Oct) | none | Reviews of #4 (R1 sim core) and #5 (R7 D* Lite) happened this week; both merged later. |
| Week 5 (2-6 Nov) | #5, #10 (R7 D* Lite), #12 (R2 search benchmark, time-dependent A*, Yen) | |
| Week 6 (9-13 Nov) | 24 PRs: #8 (R3 scenarios), #11, #24, #27, #34 (R7), #13 (R5), #14-#20 (R4, seven stacked PRs), #21 (R1 disruptions), #22, #23, #25, #26 (R2), #28, #32, #33 (R6), #29, #30, #31 (R3) | Most of the work of weeks 2-6 of the plan was merged here. |
| Week 7 (16-20 Nov) | 14 PRs up to 18 Nov: #35, #41 (R5), #36, #39, #40, #46, #47 (R3), #37, #38, #42, #44 (R1), #43, #50 (R7), #45 (R2) | #48 (R5) and #49 (R6) were still open on 18 Nov. |

## R8 entries

The member assigned to R8 had made no commit by 18 Nov. The R8 work was prepared on 18 Nov with an AI assistant (Claude) and handed over to the R8 member; the plan's R8 tasks map to
the repository as follows.

| Plan week | R8 task in the plan | Status on 18 Nov |
|---|---|---|
| W1 | Create WEEKLY_LOG, AI_USAGE_LOG, CONTRIBUTIONS | `docs/AI_USAGE_LOG.md` already existed (started by other members); `docs/WEEKLY_LOG.md` and `docs/CONTRIBUTIONS.md` added now. |
| W1 | Metric definitions and log schema with R1 | Metric definitions: `src/eval/metrics.py` docstring and `docs/EXPERIMENT_PROTOCOL.md`. `docs/log_schema.md` is R1's and says "to be confirmed with R8"; the metrics were written against it as it is. |
| W1 | Notebook skeleton, experiment protocol, rubric board | No notebook: `src/eval/aggregate.py` reads the logs into a tidy table instead. Protocol: `docs/EXPERIMENT_PROTOCOL.md`. Rubric checklist: `docs/reports/r8_report_sections.md`, last section. |
| W2 | Metric functions with unit tests on hand-made logs; plot style; report skeleton | `src/eval/metrics.py` and `tests/test_eval_metrics.py` (13 tests); `src/eval/plots.py` (one colour per mode); report sections in `docs/reports/r8_report_sections.md`. |
| W3 | Experiment runner; statistics helpers | Runner: R1's `src/sim/launcher.py`, reused unchanged. Statistics: `src/eval/stats.py`, `tests/test_eval_stats.py`. |
| W4 | Check-in materials; report drafts with verified citations; commit-balance check | No check-in materials were made. Commit balance: `docs/CONTRIBUTIONS.md`. Citation check: see `docs/reports/r8_report_sections.md`. |
| W5 | Pilot sweep; pre-register thresholds | `configs/sweeps/pilot.yaml` (pilot seeds 7-9, 27 runs planned): the 18 decentralized runs and 2 of the 9 centralized runs finished, the other 7 centralized runs were stopped to leave the CPU to the main sweep. `configs/sweeps/pilot_stress.yaml` (32 runs, rate calibration). Criteria written in `docs/EXPERIMENT_PROTOCOL.md` before the main sweep ran. |
| W6 | Freeze experiment config; teach-backs; Methods and Related Work | Config frozen: `configs/sweeps/main.yaml`, `small_three_modes.yaml`. Teach-back sessions were not organised by R8. |
| W7 | Main sweep; figures and tables by script; statistics, results, limitations, contributions | `python -m src.eval.run_all`; `reports/r8/`, `reports/figures/F6`, `F7`, `F9`; results and limitations in `docs/reports/r8_report_sections.md`. |
| W8 | AI-usage section; citations; proofreading; fresh-clone check; submission | AI-usage section: in `docs/reports/r8_report_sections.md`, from the log. The fresh-clone reproduction test by someone who did not write the code has not been done. |
