"""Report figures that need the road map (R2, Week 7). One command, deterministic:

    python -m src.viz.report_figures

writes
  reports/figures/F_region.png   the Secunderabad road network with the depots and order
                                 nodes of configs/scenarios/medium_hotspot_s3.yaml
  reports/figures/F_routes.png   a few A* routes (src/search/astar.py) between node pairs
                                 picked as in src/viz/plot_route.py (fixed seeds)
  reports/figures/F_closure.png  two replay snapshots of one real run, before a closure
                                 and while it is active, drawn with the replay renderer
                                 (src/viz/replay.py) from a log that has a `replan` row
                                 with trigger "closure"

The closure run is found by a fixed search: small_hotspot scenarios with seeds
CLOSURE_SEEDS in that order (configs/scenarios/small_hotspot_s<seed>.yaml, whose own
seed is <seed>), mode independent, CLOSURE_RATE disruptions per hour; the first log with
such a row is used. Each run goes through src.sim.run.run_from_config into a temporary
directory (only the PNGs are kept). The script prints the scenario, seed and rate, and the
same text is the figure's caption. Nothing here draws random numbers of its own: the
pairs come from plot_route.far_apart_pair (numpy, fixed seed) and the runs from the
simulator's seeded generator.
"""
import argparse
import math
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from src.viz.replay import (COLORS, ReplayRenderer, format_clock, load_log, state_at)

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "reports" / "figures"
MAP_FILE = REPO / "data" / "processed" / "secunderabad.graphml"
REGION_SCENARIO = REPO / "configs" / "scenarios" / "medium_hotspot_s3.yaml"
ROUTE_SEEDS = (42, 43, 44, 45)            # 42 is plot_route.py's SEED (docs/route_example.png)
CLOSURE_SCENARIO = "small_hotspot"
CLOSURE_SEEDS = tuple(range(10))          # small_hotspot_s0 ... s9, tried in this order
CLOSURE_RATE = 20.0                       # the highest D11 rate (lambda in {0,2,5,10,20}/h)
CLOSURE_MODE = "independent"
BEFORE_S = 60.0                           # "before" snapshot: this long before the closure
AFTER_S = 120.0                           # "active" snapshot: this long after the replan
ZOOM_MIN_M = 1500.0                       # closure panels show at least this many metres

# Categorical slots 1, 2, 3, 7 of the dataviz reference palette (light surface),
# checked with its validate_palette.js; each route is also numbered on the map.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#6250d6")
LABEL_OFFSETS = ((14, 10), (-14, 10), (14, -12), (-14, -12))    # points, per route
INK = "#222222"
INK_MUTED = "#555555"


# ---------------------------------------------------------------------------
# Shared drawing
# ---------------------------------------------------------------------------

def base_map(ax, graph) -> ReplayRenderer:
    """The replay renderer's road map (same look as the replays), no HUD, no legend."""
    renderer = ReplayRenderer(ax, graph, show_legend=False)
    renderer.hud.set_visible(False)
    return renderer


def xy(graph, node) -> Tuple[float, float]:
    d = graph.nodes[node]
    return float(d["lon"]), float(d["lat"])


def metres_per_deg(lat: float) -> Tuple[float, float]:
    """(metres per degree of longitude, of latitude) at this latitude."""
    return 111_320.0 * math.cos(math.radians(lat)), 110_574.0


def scale_bar(ax, km: float = 1.0, right: bool = False) -> None:
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    m_lon, _ = metres_per_deg((y0 + y1) / 2)
    length = km * 1000.0 / m_lon
    x = x1 - 0.04 * (x1 - x0) - length if right else x0 + 0.04 * (x1 - x0)
    y = y0 + 0.04 * (y1 - y0)
    ax.plot([x, x + length], [y, y], color=INK, lw=2.5, solid_capstyle="butt", zorder=9)
    ax.text(x + length / 2, y + 0.012 * (y1 - y0), f"{km:g} km", ha="center", va="bottom",
            fontsize=8, color=INK, zorder=9)


