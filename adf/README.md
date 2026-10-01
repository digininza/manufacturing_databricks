# ADF artifacts (Git-mode layout)

Point ADF Studio → *Manage → Git configuration* at this repo with **root folder `/adf`**.
Full design: [../docs/ADF_FRAMEWORK.md](../docs/ADF_FRAMEWORK.md).

| Folder | Contents |
|---|---|
| `factory/` | Factory definition + global parameters (Key Vault URL, CMMS URLs, Databricks workspace/job) |
| `integrationRuntime/` | `IR-SelfHosted-OnPrem` (2-node HA, on-prem SQL Server + Oracle) |
| `linkedService/` | Key Vault, ADLS Gen2 (managed identity), control DB (managed identity), SQL Server + Oracle (Key Vault passwords), CMMS REST |
| `dataset/` | 8 parameterised datasets shared by every pipeline + 2 fixed datasets for Power Query |
| `dataflow/` | `PQ_Supplier_Delivery_Cleansing` (Power Query / M) · `DF_MES_ProductionLog_CDC_Apply` (mapping data flow: CDC latest-per-key + delete/upsert into Delta) |
| `pipeline/` | `PL_00` master → `PL_01` router → `PL_10..13` source templates · `PL_14` Power Query cleansing · `PL_15` CDC apply data flow · `PL_30` Databricks job · `PL_90` alert |
| `trigger/` | `TR_Daily_0200_UTC` (schedule) · `TR_Hourly_MES_Tumbling` (tumbling window, self-dependent) |

Placeholders to replace before publishing: `*.example.com`, `adb-REPLACE*`, `REPLACE_WITH_JOB_ID`,
server names in `LS_SqlServer_MES` / `LS_Oracle_ERP`. Regenerate with `python tools/generate_adf_artifacts.py`.
