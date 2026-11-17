# R3 report sections: Data and KR&R (draft for R8 to merge)

Every number below comes from a committed, script-generated file, named in brackets. Rerun the script
and update the number if the file changes. Sections follow the report outline (guide 9.1): a course
concepts row, related work, the Data subsection, Method, Results (table T1) and Limitations.

The main sweep (R1/R8) has not run yet, so the audit numbers here are from the pilot sweep. Replace
them with `reports/audit_sweep.md` once the main sweep has run. `reports/rule_rejections.md` is on
main.

---

## Course concepts (one row of the table)

| Concept | Where in the system | What it does in the experiments |
|---|---|---|
| KR&R: production rules with forward chaining; CSP with backtracking, MRV, forward checking, AC-3/MAC | `src/kr/engine.py`, `rules.py`, `csp.py` (R3) | A tested library. In the final runs it does not make decisions: R4's allocation and R7's planner use their own checks. It is used as an independent audit of every run's log (`src/kr/audit.py`, table T1a), for the rule-rejection statistics (T1b), and by the scenario generator to check that every order can be served by some vehicle |

## Related work (KR&R and data; about four sentences)

Our feasibility rules are a forward-chaining production system in the style of Russell and Norvig
(2020, ch. 7-9). Assignment feasibility is a constraint satisfaction problem solved by backtracking
with the fail-first (MRV) ordering and forward checking of Haralick and Elliott (1980), and with arc
consistency (AC-3) from Mackworth (1977), both at the root and maintained during search (MAC). Demand
statistics come from the Amazon Last Mile Routing Research Challenge data set (Merchán et al., 2022/2024),
9,184 real routes from five US metro areas in 2018 with obfuscated locations. These are standard
techniques. Our contribution here is not a new reasoning method, but explicit, tested rules against which
every logged run is audited.

References (each opened, Nov 2026):
- Haralick, R. M., & Elliott, G. L. (1980). Increasing tree search efficiency for constraint
  satisfaction problems. *Artificial Intelligence*, 14(3), 263-313. https://doi.org/10.1016/0004-3702(80)90051-X
- Mackworth, A. K. (1977). Consistency in networks of relations. *Artificial Intelligence*, 8(1),
  99-118. https://doi.org/10.1016/0004-3702(77)90007-8
- Merchán, D., Arora, J., Pachon, J., Konduri, K., Winkenbach, M., Parks, S., & Noszek, J. (2024).
  2021 Amazon Last Mile Routing Research Challenge: Data set. *Transportation Science*, 58(1), 8-11
  (online 2022). https://doi.org/10.1287/trsc.2022.1173
- Russell, S., & Norvig, P. (2020). *Artificial Intelligence: A Modern Approach* (4th ed.). Pearson.

## Data subsection (demand part; R2 writes the map, R6 the traffic)

**Source and licence.** Amazon Last Mile Routing Research Challenge 2021, training split, AWS Open
Data; licence CC BY-NC 4.0 (non-commercial, credit Amazon). We use `route_data.json` and
`package_data.json` (454.4 MB, all 6,112 training routes; `docs/data_profile.md`). The raw data is
not committed.

**What we take, and what we do not.** We take only statistics: packages per stop, package volume,
service time per stop, how often a stop has a time window, and the window's width and start. We do
not take locations. The coordinates are obfuscated, so no geography transfers. Order locations are
sampled from 8 k-means demand zones on our Secunderabad map, in three layouts (uniform, clustered,
hotspot). Priorities (20/50/30 express/standard/economy) and the share of orders released during the
run (about 30%) are our assumptions, because the data has neither.

**Generated vs empirical** (`reports/scenario_stats.md`, 18,000 orders in the 90 committed scenarios:
3 sizes x 3 layouts x 10 seeds):
- 7.0% of generated orders have a time window. In Amazon, 6.6% of stops and 7.8% of packages have one.
- Generated windows are clipped to our 4-hour simulated period (median 3.8 h), while Amazon's median
  window is 8 h. Inside our period, windows are therefore rarely tight.