def save(fig, path: Path, dpi: int = 130) -> Path:
    import matplotlib.pyplot as plt
    path.parent.mkdir(parents=True, exist_ok=True)
    # No "Software" metadata: the PNG bytes then depend only on the drawing.
    fig.savefig(path, dpi=dpi, facecolor="white", metadata={"Software": None})
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# (a) F_region: road network, depots, order nodes
# ---------------------------------------------------------------------------

def region_points(config: Dict[str, Any]) -> Dict[str, List[int]]:
    """Depot nodes and order nodes (released at the start / later), each sorted, unique."""
    start = float(config.get("start_time_s", 21600.0))
    depots = sorted({int(v["depot_node"]) for v in config.get("vehicles", [])})
    at_start, later = set(), set()
    for o in config.get("orders", []):
        (later if float(o.get("release_time", 0.0)) > start else at_start).add(int(o["node"]))
    return {"depots": depots, "orders_start": sorted(at_start),
            "orders_later": sorted(later - at_start)}


def region_figure(graph, config: Dict[str, Any], out: Path) -> Dict[str, Any]:
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    pts = region_points(config)
    n_orders = len(config.get("orders", []))
    start = float(config.get("start_time_s", 21600.0))
    n_later = sum(1 for o in config.get("orders", []) if float(o.get("release_time", 0)) > start)
    fig, ax = plt.subplots(figsize=(8, 8.6))
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.08, top=0.9)
    base_map(ax, graph)
    on_map = {k: [n for n in v if n in graph] for k, v in pts.items()}
    for key, color, label in (("orders_start", SERIES[0], "released at 06:00"),
                              ("orders_later", SERIES[1], "released later")):
        if on_map[key]:
            xs, ys = zip(*(xy(graph, n) for n in on_map[key]))
            ax.scatter(xs, ys, s=22, color=color, edgecolors="white", linewidths=0.6,
                       zorder=5, label=label)
    xs, ys = zip(*(xy(graph, n) for n in on_map["depots"]))
    ax.scatter(xs, ys, s=150, marker="s", color=INK, edgecolors="white", linewidths=1.5,
               zorder=7)
    for i, n in enumerate(on_map["depots"], start=1):
        ax.annotate(f"D{i}", xy(graph, n), xytext=(7, 5), textcoords="offset points",
                    fontsize=9, color=INK, weight="bold", zorder=8)
    scale_bar(ax)
    scenario = f"{config.get('scenario')} (seed {config.get('seed')})"
    ax.set_title(f"Secunderabad road network, scenario {scenario}\n"
                 f"{len(on_map['depots'])} depots, {n_orders} orders on "
                 f"{len(pts['orders_start']) + len(pts['orders_later'])} nodes "
                 f"({n_orders - n_later} released at 06:00, {n_later} later)",
                 fontsize=11, color=INK)
    handles = [
        Line2D([], [], marker="s", ls="", color=INK, mec="white", ms=9, label="depot"),
        Line2D([], [], marker="o", ls="", color=SERIES[0], mec="white", ms=6,
               label="order node, released at 06:00"),
        Line2D([], [], marker="o", ls="", color=SERIES[1], mec="white", ms=6,
               label="order node, released later only"),
        Line2D([], [], color=COLORS["road_major"], lw=1.8, label="major road"),
        Line2D([], [], color=COLORS["road"], lw=0.8, label="other road"),
    ]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.005), ncol=3,
              fontsize=8, frameon=False)
    save(fig, out)
    return {"depots": len(on_map["depots"]), "orders": n_orders, "later": n_later,
            "order_nodes": len(pts["orders_start"]) + len(pts["orders_later"]),
            "missing_nodes": sum(len(v) - len(on_map[k]) for k, v in pts.items())}


# ---------------------------------------------------------------------------
# (b) F_routes: a few A* routes
# ---------------------------------------------------------------------------

