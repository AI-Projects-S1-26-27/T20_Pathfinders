# R3 · Rule-rejection statistics (Week 6)

Code: `src/kr/rejection_stats.py` · Tests: `tests/test_rejection_stats.py` ·
Full report: `reports/rule_rejections.md` and `.json` (committed)

```
python -m src.kr.rejection_stats            # 9 scenarios of seed 0, about 3 minutes
```

Same command, same report: two runs give byte-identical JSON and Markdown.

## Table for the report

How often R3's rules block an insertion or a road, on the 9 committed seed-0 scenarios (real Amazon
statistics, Secunderabad map; 1,800 orders, 30,000 order-vehicle checks):

| What is blocked | Rule | How often |
|---|---|---|
| Order offered to a vehicle (insertion) | KR-1 capacity | **28.1%** of 30,000 checks (8,435), 100% of all rejections |
| | KR-8 time window | 0 |
| | KR-10 back by shift end | 0 as committed; **4 checks (0.01%)** if every vehicle is due back by 10:00 (the only shift end tested) |
| | KR-11 release, KR-10a shift duration, no legal path | 0 |
| Order no vehicle can take | KR-15 | 0 of 1,800 orders |
| Road for a large vehicle | KR-3 road class | 81.3% of roads closed to them; **72.2% of depot -> order trips impossible**; trips that need a detour are 27.6% longer |
| Road during the run | KR-4 closure | 7 distinct closures, **3 of 7 changed a route** in at least one scenario; counted per scenario, 13 of 63 closure-scenario pairs changed a route (25 route changes in all); 0 vehicles stuck, 0 audit violations |

Two things to state next to the table:

- **"KR-1 caused 100% of rejections" depends on how it was measured.** The scenarios size fleet capacity so
  total demand is 80% of it (`docs/scenarios.md`), and part A assigns orders greedily at planning time (least
  extra time, in release order). A different capacity sizing or allocator would give a different split.
- **The closure counts come from the stand-in driver** of `scripts/run_audit_demo.py`, not from R4's
  allocation, so they will change once allocation is wired in (#38).

## What it means

- **Capacity is the only rule that bites at allocation time.** Every rejection in the committed scenarios is
  KR-1, and every order still finds some vehicle. Capacity was sized so total demand is 80% of fleet capacity
  (`docs/scenarios.md`), so late orders often meet full vans.
- **Time windows never block, and that follows from the data.** Only 6.6% of stops have a window in the
  Amazon data, and their median width is 8 hours (`docs/data_profile.md`), wider than the whole 4-hour
  simulated period. Inside the period they are almost never tight. Report this as a property of the
  data, not as evidence that KR-8 is unnecessary: the rule tests show it working on tight windows.
- **KR-10 rarely binds at a 10:00 shift end.** With every vehicle due back by 10:00, the end of the simulated
  period, KR-10 blocks 4 of 30,000 checks. An earlier shift end was not tested; it is a tighter limit, so it
  would block at least as many. A shift end in the scenarios would also let the audit check KR-10 (see
  `docs/kr_audit.md`, "Not checked").
- **KR-3 is why the fleet is small-only.** A large vehicle could not reach 72% of the order locations,
  because 81.3% of Secunderabad's directed roads (4,922 of 6,056) are closed to them: residential,
  living_street, service and unclassified together (`reports/rule_rejections.md`, part B1). Where it can reach them, most trips are the same
  length, but the ones that need a detour are 27.6% longer.
- **Some closures force a reroute, most do not.** The 9 seed-0 scenarios share the same 7 closures. 3 of the 7
  lay on some vehicle's remaining route in at least one scenario and changed it. Counted per scenario, 13 of
  63 closure-scenario pairs changed a route (25 route changes in all, since one closure can reroute several
  vehicles). With 7 closures from one seed, this is too small a sample for a rate. R7's planner always found a way round (0 vehicles waiting at a closed road), and the
  audit found 0 violations in these runs.

## How it is measured (caveats to state)

- **Insertions (part A)** use a planning-time view. Each vehicle plans from its depot at 06:00, orders are
  offered in release order, and an order goes to the eligible vehicle whose plan gets the least extra time.
  Travel times are free-flow shortest paths under each vehicle's road rules. KR-11 cannot block here,
  because a vehicle can plan a later order and wait for its release (KR-7). A rejected pair is attributed
  to the rule the rule engine reports. "heuristic" would mean a plan above 9 stops that the
  priority-first/EDF heuristic missed (`docs/heuristic_gap.md`); it occurred 0 times as committed and
  2 times with the 10:00 shift end.
- **KR-3 (part B1)** measures a hypothetical, since the committed fleet has no large vehicles. Depots are
  put on the large-vehicle road network (675 of 2,377 nodes), exactly as
  `make_scenarios.py --large-share` would.
- **Closures (part B2)** come from R1's simulator with R7's planner, `scripts/run_audit_demo.py`'s stand-in
  driver and 10 disruptions per hour. The disruption timeline depends on the seed, so all seed-0
  scenarios see the same 7 closures; route effects differ because the vehicles' routes differ. Replans by
  traffic spikes are in the JSON (`replans_changed_by_spike`), not the table.
- More seeds: `--seeds 0 1 2 3 4 5 6 7 8 9`. Not run for this report: the 10:00 shift-end run on the large
  scenarios spends most of its time in the exact stop-order search (`csp.find_sequence`) and did not finish one
  large scenario in 150 s, so 10 seeds would take hours.