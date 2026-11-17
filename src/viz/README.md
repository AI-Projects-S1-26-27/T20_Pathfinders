# src/viz (R2)

| file | what |
|------|------|
| `plot_route.py` | one A* route on the Secunderabad map -> `docs/route_example.png` |
| `replay.py` | replay of a simulator JSONL log on the map (GIF/MP4 or PNG snapshot) |
| `demo_scenario.py` | writes `configs/viz_demo.yaml`, a 10-vehicle, 30-order demo for the replay |
| `multipane.py` | 2 or 3 logs side by side on one clock, per-pane HUD, negotiation flashes, playback keys |
| `demo_pair.py` | demo runs for the viewer: no replanning, independent, full (+ centralized with `--centralized`); not the experiment |
| `report_figures.py` | the report's map figures `reports/figures/F_region.png`, `F_routes.png`, `F_closure.png` (Week 7) |

## Replay (Week 4)

```bash
python -m src.viz.demo_scenario --vehicles 10 --orders 30 --seed 7
python -m src.sim.run --config configs/viz_demo.yaml
python -m src.viz.replay --log logs/viz_demo.jsonl --config configs/viz_demo.yaml \
    [--snapshot-t 06:33 --png docs/replay_example.png]
```

Output: `reports/replay/<run_id>.mp4` if ffmpeg is on PATH, else `.gif` (not committed).

Colours: vehicle blue = driving, red = waiting at a closed road (a `blocked_wait` row in the
last 60 s), grey = stationary; order hollow = open, green = delivered, red x = failed; thick
red road + ring = closed; yellow to red road = traffic spike (by multiplier).

For reuse: `load_log(path)` -> events, `state_at(events, t, config, graph)` -> plain
`ReplayState` (no matplotlib), `ReplayRenderer(ax, graph)` draws the base map once and
`update(state)` moves the dynamic artists.

HUD messages (Week 7): `messages: N so far (negotiation a, allocation b, central c)`, a
running total up to the frame time of every message row in the log: R5's
`negotiation_request` / `negotiation_response` / `negotiation_result` (each adds
`payload["count"]`, D7), R4's contract-net `allocation` rows (independent/full) and R4's
`central_state_report` / `central_plan_broadcast` rows (centralized), one message each. Only
the groups present in the log are listed. A log with none of these rows shows
`messages: not logged`, never `0`.

Measured on the 10-vehicle, 4 h demo (Week 4): 481 frames in 46.4 s (0.096 s per frame).

The state rules, timings, and gaps (straight-line motion, dynamic orders without a node not
drawn, order locations from the config, single run per figure) are in
[`docs/replay_viz.md`](../../docs/replay_viz.md).

## Side-by-side replay (Weeks 5-7)

```bash
python -m src.viz.demo_pair --centralized    # logs/pair_{noreplan,independent,full,centralized}.jsonl
python -m src.viz.multipane \
    --logs logs/pair_centralized.jsonl logs/pair_independent.jsonl logs/pair_full.jsonl \
    --labels "centralized" "independent" "full" --config configs/viz_demo.yaml --show
```

`demo_pair.py` runs independent and full through `src/sim/run.py` (R4's contract-net, R7's
planner, R5's trigger and negotiation in full), the no-replanning run with R1's simulator
alone, and with `--centralized` the centralized mode in a subprocess that is stopped after
10 min (`--centralized-timeout`); a stopped run leaves no log. It prints how many
disruption rows the logs share.

Logs with a different scenario or seed are refused (`--allow-mismatch` renders them under a
red "NOT A COMPARISON" banner). Keys in `--show`: space pause, `+`/`-` speed 0.25x-16x,
arrows step one frame, `r` restart. The multipane HUD shows `alloc / req / resp / result`
and, for R4's centralized rows, `central`; its total equals `replay.messages_at` for the
same log and time (in the Week 7 snapshot: centralized 880, independent 615, full 627).
Rules: [`docs/multipane_viz.md`](../../docs/multipane_viz.md).

### Week 6 hardening

```bash
python -m src.viz.demo_scenario --vehicles 20 --orders 60 --seed 7 --out logs/viz_demo20.yaml
python -m src.viz.demo_pair --config logs/viz_demo20.yaml --prefix pair20   # logs/pair20_*.jsonl
python -m src.viz.multipane --logs logs/pair20_noreplan.jsonl logs/pair20_independent.jsonl \
    --labels "no replanning" "independent" --config logs/viz_demo20.yaml \
    --snapshot-times 06:10 06:20 06:28 --png reports/replay/pair20.png      # one PNG per time
```

- GIF/MP4/PNG output draws each pane's roads once into an image (`PaneRenderer.prerender`);
  `--no-prerender` redraws them every frame as in Week 5. The picture differs by at most 1
  level (of 255) in at most 8 pixels. `--show` never pre-renders (it was slower there).
- Measured (20 vehicles, 121 frames, dpi 80): 3 panes 0.237 to 0.207 s per frame (-12.5%),
  2 panes 0.165 to 0.150 s. Most of a frame is text, not roads. The live window manages
  3-4 fps, not 30.
- Row layout height is fitted to the map (about 11% smaller figure). A pane whose log ended
  holds its last state and says `FINISHED`. `--config` is optional. Bad times, a bad JSON
  line, a missing config or 1 / 4 logs give a clear error.

## Report figures (Week 7)

```bash
python -m src.viz.report_figures          # about 22 s; [--out-dir reports/figures]
```

| figure | what |
|--------|------|
| `F_region.png` | the road network with the depots and order nodes of `configs/scenarios/medium_hotspot_s3.yaml` (blue: released at 06:00, orange: released later only) |
| `F_routes.png` | 4 A* routes (`src/search/astar.py`), node pairs from `plot_route.far_apart_pair` with seeds 42-45; legend gives km, free-flow minutes and nodes expanded A* vs Dijkstra |
| `F_closure.png` | two replay snapshots of one real run, before a closure and while it is active, made with `ReplayRenderer` from a log with a `replan` row whose trigger is `closure` |

The closure run is found by a fixed search: `small_hotspot_s0` ... `s9` in that order, mode
independent, 20 disruptions per hour, the first log with such a row. The script prints the
scenario, seed and rate, and the same text is the figure's caption. Simulator logs go to a
temporary directory; only the PNGs are kept. Deterministic: three runs (default,
`PYTHONHASHSEED=1`, `=2`) gave byte-identical PNGs. Details:
[`docs/replay_viz.md`](../../docs/replay_viz.md#report-figures-week-7).