def route_pairs(graph, seeds: Sequence[int] = ROUTE_SEEDS) -> List[Tuple[int, int]]:
    """plot_route.far_apart_pair for each seed, duplicates dropped, in seed order."""
    from src.viz.plot_route import far_apart_pair
    pairs: List[Tuple[int, int]] = []
    for seed in seeds:
        pair, _ = far_apart_pair(graph, seed=seed)
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def plan_routes(graph, pairs) -> List[Dict[str, Any]]:
    """A* (with R2's heuristic) per pair, Dijkstra for the expansion count only."""
    from src.search.astar import astar
    from src.search.dijkstra import dijkstra
    from src.search.heuristics import make_heuristic

    heuristic = make_heuristic(graph)
    routes = []
    for source, target in pairs:
        result = astar(graph, source, target, heuristic=heuristic)
        baseline = dijkstra(graph, source, target)
        # as plot_route.py: length of parallel edge 0
        km = sum(graph[u][v][0]["length"] for u, v in zip(result.path, result.path[1:])) / 1000
        routes.append({"source": source, "target": target, "path": list(result.path),
                       "km": km, "minutes": result.cost / 60, "expanded": result.expanded,
                       "dijkstra_expanded": baseline.expanded,
                       "same_cost": math.isclose(result.cost, baseline.cost, rel_tol=1e-9)})
    return routes


def routes_figure(graph, routes, out: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    fig, ax = plt.subplots(figsize=(8, 8.9))
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.13, top=0.92)
    base_map(ax, graph)
    handles = []
    for i, (r, color) in enumerate(zip(routes, SERIES), start=1):
        xs, ys = zip(*(xy(graph, n) for n in r["path"]))
        ax.plot(xs, ys, color=color, lw=2.6, solid_capstyle="round", zorder=4 + i * 0.1)
        ax.scatter([xs[0]], [ys[0]], s=70, color=color, edgecolors="white", linewidths=1.5,
                   zorder=6)
        ax.scatter([xs[-1]], [ys[-1]], s=80, marker="s", color=color, edgecolors="white",
                   linewidths=1.5, zorder=6)
        # Far-apart pairs share corners of the map: fan the number labels out per route.
        offset = LABEL_OFFSETS[(i - 1) % len(LABEL_OFFSETS)]
        for x, y in ((xs[0], ys[0]), (xs[-1], ys[-1])):
            ax.annotate(str(i), (x, y), xytext=offset, textcoords="offset points",
                        fontsize=9, color=INK, weight="bold", ha="center", va="center",
                        zorder=8, bbox=dict(boxstyle="circle,pad=0.2", facecolor="white",
                                            edgecolor=color, lw=1.2))
        handles.append(Line2D([], [], color=color, lw=2.6, label=(
            f"{i}: {r['km']:.2f} km, {r['minutes']:.1f} min free-flow; nodes expanded "
            f"A* {r['expanded']:,} vs Dijkstra {r['dijkstra_expanded']:,}")))
    handles += [Line2D([], [], marker="o", ls="", color=INK_MUTED, mec="white", ms=8,
                       label="start"),
                Line2D([], [], marker="s", ls="", color=INK_MUTED, mec="white", ms=8,
                       label="end")]
    scale_bar(ax, right=True)
    ax.set_title(f"{len(routes)} A* routes on the Secunderabad road map (src/search/astar.py)\n"
                 f"node pairs: plot_route.far_apart_pair, seeds "
                 f"{', '.join(str(s) for s in ROUTE_SEEDS)}", fontsize=11, color=INK)
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.0, -0.005), ncol=1,
              fontsize=8, frameon=False, handlelength=2.2)
    save(fig, out)


# ---------------------------------------------------------------------------
# (c) F_closure: a real closure and a replan, two replay snapshots
# ---------------------------------------------------------------------------