- Service time per stop is sampled from Amazon's per-stop values (median 55.5 s, p95 178 s generated).
  Amazon repeats one stop-level service time on every package (98.6% of multi-package stops), so a
  stop's time is that value, not the sum over its packages.
- Priority split 20.1/49.7/30.3%, and 28.7% of orders released during the run, match the stated
  assumptions. No order is unservable by every vehicle.

**Transfer limits (state plainly).** US data from 2018, not Hyderabad. Only demand statistics
transfer, not spatial patterns. Priorities and release times are assumed. Windows are shortened to
fit a 4-hour period.

## Method: knowledge representation and reasoning

**Ontology.** Vehicle (capacity, load, depot, allowed road classes, class small/large, shift end),
Order (node, demand, priority, time window, service time, release time), Road (class, free-flow time,
closed or not), and Zone (nodes, banned classes, hours). Demand and capacity share one unit, the
median Amazon package volume. Times are seconds since midnight (`docs/kr_ontology_rules.md`).

**Rules.** 17 rules (KR-1 to KR-16, plus KR-10a). 15 are hard, for example KR-1 capacity, KR-3 road
class, KR-4 no closed roads, KR-8 serve by the window end, KR-11 not before release, and KR-12 served
once. KR-13 (express stops first) and KR-14 (assign express orders quickly) are soft. KR-13x and
KR-13y are overrides (earliest deadline first, then an exact search for the stop order).

**Inference.** Forward chaining over a working memory of facts. A rule fires when its conditions
match, adds derived facts (load_total -> capacity_ok, service_total -> shift_duration_ok -> sequence),
and every firing is written to an explanation trace. We chose forward rather than backward chaining
because the question is data-driven: given an assignment, derive everything that follows and report
every rule that fails. We are not proving one goal. Refraction stops a rule firing twice on the same
facts.

**Conflict resolution.** Salience classes, highest first: safety/legality (KR-3, KR-4, KR-5, KR-6,
KR-11) > physical limits (KR-1, KR-2, KR-10a) > commitments (KR-8, KR-10, KR-12) > preferences (KR-13,
KR-14). A higher class can veto a lower one. When express-first breaks a window, the trace reads
"KR-13 overridden by KR-8" and the engine retries earliest deadline first.

**CSP.** Variables: orders. Domain of an order: the vehicles allowed to take it. Constraints: capacity
per vehicle, plus a schedule check per vehicle (release, windows, shift end, using travel times).
Search: backtracking with MRV (degree tie-break), LCV, forward checking, and AC-3 at the root or
maintained (MAC). Following the guide's pitfall, there is no global CSP over 300 orders. The CSP is a
per-order filter that tells R4 which vehicles may bid. The stop order of one vehicle is searched
exactly up to 9 stops (`csp.find_sequence`); above that, the priority-first / earliest-deadline-first
heuristic is used.

**Integration.** `road_allowed` and closures become blocked edges in R2's and R7's searches
(`planner_rules.RoadRules`), priorities order the stops (`priority_stops`), and `kr_check` rows go to
the log (`docs/kr_planner_integration.md`). The final runs call none of these: R4's allocation
(contract-net and OR-Tools) uses its own feasibility checks, and R7's planner gets closures from R1's
world. So the audit below is an independent check of the outcome, not a test of R3's engine.

**Verification.** Every rule has a positive and a negative unit test (`tests/test_rule_coverage.py`).
The CSP equals brute force on random small instances and agrees with python-constraint. AC-3 never
removed a value used by a brute-force solution. `find_sequence` equals trying every permutation
(`docs/csp.md`).

## Results: table T1 (constraint-violation audit and rule-rejection statistics)

**T1a. Audit** (`reports/audit_sweep_pilot.md`; replace with `reports/audit_sweep.md` from the main
sweep):

| Runs | Road entries checked | Deliveries checked | Violations | Planted violations caught |
|---|---|---|---|---|
| 42: 9 scenarios x independent/full x 2 seeds, plus 3 small scenarios x centralized x 2 seeds; 10 disruptions/h | 66,831 | 7,800 | **0** | 8 of 8, in each mode |

