# 08 · Non-functional requirements, SLA and capacity

| Doc ID | NFMFG-DOC-08 | Owner | Data architect | Validated by | Platform / infra team, FinOps | Approver | Business owner + IT service owner |
|---|---|---|---|---|---|---|---|

| ID | Category | Requirement | Design response | Verified by (doc 11) |
|---|---|---|---|---|
| NFR-01 | Freshness | Daily KPIs by 06:00 UTC | 02:00 ADF trigger, 90-minute job health rule | Performance test PT-02 |
| NFR-02 | Freshness | Shop-floor status ≤ 60 min | Hourly MES tumbling window + DirectQuery | SIT-15 |
| NFR-03 | Volume | 3-year growth: 10M production changes/year, 18B sensor readings/year | Liquid clustering, IoT date partitions, salting in backfill (`docs/PERFORMANCE_AND_SCALE.md`) | PT-01 (1-year backfill) |
| NFR-04 | Recoverability | Any failed load recoverable by rerun, with no data loss or duplicates | Frozen windows, checkpoints, MERGE | Failure tests FT-01…08 |
| NFR-05 | Retention | Raw 90 days; bronze 2 years; silver/gold 7 years; time travel 7 days | ADLS lifecycle rules, VACUUM 168 h | Ops review |
| NFR-06 | Availability | Platform availability 99.5% in business hours | Managed services; SHIR on 2 nodes | Monitoring |
| NFR-07 | DR | RPO 24 h, RTO 8 h | ADLS GRS; code in Git; gold rebuildable from bronze with deterministic SKs | DR test (annual) |
| NFR-08 | Security | See doc 09 | | Pen test |
| NFR-09 | Cost | ≤ budgeted monthly run cost | Job clusters (not all-purpose), auto-suspend on Snowflake, resource monitor | FinOps monthly review |
| NFR-10 | Auditability | Every number traceable to a source window | Lineage columns + control tables | Audit walkthrough |

**Capacity sizing (initial):**
- Batch job: autoscaling 1–6 workers (D4ds_v5).
- Streaming: 2 workers, 64 shuffle partitions.
- Snowflake: WH_MFG_LOAD SMALL; WH_MFG_BI MEDIUM with 1–3 clusters.
- Power BI: F64 / P1 capacity, which covers the incremental refresh and model size.

Revisit after UAT volume tests.