def first_closure_replan(events) -> Optional[dict]:
    """The first `replan` row whose trigger is "closure", or None."""
    for e in events:
        if e["event"] == "replan" and e["payload"].get("trigger") == "closure":
            return e
    return None


def closure_for(events, replan: dict) -> Optional[dict]:
    """The closure disruption that the replan reacted to: same (u, v), active at the
    replan time (t <= t_replan < expiry); the latest one if several."""
    edge = replan["payload"].get("edge") or []
    if len(edge) < 2:
        return None
    u, v, t = int(edge[0]), int(edge[1]), float(replan["t"])
    found = None
    for e in events:
        p = e["payload"]
        if (e["event"] == "disruption" and p.get("type") == "closure"
                and int(p["edge"][0]) == u and int(p["edge"][1]) == v
                and float(e["t"]) <= t < float(p["expiry"])):
            found = e
    return found


def detour_entry(events, replan: dict, closure: dict) -> Optional[dict]:
    """The replanning vehicle's first edge_enter out of the closed road's start node u
    after the replan and while the closure is active (where it turns off), or None."""
    vid, u = str(replan["agent_id"]), int(closure["payload"]["edge"][0])
    t0, expiry = float(replan["t"]), float(closure["payload"]["expiry"])
    for e in events:
        if (e["event"] == "edge_enter" and str(e["agent_id"]) == vid
                and t0 <= float(e["t"]) < expiry and int(e["payload"]["edge"][0]) == u):
            return e
    return None


def snapshot_times(closure: dict, replan: dict, run_start: float,
                   detour: Optional[dict] = None) -> Tuple[float, float]:
    """(before, active). before: BEFORE_S before the closure (not before the run).
    active: AFTER_S / 4 after the vehicle turned off at the closed road's start node
    (detour row), else AFTER_S after the replan; never past the closure's end."""
    t_c, expiry = float(closure["t"]), float(closure["payload"]["expiry"])
    before = max(run_start, t_c - BEFORE_S)
    if detour is not None:
        active = float(detour["t"]) + AFTER_S / 4
    else:
        active = float(replan["t"]) + AFTER_S
    return before, max(min(active, expiry - 1.0), t_c)


def scenario_path(scenario: str, seed: int) -> Path:
    return REPO / "configs" / "scenarios" / f"{scenario}_s{seed}.yaml"


def find_closure_run(log_dir: Path, seeds: Sequence[int] = CLOSURE_SEEDS,
                     scenario: str = CLOSURE_SCENARIO, rate: float = CLOSURE_RATE,
                     mode: str = CLOSURE_MODE, verbose: bool = True) -> Optional[Dict[str, Any]]:
    """Run each seed in order until a log has a closure-triggered replan."""
    from src.sim.run import run_from_config

    for seed in seeds:
        path = scenario_path(scenario, seed)
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        run_id = f"{path.stem}_{mode}_r{rate:g}"
        config.update(mode=mode, disruption_rate_per_hour=float(rate), run_id=run_id,
                      log_path=str(log_dir / f"{run_id}.jsonl"))
        t0 = time.perf_counter()
        events = load_log(run_from_config(config))
        replan = first_closure_replan(events)
        closures = sum(1 for e in events if e["event"] == "disruption"
                       and e["payload"].get("type") == "closure")
        if verbose:
            print(f"  {path.name}: seed {config['seed']}, rate {rate:g}/h, {mode}: "
                  f"{closures} closures, closure replan "
                  f"{'at ' + format_clock(replan['t']) if replan else 'none'} "
                  f"({time.perf_counter() - t0:.1f} s)")
        if replan is not None:
            closure = closure_for(events, replan)
            if closure is None:
                raise RuntimeError(f"{path.name}: replan at {replan['t']} has no matching "
                                   "closure row")
            return {"config": config, "events": events, "replan": replan,
                    "closure": closure, "path": path}
    return None


