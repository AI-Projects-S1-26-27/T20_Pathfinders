# Audit demo (scripts/run_audit_demo.py)

Stand-in driver (orders by deadline, dealt round-robin), R1's simulator, then `src.kr.audit` with the map. Failed orders are missed windows or the horizon, which the simulator fails instead of serving late; they are not violations.

| Scenario | Vehicles | Orders | Delivered | Failed | Disruptions | Road entries checked | Deliveries checked | Violations |
|---|---|---|---|---|---|---|---|---|
| small_clustered_s0 | 10 | 100 | 90 | 10 | 22 | 2870 | 90 | 0 |
| medium_hotspot_s3 | 15 | 200 | 150 | 50 | 23 | 4113 | 150 | 0 |

Disruption rate: 10 per hour. Violations by check:

| Scenario | KR-12 | NODE | KR-11 | KR-7 | KR-8 | KR-9 | KR-1 | KR-4 | KR-5 | KR-3 | KR-6 | PATH |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| small_clustered_s0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| medium_hotspot_s3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
