# 03 · Source system interface agreements (ICD / data contracts)

| Doc ID | NFMFG-DOC-03 | Owner | Senior data engineer | **Handed over by** | Each source owner / vendor (see sections) | Approvers | Source owner **and** data architect (co-signed) |
|---|---|---|---|---|---|---|---|

An interface agreement is the **contract** between a source and the platform. Build does not start
until it is signed, and any change to it on the source side must follow the change process (doc 12).
Otherwise a column rename in the MES breaks production at 02:00.

## Common checklist (every source must answer all of these)
| # | Item | Why we need it |
|---|---|---|
| 1 | Business owner, technical contact, escalation contact, on-call hours | Incidents |
| 2 | Objects in scope (tables / endpoints / files) with DDL or schema | STTM source side |
| 3 | Primary / business keys and their uniqueness guarantee | Dedup, MERGE |
| 4 | **Change capture method**: CDC / `last_update` column / full / event | Incremental design |
| 5 | **Delete semantics**: hard, soft (flag or status), or never | Correctness |
| 6 | Volumes today and in 3 years; peak periods | Capacity (doc 08) |
| 7 | Delivery schedule / availability window / maintenance windows | Scheduling |
| 8 | Data latency guarantee and late-data behaviour | SLA |
| 9 | Time zone and timestamp semantics | Production-day logic |
| 10 | Reference / code lists (statuses, defect codes) | DQ domain rules |
| 11 | Authentication method and account provisioning | Security (doc 09) |
| 12 | **Schema change notice period** (e.g. 10 working days) | Stability |
| 13 | Known data-quality issues | DQ rules (doc 10) |
| 14 | Sample data (masked) | Profiling (doc 04) |

## 1. MES — SQL Server 2019 (handed over by: plant IT DBA + MES vendor)
| Item | Agreed value |
|---|---|
| Objects | `dbo.machine`, `dbo.production_log`, `dbo.quality_inspection` (DDL: `sql/source_systems/sqlserver_mes_setup_and_cdc.sql`) |
| Capture | **Native CDC**, capture instances `dbo_<table>`, `'all'` row filter |
| CDC retention | **7 days** (default 3; raised on our request, see RAID R-03) |
| Deletes | Hard deletes captured by CDC (`__$operation = 1`) |
| Access | Login `svc_adf_mes`, role `cdc_reader`, via the self-hosted IR (outbound only) |
| Volumes | ~3k runs/day, ~3k inspections/day, 40 machines |
| Maintenance window | Sunday 01:00–04:00 plant time (no extraction) |
| Schema change notice | 10 working days, with CDC capture instance re-creation coordinated with us |

## 2. ERP — Oracle 19c (handed over by: ERP application team)
| Item | Agreed value |
|---|---|
| Objects | `ERP.PLANTS`, `ERP.PRODUCTS`, `ERP.PRODUCTION_ORDERS` |
| Capture | Watermark on `LAST_UPDATE_DATE` (trigger-maintained, **indexed**); PLANTS is full-loaded |
| Deletes | Orders are **never hard-deleted** (status `CANCELLED`); products are end-dated, not deleted. Hard deletes require a notification. |
| Access | `SVC_ADF_ERP`, SELECT only |
| Clock | DB server time is UTC |

## 3. CMMS maintenance — REST API (handed over by: SaaS vendor)
| Item | Agreed value |
|---|---|
| Spec | OpenAPI 3 document from the vendor portal (linked, not copied) |
| Endpoint | `GET /api/v1/work-orders?updated_since&updated_until&page&page_size` |
| Auth | OAuth2 client credentials; scope `workorders.read`; secret rotation every 90 days |
| Limits | 10 requests/second, page_size ≤ 500; HTTP 429 with `Retry-After` |
| Consistency | Index lag ≤ 5 min, so the extraction uses a 5-minute safety lag |
| Deletes | Work orders are never deleted (status `CANCELLED`) |
| Sandbox | A test tenant with synthetic data for SIT |

## 4. Supplier delivery files (handed over by: procurement, on behalf of the suppliers)
| Item | Agreed value |
|---|---|
| Transport | Supplier SFTP → ADLS `landing/supplier_deliveries/incoming/` |
| Naming | `<SUPPLIER_ID>_deliveries_<YYYYMMDD>.csv` |
| Format | CSV, UTF-8, comma, header row, `"` quote; columns as in `sample_data/source_extracts/supplier_landing/` |
| Schedule | By 23:00 UTC daily; re-sends allowed (dedup on `delivery_id`) |
| Bad rows | Up to 2% are tolerated and logged; above that the file is rejected back to the supplier |

## 5. IoT telemetry (handed over by: OT / IoT integrator)
| Item | Agreed value |
|---|---|
| Transport | Edge gateway → Event Hub `machine-telemetry` (32 partitions, partition key = machine_id) |
| Payload | JSON; schema in `docs/STREAMING.md`; `event_ts` in UTC ISO-8601 |
| Guarantees | At-least-once (duplicates possible); lateness ≤ 10 min under normal operation |
| Throughput | ~600 events/s average, 3,000/s peak |
| Device IDs | `GW-<machine_id>-<SENSOR>`; the registry is maintained by the OT team |