def closure_caption(found: Dict[str, Any], before: float, active: float,
                    detour: Optional[dict] = None) -> str:
    cfg, rp, cl = found["config"], found["replan"], found["closure"]
    u, v = cl["payload"]["edge"][:2]
    turn = ""
    if detour is not None:
        w = detour["payload"]["edge"][1]
        turn = (f" At {_hms(detour['t'])} it left node {u} on road {u}->{w}, "
                f"not on the closed road.")
    return (f"Run: scenario {cfg['scenario']} ({found['path'].name}), seed {cfg['seed']}, "
            f"rate {float(cfg['disruption_rate_per_hour']):g}/h, mode {cfg['mode']}; the "
            f"first of seeds {CLOSURE_SEEDS[0]}-{CLOSURE_SEEDS[-1]} whose log has a replan "
            f"row with trigger \"closure\".\n"
            f"Road {u}->{v} closed at {_hms(cl['t'])} for "
            f"{float(cl['payload']['duration']) / 60:.1f} min; {rp['agent_id']} replanned at "
            f"{_hms(rp['t'])}.{turn}\n"
            f"Left: {_hms(before)}, before the closure. Right: {_hms(active)}, closure "
            f"active.")


def _hms(t: float) -> str:
    s = int(round(float(t)))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def driven_after(events, vehicle: str, t0: float, t1: float) -> List[Tuple[int, int]]:
    """Edges the vehicle entered in [t0, t1] (from its edge_enter rows)."""
    return [(int(e["payload"]["edge"][0]), int(e["payload"]["edge"][1])) for e in events
            if e["event"] == "edge_enter" and str(e["agent_id"]) == vehicle
            and t0 <= float(e["t"]) <= t1]


def _zoom(ax, graph, points: List[Tuple[float, float]]) -> None:
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    cx, cy = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    m_lon, m_lat = metres_per_deg(cy)
    half_m = max(ZOOM_MIN_M / 2, (max(lons) - min(lons)) * m_lon * 0.65,
                 (max(lats) - min(lats)) * m_lat * 0.65)
    ax.set_xlim(cx - half_m / m_lon, cx + half_m / m_lon)
    ax.set_ylim(cy - half_m / m_lat, cy + half_m / m_lat)


def closure_figure(graph, found: Dict[str, Any], out: Path) -> Dict[str, Any]:
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    events, config = found["events"], found["config"]
    replan, closure = found["replan"], found["closure"]
    vid = str(replan["agent_id"])
    run_start = float(config.get("start_time_s", 21600.0))
    detour = detour_entry(events, replan, closure)
    before, active = snapshot_times(closure, replan, run_start, detour)
    caption = closure_caption(found, before, active, detour)

    fig, axes = plt.subplots(1, 2, figsize=(13, 8.6))
    fig.subplots_adjust(left=0.01, right=0.99, top=0.80, bottom=0.19, wspace=0.04)
    u, v = (int(x) for x in closure["payload"]["edge"][:2])
    path = driven_after(events, vid, float(replan["t"]), active)
    states = []
    for ax, t, legend in ((axes[0], before, True), (axes[1], active, False)):
        renderer = ReplayRenderer(ax, graph, show_legend=legend)
        state = state_at(events, t, config, graph)
        renderer.update(state)
        for label in renderer.labels:       # vehicles outside the zoom: no stray labels
            label.set_clip_on(True)
        states.append(state)
    mid = tuple((a + b) / 2 for a, b in zip(xy(graph, u), xy(graph, v)))
    for ax, text in ((axes[0], f"closes at {_hms(closure['t'])}"),
                     (axes[1], f"closed until {_hms(closure['payload']['expiry'])}")):
        ax.annotate(text, mid, xytext=(30, -38), textcoords="offset points", fontsize=8,
                    color=COLORS["closure"], weight="bold", zorder=10,
                    arrowprops=dict(arrowstyle="-", color=COLORS["closure"], lw=1),
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                              edgecolor="none", alpha=0.85))
    pts = [xy(graph, u), xy(graph, v)]
    for s in states:
        vs = s.vehicles.get(vid)
        if vs is not None and vs.lat is not None:
            pts.append((vs.lon, vs.lat))
    pts += [xy(graph, n) for edge in path for n in edge]
    for ax, s in zip(axes, states):
        _zoom(ax, graph, pts)
        vs = s.vehicles.get(vid)
        if vs is not None and vs.lat is not None:
            ax.scatter([vs.lon], [vs.lat], s=420, facecolors="none", edgecolors=INK,
                       linewidths=1.6, linestyle="--", zorder=8)
        scale_bar(ax, 0.25)
    if path:
        segs = [[xy(graph, a), xy(graph, b)] for a, b in path]
        from matplotlib.collections import LineCollection
        axes[1].add_collection(LineCollection(segs, colors=SERIES[0], linewidths=3.2,
                                              alpha=0.55, zorder=2))
        axes[1].legend(handles=[
            Line2D([], [], color=SERIES[0], lw=3.2, alpha=0.55,
                   label=f"edges {vid} entered from its replan to {_hms(active)} "
                         f"(log edge_enter rows)"),
            Line2D([], [], marker="o", ls="", mfc="none", mec=INK, ms=12,
                   label=f"{vid}, the vehicle that replanned")],
            loc="upper center", bbox_to_anchor=(0.5, -0.01), fontsize=7.5, frameon=False)
    fig.text(0.5, 0.012, caption, ha="center", va="bottom", fontsize=8.5, color=INK_MUTED,
             wrap=True)
    save(fig, out)
    return {"caption": caption, "before": before, "active": active, "vehicle": vid,
            "detour": detour,
            "edges_after": len(path),
            "closures_active": [s.counters["closures_active"] for s in states]}