State with it what the zero covers. It covers 12 checks: served once, delivery node, release, window
start and end, service after arrival, capacity, closures, road direction, road class, zones, and path
integrity. It does not cover KR-10 (no shift end in the scenarios) or allocation decisions (KR-2,
KR-13-KR-15). KR-3 cannot fail with a small-only fleet, and there are no zones for KR-6. Centralized
runs log deliveries without `node`/`demand`, so for them NODE is checked by the vehicle's position.

**T1b. How often rules block** (`reports/rule_rejections.md`, 9 seed-0 scenarios: 1,800 orders,
30,000 order-vehicle checks; the two-allocation B2 result is from PR #46). The insertion and
large-vehicle rows are stand-alone measurements of the rules, not of the system the experiments run.
The closure row gives both.

| What is blocked | Rule | How often |
|---|---|---|
| Insertion of an order into a vehicle, in a stand-alone greedy allocation | KR-1 capacity | 28.1% of checks; all 8,435 rejections |
| | KR-8 time window | 0 |
| | KR-10 back by shift end | 0 as committed; 4 checks if every vehicle is due back by 10:00 (only shift end tested) |
| Order no vehicle can take | KR-15 | 0 of 1,800 |
| Road for a large vehicle, from 3 hypothetical depots | KR-3 | 81.3% of roads closed to them (4,922 of 6,056; 4,800 of them residential); 1,299 of 1,800 (order node, depot) trips impossible (600 order nodes x 3 depots); detoured trips 27.6% longer |
| Road during the run | KR-4 closure | Contract-net, the allocation the experiments use: 0 of 7 distinct closures changed a route (0 of 63 closure-scenario pairs, no route check triggered). Stand-in hand-written routes: 3 of 7 (13 of 63 pairs). Both: 0 vehicles stuck, 0 audit violations |

Notes to keep next to T1b:
- The insertion rows come from a stand-alone greedy allocation at planning time (least extra time, in
  release order), not from R4's contract-net, which uses its own capacity checks; R3's rules are not
  called in the experiment runs. That KR-1 is the only rule that blocks follows from the scenarios:
  fleet capacity is sized so demand is 80% of it, and every order is loaded at 06:00 with no unloading.
- A large-vehicle trip is an (order node, hypothetical depot) pair, not an order; that there are also
  1,800 orders is a coincidence. Most trips are impossible because of the rule and the map together.
- In our runs, closures did not force reroutes: with contract-net no closure lay on a vehicle's
  remaining route. With the stand-in's routes, fixed in advance, the same closures did force
  reroutes. With 7 closures from one seed, neither is a rate.

**Supporting numbers for the text** (`docs/csp_pruning.md`, 30 seeds; `docs/heuristic_gap.md`):
- 12 orders x 4 vehicles. Mean nodes: plain backtracking 46,342 (11 of 30 hit the 100,000-node limit),
  with forward checking 6,140, with MAC 3,839. AC-3 at the root alone pruned nothing extra, and MRV
  without forward checking gave the same node counts as plain backtracking.
- The above-9-stop heuristic never accepted an infeasible route in 1,800 instances. It rejected
  3.7-26.2% of feasible routes at 10-15 stops with 20-60 min windows, and 12.8-44.4% with 10-30 min
  windows. Exact search took at most 8.3 ms at 15 stops.

## Limitations (KR&R and data)

- Demand is US 2018 statistics; Hyderabad demand may differ, and only statistics transfer.
- Priorities, release times and the 80% capacity sizing are our assumptions. The rejection split
  depends on them, and it is measured on a stand-alone allocation, not on R4's.
- Generated windows are clipped to 4 hours, so KR-8 almost never binds. This is a property of the
  data and the period, not evidence that the rule is unnecessary.
- The fleet is small-only, so KR-3 is measured as a hypothetical (B1) and cannot fail in the audit.
  KR-6 zones are not defined for our map. KR-10 is not exercised, because no scenario sets a shift end.
- The audit checks outcomes in the log, not allocation decisions. Its zero is on the 12 listed checks.
