"""Tests built from the repository's documented event names/payload shapes."""
from src.eval.metrics import compute_run_metrics
from src.eval.statistics import mean_ci, paired_bootstrap


def row(event, t, payload, agent="env", mode="full"):
    return {"run_id":"schema_test", "mode":mode, "seed":17, "scenario":"small_uniform",
            "rate":5.0, "t":float(t), "agent_id":agent, "event":event, "payload":payload}


def test_repository_negotiation_payload_counts_are_used():
    rows=[row("sim_start",0,{"start_time_s":0,"duration_seconds":100}),
          row("negotiation_request",10,{"count":1,"edge":[1,2,0]},"V1"),
          row("negotiation_response",11,{"count":1,"edge":[1,2,0]},"V1"),
          row("negotiation_result",12,{"count":2,"edge":[1,2,0],"vehicles":["V1","V2"],"winner":"V1","price":1,"ttc":4,"actions":{"V2":"wait"}})]
    m=compute_run_metrics(rows,{})
    assert m["messages_negotiation_request"]==1
    assert m["messages_negotiation_response"]==1
    assert m["messages_negotiation_result"]==2
    assert m["messages_negotiation_total"]==4


def test_allocation_complete_contract_net_is_not_centralized_cpu():
    rows=[row("sim_start",0,{"start_time_s":0,"duration_seconds":100}),
          row("allocation_complete",10,{"cpu_seconds":0.25,"messages":3,"mode":"full","order_id":"O1","winner":"V1"})]
    m=compute_run_metrics(rows,{})
    assert m["cpu_contract_net_allocation_total_s"]==0.25
    assert m["cpu_centralized_resolve_total_s"]==0


def test_centralized_allocation_complete_is_solver_cpu():
    rows=[row("sim_start",0,{"start_time_s":0,"duration_seconds":100},mode="centralized"),
          row("allocation_complete",10,{"cpu_seconds":0.5,"messages":2},mode="centralized")]
    m=compute_run_metrics(rows,{})
    assert m["cpu_centralized_resolve_total_s"]==0.5
    assert m["cpu_contract_net_allocation_total_s"]==0


def test_physical_metrics_use_explicit_service_and_wait_events():
    rows=[row("sim_start",0,{"start_time_s":0,"duration_seconds":100}),
          row("edge_enter",2,{"edge":["1","2",0],"expected_duration":10},"V1"),
          row("edge_exit",12,{"node":2},"V1"),
          row("wait_start",12,{"wait_id":"w1","reason":"time_window"},"V1"),
          row("wait_end",17,{"wait_id":"w1","reason":"time_window","duration_s":5},"V1"),
          row("service_start",17,{"order_id":"A","service_time":3,"tw_start":0,"tw_end":20},"V1"),
          row("service_end",20,{"order_id":"A"},"V1"),
          row("order_delivered",20,{"order_id":"A","node":2,"demand":1},"V1"),
          row("sim_complete",21,{"delivered":1,"failed":0})]
    cfg={"vehicles":[{"id":"V1"}],"orders":[{"id":"A","release_time":0,"service_time":3,"tw_start":0,"tw_end":20,"priority":1}]}
    m=compute_run_metrics(rows,cfg)
    assert m["delivery_time_total_s"]==20
    assert m["waiting_idle_s"]==5
    assert m["on_time_rate"]==1
    assert m["jain_workload_fairness"]==1


def test_missing_data_is_not_fabricated():
    rows=[row("sim_start",0,{"start_time_s":0,"duration_seconds":10})]
    m=compute_run_metrics(rows,{})
    assert m["distance_total_m"] is None
    assert m["waiting_idle_s"] is None
    assert m["unresolved_conflicts"] is None


def test_bootstrap_functions_are_deterministic():
    assert mean_ci([1,2,3],n_boot=200,seed=7)==mean_ci([1,2,3],n_boot=200,seed=7)
    paired=paired_bootstrap({1:10,2:12},{1:8,2:10},n_boot=200,seed=7)
    assert paired["mean_difference"]==2 and paired["n_pairs"]==2
