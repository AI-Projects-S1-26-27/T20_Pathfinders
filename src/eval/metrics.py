"""R8 metrics for the repository's JSONL event schema.

Physical time is based on event timestamps. Metrics that require information
not present in the log are returned as None and explained in status fields.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from math import inf
from pathlib import Path
import json
import statistics

MESSAGE_EVENTS = {"negotiation_request", "negotiation_response", "negotiation_result", "allocation"}
ROAD_CAPACITY = {"residential": 1, "secondary": 2, "primary": 3, "trunk": 1,
                 "tertiary": 1, "living_street": 1, "unclassified": 1, "motorway": 1}


def read_jsonl(path):
    rows=[]
    with open(path, encoding="utf-8") as f:
        for n,line in enumerate(f,1):
            if not line.strip(): continue
            try: rows.append(json.loads(line))
            except json.JSONDecodeError as e: raise ValueError(f"{path}:{n}: invalid JSON: {e}") from e
    if not rows: raise ValueError(f"Empty log: {path}")
    return rows


def _num(x):
    try:
        v=float(x)
        return v if v == v and abs(v) != inf else None
    except (TypeError,ValueError): return None


def _edge_key(payload):
    e=payload.get("edge")
    return tuple(str(x) for x in e[:3]) if isinstance(e,(list,tuple)) and len(e)>=2 else None


def _load_graph(map_path):
    if not map_path or not Path(map_path).exists(): return None
    try:
        import networkx as nx
        return nx.read_graphml(map_path, force_multigraph=True)
    except Exception: return None


def _edge_attributes(graph, edge):
    if graph is None or edge is None: return {}
    try:
        u,v=edge[:2]
        if graph.is_multigraph():
            key=edge[2] if len(edge)>2 else None
            if key is not None and graph.has_edge(u,v,key): return graph.get_edge_data(u,v,key) or {}
            d=graph.get_edge_data(u,v) or {}
            return next(iter(d.values()),{}) if d else {}
        return graph.get_edge_data(u,v) or {}
    except Exception: return {}


def _jain(values):
    x=[max(0.0,float(v)) for v in values]
    return (sum(x)**2)/(len(x)*sum(v*v for v in x)) if x and sum(v*v for v in x)>0 else None


def _concurrency_metrics(intervals, capacities):
    by_edge=defaultdict(list)
    for edge,a,b in intervals:
        if edge is not None and b>a:
            by_edge[edge].extend([(a,1),(b,-1)])
    peak=0; overload=0.0; observed=0.0
    for edge,evs in by_edge.items():
        evs.sort(key=lambda z:(z[0],z[1])) # exits before entries at same timestamp
        active=0; prev=None; cap=capacities.get(edge,1)
        for t,d in evs:
            if prev is not None and t>prev:
                dt=t-prev; observed+=dt; overload+=max(0,active-cap)*dt
            active+=d; peak=max(peak,active); prev=t
    return (peak if intervals else None, overload/observed if observed else (0.0 if intervals else None))


def compute_run_metrics(rows, config=None, map_path=None):
    config=config or {}
    first=next((r for r in rows if r.get('event')=='sim_start'),{})
    start=_num((first.get('payload') or {}).get('start_time_s'))
    if start is None: start=_num(first.get('t')) or 0.0
    mode=(rows[0].get('mode') or config.get('mode'))
    orders={str(o.get('id')):o for o in config.get('orders',[])}
    release={}; delivered={}; failed={}; service_start={}; service_end={}
    wait_starts=defaultdict(list); wait_intervals=[]
    for r in rows:
        ev=r.get('event'); p=r.get('payload') or {}; oid=str(p.get('order_id',''))
        t=_num(r.get('t'))
        if ev=='new_order' and oid: release[oid]=_num(p.get('release_time'))
        elif ev=='order_delivered': delivered[oid]=r
        elif ev=='order_failed': failed[oid]=r
        elif ev=='service_start' and oid: service_start[oid]=t
        elif ev=='service_end' and oid: service_end[oid]=t
        elif ev=='wait_start': wait_starts[str(r.get('agent_id'))].append((t,p.get('wait_id'),p.get('reason')))
        elif ev=='wait_end':
            vid=str(r.get('agent_id')); wid=p.get('wait_id')
            starts=wait_starts[vid]
            idx=next((i for i in range(len(starts)-1,-1,-1) if wid is None or starts[i][1]==wid),None)
            if idx is not None:
                t0,_,reason=starts.pop(idx)
                if t is not None and t0 is not None and t>=t0: wait_intervals.append((vid,t0,t,reason))

    # Time in system is completion timestamp minus release timestamp.
    response_times=[]; on_time=[]; weighted_lateness=[]; service_by_vehicle=defaultdict(float)
    for oid,row in delivered.items():
        cfg=orders.get(oid,{})
        rel=_num(cfg.get('release_time'))
        if rel is None: rel=release.get(oid)
        if rel is not None and _num(row.get('t')) is not None: response_times.append(max(0.0,float(row['t'])-rel))
        ss=service_start.get(oid)
        if ss is None: # Legacy logs: explicitly mark this estimate, not an exact start.
            st=_num(cfg.get('service_time'))
            end=_num(row.get('t'))
            ss=end-st if st is not None and end is not None else None
        tws=_num(cfg.get('tw_start')); twe=_num(cfg.get('tw_end'))
        if ss is not None and tws is not None and twe is not None:
            on_time.append(tws <= ss <= twe)
            priority=int(cfg.get('priority',2)); weighted_lateness.append(max(1,4-priority)*max(0.0,ss-twe))
        se=service_end.get(oid)
        if ss is not None and se is not None and se>=ss: service_by_vehicle[str(row.get('agent_id','env'))]+=se-ss
        elif _num(cfg.get('service_time')) is not None: service_by_vehicle[str(row.get('agent_id','env'))]+=_num(cfg.get('service_time'))

    n_orders=len(set(orders)|set(release)) if orders or release else len(delivered)+len(failed)
    unresolved=max(0,n_orders-len(delivered)-len(failed))
    makespan=max((_num(r.get('t')) or start) for r in delivered.values())-start if delivered else None

    graph=_load_graph(map_path or config.get('map_path'))
    enters=defaultdict(list); intervals=[]; distance=0.0; distance_ok=graph is not None
    drive_by_vehicle=defaultdict(float)
    for r in rows:
        ev=r.get('event'); p=r.get('payload') or {}; vid=str(r.get('agent_id','env')); t=_num(r.get('t'))
        if ev=='edge_enter':
            edge=_edge_key(p); enters[vid].append((edge,t))
            attrs=_edge_attributes(graph,edge); length=_num(attrs.get('length'))
            if length is None: distance_ok=False
            else: distance+=length
        elif ev=='edge_exit' and enters[vid]:
            edge,t0=enters[vid].pop(0)
            if t is not None and t0 is not None and t>=t0:
                intervals.append((edge,t0,t)); drive_by_vehicle[vid]+=t-t0
    # Workload fairness uses observed driving + service durations, not expected edge ETAs.
    workload=defaultdict(float)
    for vid,v in drive_by_vehicle.items(): workload[vid]+=v
    for vid,v in service_by_vehicle.items(): workload[vid]+=v
    for v in config.get('vehicles',[]): workload.setdefault(str(v.get('id')),0.0)
    fairness=_jain(list(workload.values()))

    # Explicit wait intervals only. A dangling start (e.g. run ends during a wait)
    # is censored and not treated as a completed interval.
    waiting=sum(max(0.0,b-a) for _,a,b,_ in wait_intervals)
    capacities={}
    for edge,_,_ in intervals:
        attrs=_edge_attributes(graph,edge); hw=attrs.get('highway','unclassified')
        if isinstance(hw,list): hw=hw[0] if hw else 'unclassified'
        capacities[edge]=ROAD_CAPACITY.get(str(hw),1)
    peak,overload=_concurrency_metrics(intervals,capacities)

    # CPU: allocation_complete has two meanings in this repository. Its payload
    # includes order_id/winner in contract-net modes; centralized rows do not.
    cpu_disruption=[]; cpu_central=[]; cpu_contract=[]; cpu_replan_other=[]; cpu_all=[]
    coordination_cpu=None
    for r in rows:
        ev=r.get('event'); p=r.get('payload') or {}; c=_num(p.get('cpu_seconds'))
        if ev in ('replan','replan_kept') and c is not None:
            cpu_all.append(c)
            if p.get('trigger') in ('closure','traffic_spike'): cpu_disruption.append(c)
            else: cpu_replan_other.append(c)
        elif ev=='allocation_complete' and c is not None:
            cpu_all.append(c)
            is_contract=('order_id' in p) or (mode in ('independent','full') and p.get('mode') in ('independent','full'))
            if is_contract: cpu_contract.append(c)
            else: cpu_central.append(c)
        elif ev=='replan_summary': coordination_cpu=_num(p.get('coordination_cpu_seconds'))

    counts=Counter()
    for r in rows:
        ev=r.get('event'); p=r.get('payload') or {}
        if ev in ('negotiation_request','negotiation_response','negotiation_result'):
            n=_num(p.get('count')); counts[ev]+=int(n if n is not None else 1)
        elif ev=='allocation': counts['allocation']+=1
    negotiation=sum(counts[x] for x in ('negotiation_request','negotiation_response','negotiation_result'))
    # No predicted-conflict ID -> later violation linkage in the current schema.
    return {
        'run_id':rows[0].get('run_id'),'scenario':rows[0].get('scenario'),'seed':rows[0].get('seed'),
        'mode':mode,'rate':rows[0].get('rate'),'n_orders':n_orders,'delivered':len(delivered),
        'failed':len(failed),'unresolved_orders':unresolved,
        'delivery_time_total_s':sum(response_times) if response_times else None,
        'delivery_time_mean_s':statistics.mean(response_times) if response_times else None,
        'delivery_makespan_s':makespan,
        'distance_total_m':distance if distance_ok else None,
        'waiting_idle_s':waiting if wait_intervals else None,
        'congestion_peak_vehicles':peak,'congestion_mean_overload':overload,
        'jain_workload_fairness':fairness,
        'on_time_rate':sum(on_time)/len(on_time) if on_time else None,
        'priority_weighted_lateness_s':statistics.mean(weighted_lateness) if weighted_lateness else None,
        'unresolved_conflicts':None,
        'cpu_disruption_mean_s':statistics.mean(cpu_disruption) if cpu_disruption else None,
        'cpu_disruption_p95_s':sorted(cpu_disruption)[max(0,int(.95*len(cpu_disruption)+.999)-1)] if cpu_disruption else None,
        'cpu_all_logged_events_mean_s':statistics.mean(cpu_all) if cpu_all else None,
        'cpu_centralized_resolve_total_s':sum(cpu_central),
        'cpu_contract_net_allocation_total_s':sum(cpu_contract),
        'cpu_agent_replan_other_total_s':sum(cpu_replan_other),
        'cpu_coordination_total_s':coordination_cpu,
        'messages_total':negotiation+counts['allocation'],
        'messages_negotiation_total':negotiation,
        'messages_negotiation_request':counts['negotiation_request'],
        'messages_negotiation_response':counts['negotiation_response'],
        'messages_negotiation_result':counts['negotiation_result'],
        'messages_allocation':counts['allocation'],
        'metric_status_distance':'ok' if distance_ok else 'requires GraphML length for every traversed edge',
        'metric_status_conflicts':'unavailable: no predicted-conflict ID linked to a later realized violation',
        'metric_status_waiting':'explicit wait_start/wait_end intervals only; legacy logs without these events are unavailable',
        'metric_status_on_time':'exact with service_start events; legacy logs fall back to delivery time minus configured service_time',
        'metric_status_congestion':'peak occupancy from edge_enter/edge_exit; overload uses provisional road-class capacities',
    }