# ---------------------------------------------------------------------------

def load_graph(map_path: Path = MAP_FILE):
    from src.world.graph_loader import load_road_graph
    return load_road_graph(Path(map_path).stem)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Write the map figures of the report.")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    import matplotlib
    matplotlib.use("Agg")
    out_dir = Path(args.out_dir)
    t0 = time.perf_counter()
    graph = load_graph()

    with open(REGION_SCENARIO, encoding="utf-8") as f:
        region_cfg = yaml.safe_load(f)
    info = region_figure(graph, region_cfg, out_dir / "F_region.png")
    print(f"F_region.png: {REGION_SCENARIO.name}, {info['depots']} depots, {info['orders']} "
          f"orders on {info['order_nodes']} nodes ({info['later']} released later), "
          f"{info['missing_nodes']} nodes not on the map")

    routes = plan_routes(graph, route_pairs(graph))
    routes_figure(graph, routes, out_dir / "F_routes.png")
    for i, r in enumerate(routes, start=1):
        print(f"F_routes.png route {i}: {r['source']} -> {r['target']}, {r['km']:.2f} km, "
              f"{r['minutes']:.1f} min, expanded A* {r['expanded']} vs Dijkstra "
              f"{r['dijkstra_expanded']}, same cost as Dijkstra: {r['same_cost']}")

    print(f"F_closure.png: searching {CLOSURE_SCENARIO} seeds {list(CLOSURE_SEEDS)}, "
          f"{CLOSURE_MODE}, {CLOSURE_RATE:g}/h")
    with tempfile.TemporaryDirectory(prefix="report_figures_") as tmp:
        found = find_closure_run(Path(tmp))
        if found is None:
            raise SystemExit("no seed in CLOSURE_SEEDS gave a replan row with trigger "
                             "'closure': F_closure.png not written")
        cl = closure_figure(graph, found, out_dir / "F_closure.png")
    print(cl["caption"])
    print(f"F_closure.png: closures active at the two times {cl['closures_active']}, "
          f"{cl['edges_after']} edges entered by {cl['vehicle']} after the replan")
    print(f"done in {time.perf_counter() - t0:.1f} s -> {out_dir}")


if __name__ == "__main__":
    main()
