# R3 · Rule-rejection statistics (Week 6)

Code: `src/kr/rejection_stats.py` · Tests: `tests/test_rejection_stats.py` ·
Full report: `reports/rule_rejections.md` and `.json` (committed)

```
python -m src.kr.rejection_stats            # 9 scenarios of seed 0, about 4 minutes
```

Same command, same report: two runs give byte-identical JSON and Markdown.

## Table for the report

How often R3's rules block an insertion or a road, on the 9 committed seed-0 scenarios (real Amazon
statistics, Secunderabad map; 1,800 orders, 30,000 order-vehicle checks). Rows A and B1 are
stand-alone measurements of the rules, not of the system the experiments run; row B2 gives both.

| What is blocked | Rule | How often |
|---|---|---|
| Order offered to a vehicle in a stand-alone greedy allocation (part A) | KR-1 capacity | **28.1%** of 30,000 checks (8,435), 100% of all rejections |
| | KR-8 time window | 0 |
| | KR-10 back by shift end | 0 as committed; **4 checks (0.01%)** if every vehicle is due back by 10:00 (the only shift end tested) |
| | KR-11 release, KR-10a shift duration, no legal path | 0 |
| Order no vehicle can take | KR-15 | 0 of 1,800 orders |
| Road for a large vehicle (part B1, hypothetical depots) | KR-3 road class | 81.3% of roads closed to them (4,922 of 6,056); **1,299 of 1,800 (order node, depot) trips impossible (72.2%)**: 600 distinct order nodes x 3 hypothetical depots; trips that need a detour are 27.6% longer |
| Road during the run (part B2) | KR-4 closure | Stand-in hand-written routes: **3 of 7** distinct closures changed a route (13 of 63 closure-scenario pairs, 25 route changes). Contract-net, the allocation the experiments use: **0 of 7** (0 of 63 pairs, 0 route checks). Both: 0 vehicles stuck, 0 audit violations |

Three things to state next to the table:

- **Part A is not R4's allocation.** It is a stand-alone greedy allocation at planning time (least extra
  time, in release order). In the experiments, R4's contract-net uses its own capacity checks and R3's
  rules are not called. That KR-1 is the only rule that blocks follows from how the scenarios were built:
  fleet capacity is sized so total demand is 80% of it (`docs/scenarios.md`), and every order is loaded
  at 06:00 with no unloading.
- **Part B1's trips are (order node, depot) pairs, not orders.** 600 distinct order nodes x 3 hypothetical
  depots = 1,800 trips; that there are also 1,800 orders is a coincidence. The depots are placed on the
  large-vehicle road network, because the committed fleet has no large vehicles.
- **Part B2 is run twice per scenario.** Once with `scripts/run_audit_demo.py`'s stand-in hand-written
  routes (allocation `route`), once with the simulator's default allocation (R4's contract-net since R1's
  #38). Only the second is the system the experiments run.

## What it means

- **In the stand-alone greedy allocation, capacity is the only rule that blocked, and this follows from
  the 80% sizing.** Every rejection is KR-1 and every order still finds some vehicle. This is a property
  of the scenarios (capacity sized to 80% of demand, every order loaded at 06:00 with no unloading), not
  a measurement of R4's runs.
- **Time windows never block, and that follows from the data.** Only 6.6% of stops have a window in the
  Amazon data, and their median width is 8 hours (`docs/data_profile.md`), wider than the whole 4-hour
  simulated period. Inside the period they are almost never tight. Report this as a property of the
  data, not as evidence that KR-8 is unnecessary: the rule tests show it working on tight windows.
- **KR-10 rarely binds at a 10:00 shift end.** With every vehicle due back by 10:00, the end of the
  simulated period, KR-10 blocks 4 of 30,000 checks. An earlier shift end was not tested; it is a tighter
  limit, so it would be expected to block at least as many. A shift end in the scenarios would also let
  the audit check KR-10 (see `docs/kr_audit.md`, "Not checked").
- **KR-3 is why the fleet is small-only.** From 3 hypothetical depots on the large-vehicle network, a
  large vehicle cannot make 1,299 of 1,800 (order node, depot) trips. This follows from the rule and the
  map together: 4,922 of Secunderabad's 6,056 directed roads (81.3%) are closed to large vehicles, of
  which 4,800 are residential (also 120 living_street and 2 unclassified; `reports/rule_rejections.md`,
  part B1). Where a trip is possible, most are the same length, but the ones that need a detour are
  27.6% longer.
- **Whether closures force reroutes depends on the allocation.** The 9 seed-0 scenarios share the same 7
  closures. With the stand-in hand-written routes, 3 of the 7 lay on some vehicle's remaining route in at
  least one scenario and changed it (13 of 63 closure-scenario pairs, 25 route changes). With
  contract-net, the allocation the experiments use, none did: 0 of 63 pairs changed a route, and no
  closure even triggered a route check. With 7 closures from one seed, neither is a rate. In both, no
  vehicle waited at a closed road and the audit found 0 violations.

## How it is measured (caveats to state)

- **Insertions (part A)** use a stand-alone planning-time allocation. Each vehicle plans from its depot
  at 06:00, orders are offered in release order, and an order goes to the eligible vehicle whose plan
  gets the least extra time. Travel times are free-flow shortest paths under each vehicle's road rules.
  KR-11 cannot block here, because a vehicle can plan a later order and wait for its release (KR-7). A
  rejected pair is attributed to the rule the rule engine reports. "heuristic" would mean a plan above
  9 stops that the priority-first/EDF heuristic missed (`docs/heuristic_gap.md`); it occurred 0 times as
  committed and 2 times with the 10:00 shift end.
- **KR-3 (part B1)** measures a hypothetical, since the committed fleet has no large vehicles. Depots are
  put on the large-vehicle road network (675 of 2,377 nodes), exactly as
  `make_scenarios.py --large-share` would.
- **Closures (part B2)** come from R1's simulator with R7's planner and 10 disruptions per hour, run with
  each of the two allocations above. The disruption timeline depends on the seed, so all seed-0
  scenarios see the same 7 closures; route effects differ because the vehicles' routes differ. Replans
  by traffic spikes are in the JSON (`replans_changed_by_spike`), not the table.
- More seeds: `--seeds 0 1 2 3 4 5 6 7 8 9`. Not run for this report: the 10:00 shift-end run on the large
  scenarios spends most of its time in the exact stop-order search (`csp.find_sequence`) and did not
  finish one large scenario in 150 s, so 10 seeds would take hours.
