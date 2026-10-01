"""
Generates the ADF Git-mode JSON in adf/ (factory, IR, linked services, datasets, pipelines, triggers).

Why a generator: ~1,500 lines of hand-written ADF JSON drift easily (a renamed
activity breaks every @activity() reference). The generated files in adf/ are
what ADF Studio imports; once ADF is Git-connected you normally edit in the
ADF UI and this script is only a convenience for bulk changes.

    python tools/generate_adf_artifacts.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "adf"
FACTORY = "adf-nf-mfg-dev"


def E(v):
    return {"value": v, "type": "Expression"}


def write(folder, name, props, extra=None):
    doc = {"name": name, "properties": props}
    if extra:
        doc.update(extra)
    (ROOT / folder).mkdir(parents=True, exist_ok=True)
    (ROOT / folder / f"{name}.json").write_text(json.dumps(doc, indent=4) + "\n")


def ls_ref(name, params=None):
    r = {"referenceName": name, "type": "LinkedServiceReference"}
    if params:
        r["parameters"] = params
    return r


def ds_ref(name, params=None):
    r = {"referenceName": name, "type": "DatasetReference"}
    if params:
        r["parameters"] = params
    return r


def kv_secret(secret):
    return {"type": "AzureKeyVaultSecret", "store": ls_ref("LS_KeyVault"), "secretName": secret}


def dep(name, cond="Succeeded"):
    return {"activity": name, "dependencyConditions": [cond]}


def policy(retry=0, interval=30, timeout="0.12:00:00", secure_in=False, secure_out=False):
    return {"timeout": timeout, "retry": retry, "retryIntervalInSeconds": interval, "secureOutput": secure_out, "secureInput": secure_in}


RETRY = dict(retry=3, interval=120)  # transient network / SHIR / throttling failures


# =============================================================================== factory + IR
write(
    "factory",
    FACTORY,
    {
        "globalParameters": {
            "gp_environment": {"type": "String", "value": "dev"},
            "gp_keyvault_url": {"type": "String", "value": "https://kv-nf-mfg-dev.vault.azure.net"},
            "gp_cmms_base_url": {"type": "String", "value": "https://cmms.example.com/api/v1/"},
            "gp_cmms_token_url": {"type": "String", "value": "https://login.cmms.example.com/oauth2/token"},
            "gp_cmms_client_id": {"type": "String", "value": "adf-nf-mfg-dev"},
            "gp_databricks_workspace_url": {"type": "String", "value": "https://adb-REPLACE.azuredatabricks.net"},
            "gp_databricks_job_id": {"type": "String", "value": "REPLACE_WITH_JOB_ID"},
        }
    },
    extra={"location": "westeurope", "identity": {"type": "SystemAssigned"}},
)

write(
    "integrationRuntime",
    "IR-SelfHosted-OnPrem",
    {
        "type": "SelfHosted",
        "description": "Self-hosted IR on 2 HA nodes in the plant data centre. Reaches on-prem SQL Server (MES) and Oracle (ERP) over the private network; outbound 443 only to Azure (no inbound firewall rules).",
    },
)

# =============================================================================== linked services
write(
    "linkedService",
    "LS_KeyVault",
    {
        "type": "AzureKeyVault",
        "description": "All secrets (DB passwords, API client secret, alert webhook). ADF managed identity has 'Key Vault Secrets User'.",
        "typeProperties": {"baseUrl": "https://kv-nf-mfg-dev.vault.azure.net/"},
    },
)
write(
    "linkedService",
    "LS_ADLS_Gen2",
    {
        "type": "AzureBlobFS",
        "description": "ADLS Gen2 (stnfmfgdev). Auth = ADF system-assigned managed identity with 'Storage Blob Data Contributor'. No account keys.",
        "typeProperties": {"url": "https://stnfmfgdev.dfs.core.windows.net/"},
    },
)
write(
    "linkedService",
    "LS_AzureSql_ControlDB",
    {
        "type": "AzureSqlDatabase",
        "description": "Metadata/control DB. Auth = ADF managed identity (CREATE USER ... FROM EXTERNAL PROVIDER).",
        "typeProperties": {
            "server": "sql-nf-mfg-dev.database.windows.net",
            "database": "sqldb-nf-mfg-control",
            "encrypt": "mandatory",
            "trustServerCertificate": False,
            "authenticationType": "SystemAssignedManagedIdentity",
        },
    },
)
write(
    "linkedService",
    "LS_SqlServer_MES",
    {
        "type": "SqlServer",
        "description": "On-prem MES SQL Server via self-hosted IR. Read-only CDC login; password from Key Vault.",
        "typeProperties": {
            "server": "MES-SQL01.northforge.local",
            "database": "MES_PROD",
            "encrypt": "mandatory",
            "trustServerCertificate": False,
            "authenticationType": "SQL",
            "userName": "svc_adf_mes",
            "password": kv_secret("mes-sqlserver-password"),
        },
        "connectVia": {"referenceName": "IR-SelfHosted-OnPrem", "type": "IntegrationRuntimeReference"},
    },
)
write(
    "linkedService",
    "LS_Oracle_ERP",
    {
        "type": "Oracle",
        "version": "2.0",  # current Oracle connector; the 1.0 connection-string form is deprecated
        "description": "On-prem Oracle ERP via self-hosted IR (Oracle connector v2.0). Read-only user; password from Key Vault.",
        "typeProperties": {
            "server": "erp-ora01.northforge.local:1521/ERPPROD",  # host:port/service_name
            "authenticationType": "Basic",
            "username": "SVC_ADF_ERP",
            "password": kv_secret("erp-oracle-password"),
        },
        "connectVia": {"referenceName": "IR-SelfHosted-OnPrem", "type": "IntegrationRuntimeReference"},
    },
)
write(
    "linkedService",
    "LS_Rest_CMMS",
    {
        "type": "RestService",
        "description": "CMMS (maintenance) REST API. OAuth2 bearer token obtained per run by a Web activity (client secret in Key Vault) and passed as a header.",
        "typeProperties": {"url": "https://cmms.example.com/api/v1/", "enableServerCertificateValidation": True, "authenticationType": "Anonymous"},
    },
)

# =============================================================================== datasets
write("dataset", "DS_ControlDB", {"linkedServiceName": ls_ref("LS_AzureSql_ControlDB"), "type": "AzureSqlTable", "schema": [], "typeProperties": {}})
write("dataset", "DS_SqlServer_MES", {"linkedServiceName": ls_ref("LS_SqlServer_MES"), "type": "SqlServerTable", "schema": [], "typeProperties": {}})
write("dataset", "DS_Oracle_ERP", {"linkedServiceName": ls_ref("LS_Oracle_ERP"), "type": "OracleTable", "schema": [], "typeProperties": {}})
write(
    "dataset",
    "DS_Rest_CMMS",
    {
        "linkedServiceName": ls_ref("LS_Rest_CMMS"),
        "parameters": {"relative_url": {"type": "string"}},
        "type": "RestResource",
        "typeProperties": {"relativeUrl": E("@dataset().relative_url")},
    },
)


def adls_location(file_name=True):
    loc = {"type": "AzureBlobFSLocation", "fileSystem": E("@dataset().container"), "folderPath": E("@dataset().folder")}
    if file_name:
        loc["fileName"] = E("@dataset().file")
    return loc


write(
    "dataset",
    "DS_ADLS_Parquet",
    {
        "linkedServiceName": ls_ref("LS_ADLS_Gen2"),
        "parameters": {"container": {"type": "string"}, "folder": {"type": "string"}, "file": {"type": "string"}},
        "type": "Parquet",
        "typeProperties": {"location": adls_location(), "compressionCodec": "snappy"},
        "schema": [],
    },
)
write(
    "dataset",
    "DS_ADLS_Binary",
    {
        "linkedServiceName": ls_ref("LS_ADLS_Gen2"),
        "parameters": {"container": {"type": "string"}, "folder": {"type": "string"}},
        "type": "Binary",
        "typeProperties": {"location": adls_location(file_name=False)},
    },
)
write(
    "dataset",
    "DS_ADLS_Json",
    {
        "linkedServiceName": ls_ref("LS_ADLS_Gen2"),
        "parameters": {"container": {"type": "string"}, "folder": {"type": "string"}, "file": {"type": "string"}},
        "type": "Json",
        "typeProperties": {"location": adls_location(), "encodingName": "UTF-8"},
        "schema": {},
    },
)
write(
    "dataset",
    "DS_ADLS_Csv",
    {
        "linkedServiceName": ls_ref("LS_ADLS_Gen2"),
        "parameters": {"container": {"type": "string"}, "folder": {"type": "string"}},
        "type": "DelimitedText",
        "typeProperties": {
            "location": adls_location(file_name=False),
            "columnDelimiter": ",",
            "escapeChar": "\\",
            "quoteChar": '"',
            "firstRowAsHeader": True,
            "encodingName": "UTF-8",
        },
        "schema": [],
    },
)

# =============================================================================== reusable activity builders
CHILD_PARAMS = {"entity": {"type": "object"}, "batch_run_id": {"type": "string"}, "run_date": {"type": "string"}}
P = "pipeline().parameters.entity"
RUN = "activity('Start_Run').output.firstRow"


def sp_activity(name, proc, params, depends=None, retry=2):
    return {
        "name": name,
        "type": "SqlServerStoredProcedure",
        "dependsOn": depends or [],
        "policy": policy(retry=retry, interval=30, timeout="0.00:10:00"),
        "userProperties": [],
        "linkedServiceName": ls_ref("LS_AzureSql_ControlDB"),
        "typeProperties": {"storedProcedureName": proc, "storedProcedureParameters": params},
    }


def sp_lookup(name, proc, params, depends=None, first_row=True):
    return {
        "name": name,
        "type": "Lookup",
        "dependsOn": depends or [],
        "policy": policy(retry=2, interval=30, timeout="0.00:10:00"),
        "userProperties": [],
        "typeProperties": {
            "source": {"type": "AzureSqlSource", "sqlReaderStoredProcedureName": proc, "storedProcedureParameters": params, "queryTimeout": "00:10:00"},
            "dataset": ds_ref("DS_ControlDB"),
            "firstRowOnly": first_row,
        },
    }


def start_run(candidate_expr, depends=None):
    return sp_lookup(
        "Start_Run",
        "[ctl].[usp_start_ingestion_run]",
        {
            "entity_id": {"type": "Int32", "value": E(f"@{P}.entity_id")},
            "batch_run_id": {"type": "Int64", "value": E("@pipeline().parameters.batch_run_id")},
            "adf_pipeline_run_id": {"type": "String", "value": E("@pipeline().RunId")},
            "load_date": {"type": "DateTime", "value": E("@pipeline().parameters.run_date")},
            "candidate_window_end": {"type": "String", "value": E(candidate_expr)},
        },
        depends,
    )


def validate_counts(copy_name, source_count_expr, depends):
    return sp_activity(
        "Validate_Counts",
        "[ctl].[usp_validate_ingestion_counts]",
        {
            "run_id": {"type": "String", "value": E(f"@{RUN}.run_id")},
            "source_count": {"type": "Int64", "value": E(source_count_expr)},
            "rows_read": {"type": "Int64", "value": E(f"@activity('{copy_name}').output.rowsRead")},
            "rows_copied": {"type": "Int64", "value": E(f"@activity('{copy_name}').output.rowsCopied")},
            "rows_skipped": {"type": "Int64", "value": E(f"@coalesce(activity('{copy_name}').output.rowsSkipped, 0)")},
        },
        depends,
        retry=0,  # a count mismatch is not transient: never retry, fail and alert
    )


def publish_steps(after):
    """write-audit-publish: _staging -> raw (atomic from the consumer's view), manifest, then watermark."""
    return [
        {
            "name": "Publish_Staging_To_Raw",
            "description": "Move validated files from _staging to the path Auto Loader watches. Same run_id => same path => a retry overwrites instead of duplicating.",
            "type": "Copy",
            "dependsOn": [dep(after)],
            "policy": policy(**RETRY, timeout="0.02:00:00"),
            "userProperties": [],
            "typeProperties": {
                "source": {
                    "type": "BinarySource",
                    "storeSettings": {"type": "AzureBlobFSReadSettings", "recursive": True, "deleteFilesAfterCompletion": True},
                    "formatSettings": {"type": "BinaryReadSettings"},
                },
                "sink": {"type": "BinarySink", "storeSettings": {"type": "AzureBlobFSWriteSettings", "copyBehavior": "PreserveHierarchy"}},
                "enableStaging": False,
            },
            "inputs": [ds_ref("DS_ADLS_Binary", {"container": E(f"@{P}.target_container"), "folder": E(f"@concat('_staging/', {RUN}.target_path)")})],
            "outputs": [ds_ref("DS_ADLS_Binary", {"container": E(f"@{P}.target_container"), "folder": E(f"@{RUN}.target_path")})],
        },
        {
            "name": "Write_Manifest",
            "description": "Row-count manifest consumed by Databricks ADF_TO_BRONZE reconciliation.",
            "type": "Copy",
            "dependsOn": [dep("Publish_Staging_To_Raw")],
            "policy": policy(**RETRY, timeout="0.00:10:00"),
            "userProperties": [],
            "typeProperties": {
                "source": {
                    "type": "AzureSqlSource",
                    "sqlReaderQuery": E(f"@concat('SELECT * FROM ctl.vw_run_manifest WHERE run_id = ''', {RUN}.run_id, '''')"),
                },
                "sink": {
                    "type": "JsonSink",
                    "storeSettings": {"type": "AzureBlobFSWriteSettings"},
                    "formatSettings": {"type": "JsonWriteSettings", "filePattern": "setOfObjects"},
                },
                "enableStaging": False,
            },
            "inputs": [ds_ref("DS_ControlDB")],
            "outputs": [
                ds_ref(
                    "DS_ADLS_Json",
                    {
                        "container": E(f"@{P}.target_container"),
                        "folder": E(f"@concat('_manifests/', {P}.entity_name)"),
                        "file": E(f"@concat({RUN}.run_id, '.json')"),
                    },
                )
            ],
        },
        sp_activity(
            "Complete_Run_Advance_Watermark",
            "[ctl].[usp_complete_ingestion_run]",
            {"run_id": {"type": "String", "value": E(f"@{RUN}.run_id")}, "no_changes": {"type": "Boolean", "value": "false"}},
            [dep("Write_Manifest")],
        ),
    ]


def no_change_branch():
    return [
        sp_activity(
            "Complete_Run_No_Changes",
            "[ctl].[usp_complete_ingestion_run]",
            {"run_id": {"type": "String", "value": E(f"@{RUN}.run_id")}, "no_changes": {"type": "Boolean", "value": "true"}},
        )
    ]


def catch_block(guarded):
    """Try/catch: log -> alert -> Fail (so the pipeline is still reported FAILED to the parent and monitors)."""
    return [
        sp_activity(
            "Log_Run_Failure",
            "[ctl].[usp_fail_ingestion_run]",
            {
                "run_id": {"type": "String", "value": E(f"@{RUN}.run_id")},
                "error_message": {"type": "String", "value": E(f"@activity('{guarded}').error.message")},
            },
            [dep(guarded, "Failed")],
        ),
        alert_call("Send_Failure_Alert", E(f"@{P}.alert_severity"), E(f"@activity('{guarded}').error.message"), [dep("Log_Run_Failure", "Completed")]),
        {
            "name": "Fail_Pipeline",
            "type": "Fail",
            "dependsOn": [dep("Send_Failure_Alert", "Completed")],
            "userProperties": [],
            "typeProperties": {
                "message": E(f"@concat('Ingestion failed for ', {P}.entity_name, ' run ', {RUN}.run_id, ': ', activity('{guarded}').error.message)"),
                "errorCode": "INGESTION_FAILED",
            },
        },
    ]


def alert_call(name, severity, message, depends):
    return {
        "name": name,
        "type": "ExecutePipeline",
        "dependsOn": depends,
        "userProperties": [],
        "typeProperties": {
            "pipeline": {"referenceName": "PL_90_Send_Alert", "type": "PipelineReference"},
            "waitOnCompletion": True,
            "parameters": {
                "severity": severity,
                "pipeline_name": E("@pipeline().Pipeline"),
                "entity_name": E(f"@{P}.entity_name"),
                "message": message,
                "adf_run_id": E("@pipeline().RunId"),
            },
        },
    }


def if_changes(true_acts):
    return {
        "name": "If_Has_Changes",
        "type": "IfCondition",
        "dependsOn": [dep("Start_Run")],
        "userProperties": [],
        "typeProperties": {"expression": E(f"@bool({RUN}.has_changes)"), "ifTrueActivities": true_acts, "ifFalseActivities": no_change_branch()},
    }


def staging_parquet_sink():
    return ds_ref(
        "DS_ADLS_Parquet",
        {"container": E(f"@{P}.target_container"), "folder": E(f"@concat('_staging/', {RUN}.target_path)"), "file": E(f"@concat({RUN}.run_id, '.parquet')")},
    )


PARQUET_SINK = {"type": "ParquetSink", "storeSettings": {"type": "AzureBlobFSWriteSettings"}, "formatSettings": {"type": "ParquetWriteSettings"}}


# =============================================================================== PL_10 SQL Server CDC
def db_lookup(name, dataset, source_type, query_key, query_expr, depends=None):
    return {
        "name": name,
        "type": "Lookup",
        "dependsOn": depends or [],
        "policy": policy(**RETRY, timeout="0.00:30:00"),
        "userProperties": [],
        "typeProperties": {
            "source": {"type": source_type, query_key: E(query_expr), "queryTimeout": "00:30:00"},
            "dataset": ds_ref(dataset),
            "firstRowOnly": True,
        },
    }


def db_pipeline(name, desc, dataset, source_type, query_key, extra_source=None, folder="10_Ingestion"):
    copy_source = {"type": source_type, query_key: E(f"@{RUN}.source_query"), "queryTimeout": "02:00:00"}
    copy_source.update(extra_source or {})
    true_acts = [
        db_lookup("Get_Source_Count", dataset, source_type, query_key, f"@{RUN}.count_query"),
        {
            "name": "Copy_To_Staging",
            "type": "Copy",
            "dependsOn": [dep("Get_Source_Count")],
            "policy": policy(**RETRY, timeout="0.04:00:00"),
            "userProperties": [{"name": "entity", "value": E(f"@{P}.entity_name")}, {"name": "run_id", "value": E(f"@{RUN}.run_id")}],
            "typeProperties": {"source": copy_source, "sink": PARQUET_SINK, "enableStaging": False, "parallelCopies": 4},
            "inputs": [ds_ref(dataset)],
            "outputs": [staging_parquet_sink()],
        },
        validate_counts("Copy_To_Staging", "@activity('Get_Source_Count').output.firstRow.SOURCE_COUNT", [dep("Copy_To_Staging")]),
    ] + publish_steps("Validate_Counts")

    hwm_expr = f"@if(empty({P}.hwm_query), 'SELECT NULL AS HWM{' FROM DUAL' if 'Oracle' in source_type else ''}', {P}.hwm_query)"
    acts = [
        db_lookup("Get_High_Watermark", dataset, source_type, query_key, hwm_expr),
        start_run("@coalesce(activity('Get_High_Watermark').output.firstRow?.HWM, '')", [dep("Get_High_Watermark")]),
        if_changes(true_acts),
    ] + catch_block("If_Has_Changes")
    write("pipeline", name, {"description": desc, "activities": acts, "parameters": CHILD_PARAMS, "folder": {"name": folder}, "annotations": ["ingestion"]})


db_pipeline(
    "PL_10_Ingest_SqlServer_CDC",
    "SQL Server MES native CDC. Window (last LSN, max LSN] frozen by usp_start_ingestion_run. Reads cdc.fn_cdc_get_all_changes_<ci>; throws on LSN gap. Count -> Copy -> validate -> publish -> manifest -> advance watermark.",
    "DS_SqlServer_MES",
    "SqlServerSource",
    "sqlReaderQuery",
    {"isolationLevel": "ReadCommitted", "partitionOption": "None"},
)
db_pipeline(
    "PL_11_Ingest_Oracle",
    "Oracle ERP. ORACLE_WATERMARK: LAST_UPDATE_DATE window frozen at start. ORACLE_FULL: whole table (has_changes always 1). Same count/validate/publish/watermark contract as every source.",
    "DS_Oracle_ERP",
    "OracleSource",
    "oracleReaderQuery",
    {"partitionOption": "None"},
)

# =============================================================================== PL_12 REST API
kv_get = lambda name, secret: {  # noqa: E731
    "name": name,
    "type": "WebActivity",
    "dependsOn": [],
    "policy": policy(**RETRY, timeout="0.00:05:00", secure_out=True),
    "userProperties": [],
    "typeProperties": {
        "method": "GET",
        "url": E(f"@concat(pipeline().globalParameters.gp_keyvault_url, '/secrets/{secret}?api-version=7.4')"),
        "authentication": {"type": "MSI", "resource": "https://vault.azure.net"},
    },
}
AUTH_HEADER = "@concat('Bearer ', activity('Get_Access_Token').output.access_token)"
api_fields = [
    "work_order_id",
    "machine_id",
    "work_type",
    "priority",
    "status",
    "reported_at",
    "started_at",
    "completed_at",
    "downtime_minutes",
    "technician",
    "updated_at",
]
rest_true = [
    {
        "name": "Get_Source_Count",
        "description": "Same filter with page_size=1: API returns meta.total_count -> independent source count.",
        "type": "WebActivity",
        "dependsOn": [],
        "policy": policy(**RETRY, timeout="0.00:05:00", secure_in=True),
        "userProperties": [],
        "typeProperties": {
            "method": "GET",
            "url": E(f"@concat(pipeline().globalParameters.gp_cmms_base_url, {RUN}.count_query)"),
            "headers": {"Authorization": E(AUTH_HEADER), "Accept": "application/json"},
        },
    },
    {
        "name": "Copy_To_Staging",
        "type": "Copy",
        "dependsOn": [dep("Get_Source_Count")],
        "policy": policy(**RETRY, timeout="0.02:00:00", secure_in=True),
        "userProperties": [{"name": "entity", "value": E(f"@{P}.entity_name")}],
        "typeProperties": {
            "source": {
                "type": "RestSource",
                "httpRequestTimeout": "00:02:00",
                "requestInterval": "00.00:00:00.200",
                "requestMethod": "GET",
                "additionalHeaders": {"Authorization": E(AUTH_HEADER)},
                "paginationRules": {"QueryParameters.page": "RANGE:1::1", "EndCondition:$.data": "Empty", "MaxRequestNumber": "5000"},
            },
            "sink": PARQUET_SINK,
            "enableStaging": False,
            "translator": {
                "type": "TabularTranslator",
                "collectionReference": "$['data']",
                "mappings": [{"source": {"path": f"['{f}']"}, "sink": {"name": f}} for f in api_fields],
            },
        },
        "inputs": [ds_ref("DS_Rest_CMMS", {"relative_url": E(f"@{RUN}.source_query")})],
        "outputs": [staging_parquet_sink()],
    },
    validate_counts("Copy_To_Staging", "@activity('Get_Source_Count').output.meta.total_count", [dep("Copy_To_Staging")]),
] + publish_steps("Validate_Counts")

rest_acts = [
    kv_get("Get_Client_Secret", "cmms-client-secret"),
    {
        "name": "Get_Access_Token",
        "description": "OAuth2 client-credentials. secureInput/secureOutput keep the secret and token out of ADF run history.",
        "type": "WebActivity",
        "dependsOn": [dep("Get_Client_Secret")],
        "policy": policy(**RETRY, timeout="0.00:05:00", secure_in=True, secure_out=True),
        "userProperties": [],
        "typeProperties": {
            "method": "POST",
            "url": E("@pipeline().globalParameters.gp_cmms_token_url"),
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "body": E(
                "@concat('grant_type=client_credentials&scope=workorders.read&client_id=', pipeline().globalParameters.gp_cmms_client_id, '&client_secret=', activity('Get_Client_Secret').output.value)"
            ),
        },
    },
    start_run(
        f"@formatDateTime(addMinutes(pipeline().TriggerTime, mul(-1, int({P}.api_safety_lag_minutes))), 'yyyy-MM-ddTHH:mm:ssZ')", [dep("Get_Access_Token")]
    ),
    if_changes(rest_true),
] + catch_block("If_Has_Changes")
write(
    "pipeline",
    "PL_12_Ingest_RestApi",
    {
        "description": "CMMS REST API: OAuth2 token -> updated_since/updated_until window (5-min safety lag) -> paginated Copy (page=1..n until data is empty) -> flatten $.data -> parquet. Count from meta.total_count.",
        "activities": rest_acts,
        "parameters": CHILD_PARAMS,
        "folder": {"name": "10_Ingestion"},
        "annotations": ["ingestion"],
    },
)

# =============================================================================== PL_13 ADLS files
file_true = [
    {
        "name": "List_Landing_Files",
        "type": "GetMetadata",
        "dependsOn": [],
        "policy": policy(**RETRY, timeout="0.00:10:00"),
        "userProperties": [],
        "typeProperties": {
            "dataset": ds_ref("DS_ADLS_Binary", {"container": "landing", "folder": E(f"@{P}.landing_folder")}),
            "fieldList": ["childItems", "itemCount"],
            "storeSettings": {
                "type": "AzureBlobFSReadSettings",
                "recursive": False,
                "modifiedDatetimeStart": E(f"@{RUN}.window_start"),
                "modifiedDatetimeEnd": E(f"@{RUN}.window_end"),
            },
            "formatSettings": {"type": "BinaryReadSettings"},
        },
    },
    sp_activity(
        "Audit_File_List",
        "[ctl].[usp_log_audit]",
        {
            "run_id": {"type": "String", "value": E(f"@{RUN}.run_id")},
            "entity_name": {"type": "String", "value": E(f"@{P}.entity_name")},
            "event_type": {"type": "String", "value": "FILES_DETECTED"},
            "event_detail": {"type": "String", "value": E("@string(activity('List_Landing_Files').output.childItems)")},
        },
        [dep("List_Landing_Files")],
    ),
    {
        "name": "Copy_To_Staging",
        "description": "CSV -> parquet. Fault tolerance: malformed rows are SKIPPED and logged to raw/_rejected/, then Validate_Counts enforces max_reject_pct.",
        "type": "Copy",
        "dependsOn": [dep("Audit_File_List")],
        "policy": policy(**RETRY, timeout="0.02:00:00"),
        "userProperties": [{"name": "entity", "value": E(f"@{P}.entity_name")}],
        "typeProperties": {
            "source": {
                "type": "DelimitedTextSource",
                "storeSettings": {
                    "type": "AzureBlobFSReadSettings",
                    "recursive": False,
                    "wildcardFolderPath": E(f"@{P}.landing_folder"),
                    "wildcardFileName": E(f"@{P}.file_pattern"),
                    "modifiedDatetimeStart": E(f"@{RUN}.window_start"),
                    "modifiedDatetimeEnd": E(f"@{RUN}.window_end"),
                    "enablePartitionDiscovery": False,
                },
                "formatSettings": {"type": "DelimitedTextReadSettings", "skipLineCount": 0},
                "additionalColumns": [{"name": "_landing_file_name", "value": "$$FILEPATH"}],
            },
            "sink": PARQUET_SINK,
            "enableStaging": False,
            "enableSkipIncompatibleRow": True,
            "logSettings": {
                "enableCopyActivityLog": True,
                "copyActivityLogSettings": {"logLevel": "Warning", "enableReliableLogging": True},
                "logLocationSettings": {
                    "linkedServiceName": ls_ref("LS_ADLS_Gen2"),
                    "path": E(f"@concat('raw/_rejected/', {P}.entity_name, '/', {RUN}.run_id)"),
                },
            },
        },
        "inputs": [ds_ref("DS_ADLS_Csv", {"container": "landing", "folder": E(f"@{P}.landing_folder")})],
        "outputs": [staging_parquet_sink()],
    },
    validate_counts("Copy_To_Staging", "@int(-1)", [dep("Copy_To_Staging")]),
] + publish_steps("Validate_Counts")
file_true.append(
    {
        "name": "Archive_Landing_Files",
        "description": "Move processed files landing/<folder>/incoming -> archive (copy + delete source). Runs AFTER the watermark moved: a failure here never causes re-ingestion.",
        "type": "Copy",
        "dependsOn": [dep("Complete_Run_Advance_Watermark")],
        "policy": policy(**RETRY, timeout="0.01:00:00"),
        "userProperties": [],
        "typeProperties": {
            "source": {
                "type": "BinarySource",
                "storeSettings": {
                    "type": "AzureBlobFSReadSettings",
                    "recursive": False,
                    "wildcardFileName": E(f"@{P}.file_pattern"),
                    "modifiedDatetimeStart": E(f"@{RUN}.window_start"),
                    "modifiedDatetimeEnd": E(f"@{RUN}.window_end"),
                    "deleteFilesAfterCompletion": True,
                },
                "formatSettings": {"type": "BinaryReadSettings"},
            },
            "sink": {"type": "BinarySink", "storeSettings": {"type": "AzureBlobFSWriteSettings"}},
            "enableStaging": False,
        },
        "inputs": [ds_ref("DS_ADLS_Binary", {"container": "landing", "folder": E(f"@{P}.landing_folder")})],
        "outputs": [
            ds_ref(
                "DS_ADLS_Binary",
                {"container": "landing", "folder": E(f"@concat(replace({P}.landing_folder, '/incoming', '/archive'), '/', pipeline().parameters.run_date)")},
            )
        ],
    }
)
file_acts = [start_run("@utcNow('yyyy-MM-ddTHH:mm:ssZ')"), if_changes(file_true)] + catch_block("If_Has_Changes")
write(
    "pipeline",
    "PL_13_Ingest_ADLS_Files",
    {
        "description": "Supplier CSV files on ADLS landing: LastModified window -> list+audit files -> CSV->parquet with skip-and-log fault tolerance -> validate (reject %) -> publish -> manifest -> watermark -> archive.",
        "activities": file_acts,
        "parameters": CHILD_PARAMS,
        "folder": {"name": "10_Ingestion"},
        "annotations": ["ingestion"],
    },
)


# =============================================================================== PL_01 router
def exec_child(name, pipeline):
    return {
        "name": name,
        "type": "ExecutePipeline",
        "dependsOn": [],
        "userProperties": [],
        "typeProperties": {
            "pipeline": {"referenceName": pipeline, "type": "PipelineReference"},
            "waitOnCompletion": True,
            "parameters": {
                "entity": E("@pipeline().parameters.entity"),
                "batch_run_id": E("@pipeline().parameters.batch_run_id"),
                "run_date": E("@pipeline().parameters.run_date"),
            },
        },
    }


write(
    "pipeline",
    "PL_01_Ingest_Entity_Router",
    {
        "description": "Routes one control-table row to the child pipeline for its source_type. New source TYPE = new case; new source TABLE = control-table row only.",
        "activities": [
            {
                "name": "Route_By_Source_Type",
                "type": "Switch",
                "dependsOn": [],
                "userProperties": [],
                "typeProperties": {
                    "on": E("@pipeline().parameters.entity.source_type"),
                    "cases": [
                        {"value": "SQLSERVER_CDC", "activities": [exec_child("Run_SqlServer_CDC", "PL_10_Ingest_SqlServer_CDC")]},
                        {"value": "ORACLE_WATERMARK", "activities": [exec_child("Run_Oracle_Incremental", "PL_11_Ingest_Oracle")]},
                        {"value": "ORACLE_FULL", "activities": [exec_child("Run_Oracle_Full", "PL_11_Ingest_Oracle")]},
                        {"value": "REST_API", "activities": [exec_child("Run_Rest_Api", "PL_12_Ingest_RestApi")]},
                        {"value": "ADLS_FILE", "activities": [exec_child("Run_ADLS_Files", "PL_13_Ingest_ADLS_Files")]},
                    ],
                    "defaultActivities": [
                        {
                            "name": "Unknown_Source_Type",
                            "type": "Fail",
                            "dependsOn": [],
                            "userProperties": [],
                            "typeProperties": {
                                "message": E("@concat('Unknown source_type in control table: ', pipeline().parameters.entity.source_type)"),
                                "errorCode": "BAD_METADATA",
                            },
                        }
                    ],
                },
            }
        ],
        "parameters": CHILD_PARAMS,
        "folder": {"name": "00_Orchestration"},
        "annotations": ["orchestration"],
    },
)

# =============================================================================== PL_00 master
master_acts = [
    {
        "name": "Set_Run_Date",
        "type": "SetVariable",
        "dependsOn": [],
        "userProperties": [],
        "typeProperties": {
            "variableName": "v_run_date",
            "value": E("@if(empty(pipeline().parameters.run_date), formatDateTime(pipeline().TriggerTime, 'yyyy-MM-dd'), pipeline().parameters.run_date)"),
        },
    },
    sp_lookup(
        "Start_Batch",
        "[ctl].[usp_start_batch]",
        {
            "batch_group": {"type": "String", "value": E("@pipeline().parameters.batch_group")},
            "run_date": {"type": "DateTime", "value": E("@variables('v_run_date')")},
            "adf_pipeline_run_id": {"type": "String", "value": E("@pipeline().RunId")},
        },
        [dep("Set_Run_Date")],
    ),
    sp_lookup(
        "Get_Pending_Entities",
        "[ctl].[usp_get_pending_entities]",
        {
            "batch_group": {"type": "String", "value": E("@pipeline().parameters.batch_group")},
            "run_date": {"type": "DateTime", "value": E("@variables('v_run_date')")},
            "force_rerun": {"type": "Boolean", "value": E("@pipeline().parameters.force_rerun")},
        },
        [dep("Start_Batch")],
        first_row=False,
    ),
    {
        "name": "ForEach_Entity",
        "description": "Parallel (4 at a time). One entity failing does NOT stop the others; ForEach reports Failed at the end if any failed.",
        "type": "ForEach",
        "dependsOn": [dep("Get_Pending_Entities")],
        "userProperties": [],
        "typeProperties": {
            "items": E("@activity('Get_Pending_Entities').output.value"),
            "isSequential": False,
            "batchCount": 4,
            "activities": [
                {
                    "name": "Ingest_Entity",
                    "type": "ExecutePipeline",
                    "dependsOn": [],
                    "userProperties": [],
                    "typeProperties": {
                        "pipeline": {"referenceName": "PL_01_Ingest_Entity_Router", "type": "PipelineReference"},
                        "waitOnCompletion": True,
                        "parameters": {
                            "entity": E("@item()"),
                            "batch_run_id": E("@string(activity('Start_Batch').output.firstRow.batch_run_id)"),
                            "run_date": E("@variables('v_run_date')"),
                        },
                    },
                }
            ],
        },
    },
    sp_lookup(
        "Complete_Batch",
        "[ctl].[usp_complete_batch]",
        {"batch_run_id": {"type": "Int64", "value": E("@activity('Start_Batch').output.firstRow.batch_run_id")}},
        [dep("ForEach_Entity", "Completed")],
    ),
    {
        "name": "If_Trigger_Databricks",
        "description": "Trigger Databricks even on PARTIAL failure: healthy entities flow on; failed ones are simply absent (no manifest) and are picked up after the rerun.",
        "type": "IfCondition",
        "dependsOn": [dep("Complete_Batch")],
        "userProperties": [],
        "typeProperties": {
            "expression": E("@and(pipeline().parameters.trigger_databricks, greater(int(activity('Complete_Batch').output.firstRow.entities_succeeded), 0))"),
            "ifTrueActivities": [
                {
                    "name": "Run_Databricks_Job",
                    "type": "ExecutePipeline",
                    "dependsOn": [],
                    "userProperties": [],
                    "typeProperties": {
                        "pipeline": {"referenceName": "PL_30_Run_Databricks_Job", "type": "PipelineReference"},
                        "waitOnCompletion": True,
                        "parameters": {"batch_group": E("@pipeline().parameters.batch_group"), "run_date": E("@variables('v_run_date')")},
                    },
                }
            ],
        },
    },
    {
        "name": "Batch_Failure_Alert",
        "type": "ExecutePipeline",
        "dependsOn": [dep("Complete_Batch")],
        "userProperties": [],
        "typeProperties": {
            "pipeline": {"referenceName": "PL_90_Send_Alert", "type": "PipelineReference"},
            "waitOnCompletion": True,
            "parameters": {
                "severity": "HIGH",
                "pipeline_name": E("@pipeline().Pipeline"),
                "entity_name": "BATCH",
                "message": E(
                    "@concat('Batch ', pipeline().parameters.batch_group, ' ', variables('v_run_date'), ' finished with status ', activity('Complete_Batch').output.firstRow.status, ': ', string(activity('Complete_Batch').output.firstRow.entities_failed), ' entity(ies) failed. Rerun PL_00 - completed entities are skipped automatically.')"
                ),
                "adf_run_id": E("@pipeline().RunId"),
            },
        },
    },
]
# Batch_Failure_Alert must run only when something failed: wrap it in an If
master_acts[-1] = {
    "name": "If_Batch_Had_Failures",
    "type": "IfCondition",
    "dependsOn": [dep("Complete_Batch")],
    "userProperties": [],
    "typeProperties": {
        "expression": E("@not(equals(activity('Complete_Batch').output.firstRow.status, 'SUCCESS'))"),
        "ifTrueActivities": [
            dict(master_acts[-1], dependsOn=[]),
            {
                "name": "Fail_Master",
                "type": "Fail",
                "dependsOn": [dep("Batch_Failure_Alert", "Completed")],
                "userProperties": [],
                "typeProperties": {
                    "message": E("@concat('Batch finished with status ', activity('Complete_Batch').output.firstRow.status)"),
                    "errorCode": "BATCH_PARTIAL_FAILURE",
                },
            },
        ],
    },
}
write(
    "pipeline",
    "PL_00_Master_Orchestrator",
    {
        "description": "Metadata-driven master. Reads ctl.ingestion_control for the batch group, skips entities already successful for run_date (rerun-from-failure), fans out in parallel, closes the batch, triggers Databricks, alerts on any failure.",
        "activities": master_acts,
        "parameters": {
            "batch_group": {"type": "string", "defaultValue": "DAILY"},
            "run_date": {"type": "string", "defaultValue": ""},
            "force_rerun": {"type": "bool", "defaultValue": False},
            "trigger_databricks": {"type": "bool", "defaultValue": True},
        },
        "variables": {"v_run_date": {"type": "String"}},
        "folder": {"name": "00_Orchestration"},
        "annotations": ["orchestration"],
    },
)

# =============================================================================== PL_30 Databricks job
DBX_RESOURCE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"  # well-known Azure Databricks Entra ID application
until_acts = [
    {"name": "Wait_60s", "type": "Wait", "dependsOn": [], "userProperties": [], "typeProperties": {"waitTimeInSeconds": 60}},
    {
        "name": "Get_Run_State",
        "type": "WebActivity",
        "dependsOn": [dep("Wait_60s")],
        "policy": policy(**RETRY, timeout="0.00:05:00"),
        "userProperties": [],
        "typeProperties": {
            "method": "GET",
            "url": E("@concat(pipeline().globalParameters.gp_databricks_workspace_url, '/api/2.1/jobs/runs/get?run_id=', variables('v_dbx_run_id'))"),
            "authentication": {"type": "MSI", "resource": DBX_RESOURCE},
        },
    },
    {
        "name": "Set_Life_Cycle",
        "type": "SetVariable",
        "dependsOn": [dep("Get_Run_State")],
        "userProperties": [],
        "typeProperties": {"variableName": "v_life_cycle", "value": E("@activity('Get_Run_State').output.state.life_cycle_state")},
    },
    {
        "name": "Set_Result_State",
        "type": "SetVariable",
        "dependsOn": [dep("Set_Life_Cycle")],
        "userProperties": [],
        "typeProperties": {"variableName": "v_result_state", "value": E("@coalesce(activity('Get_Run_State').output.state?.result_state, '')")},
    },
]
write(
    "pipeline",
    "PL_30_Run_Databricks_Job",
    {
        "description": "Starts the Databricks batch job (Jobs API 2.1, ADF managed identity added to the workspace as a service principal) and polls until it terminates. Alerts + fails if the job did not succeed.",
        "activities": [
            {
                "name": "Run_Now",
                "type": "WebActivity",
                "dependsOn": [],
                "policy": policy(**RETRY, timeout="0.00:05:00"),
                "userProperties": [],
                "typeProperties": {
                    "method": "POST",
                    "url": E("@concat(pipeline().globalParameters.gp_databricks_workspace_url, '/api/2.1/jobs/run-now')"),
                    "authentication": {"type": "MSI", "resource": DBX_RESOURCE},
                    "body": E(
                        '@json(concat(\'{"job_id": \', pipeline().globalParameters.gp_databricks_job_id, \', "job_parameters": {"run_date": "\', pipeline().parameters.run_date, \'", "batch_group": "\', pipeline().parameters.batch_group, \'", "adf_run_id": "\', pipeline().RunId, \'"}}\'))'
                    ),
                },
            },
            {
                "name": "Set_Run_Id",
                "type": "SetVariable",
                "dependsOn": [dep("Run_Now")],
                "userProperties": [],
                "typeProperties": {"variableName": "v_dbx_run_id", "value": E("@string(activity('Run_Now').output.run_id)")},
            },
            {
                "name": "Until_Job_Terminated",
                "type": "Until",
                "dependsOn": [dep("Set_Run_Id")],
                "userProperties": [],
                "typeProperties": {
                    "expression": E("@contains(createArray('TERMINATED', 'SKIPPED', 'INTERNAL_ERROR'), variables('v_life_cycle'))"),
                    "activities": until_acts,
                    "timeout": "0.06:00:00",
                },
            },
            {
                "name": "If_Job_Not_Successful",
                "type": "IfCondition",
                "dependsOn": [dep("Until_Job_Terminated", "Completed")],
                "userProperties": [],
                "typeProperties": {
                    "expression": E("@not(equals(variables('v_result_state'), 'SUCCESS'))"),
                    "ifTrueActivities": [
                        {
                            "name": "Alert_Databricks_Failed",
                            "type": "ExecutePipeline",
                            "dependsOn": [],
                            "userProperties": [],
                            "typeProperties": {
                                "pipeline": {"referenceName": "PL_90_Send_Alert", "type": "PipelineReference"},
                                "waitOnCompletion": True,
                                "parameters": {
                                    "severity": "CRITICAL",
                                    "pipeline_name": E("@pipeline().Pipeline"),
                                    "entity_name": "DATABRICKS_JOB",
                                    "message": E(
                                        "@concat('Databricks run ', variables('v_dbx_run_id'), ' ended ', variables('v_life_cycle'), '/', variables('v_result_state'))"
                                    ),
                                    "adf_run_id": E("@pipeline().RunId"),
                                },
                            },
                        },
                        {
                            "name": "Fail_Databricks",
                            "type": "Fail",
                            "dependsOn": [dep("Alert_Databricks_Failed", "Completed")],
                            "userProperties": [],
                            "typeProperties": {
                                "message": E("@concat('Databricks job result: ', variables('v_result_state'))"),
                                "errorCode": "DATABRICKS_JOB_FAILED",
                            },
                        },
                    ],
                },
            },
        ],
        "parameters": {"batch_group": {"type": "string"}, "run_date": {"type": "string"}},
        "variables": {"v_dbx_run_id": {"type": "String"}, "v_life_cycle": {"type": "String", "defaultValue": "PENDING"}, "v_result_state": {"type": "String"}},
        "folder": {"name": "20_Processing"},
        "annotations": ["databricks"],
    },
)

# =============================================================================== PL_90 alert
write(
    "pipeline",
    "PL_90_Send_Alert",
    {
        "description": "Single alert path for all pipelines: Logic App (URL with SAS in Key Vault) -> Email to on-call DL + Teams channel. Databricks uses the SAME Logic App.",
        "activities": [
            kv_get("Get_Webhook_Url", "logicapp-alert-webhook-url"),
            {
                "name": "Post_Alert",
                "type": "WebActivity",
                "dependsOn": [dep("Get_Webhook_Url")],
                "policy": policy(retry=3, interval=30, timeout="0.00:05:00", secure_in=True),
                "userProperties": [],
                "typeProperties": {
                    "method": "POST",
                    "url": E("@activity('Get_Webhook_Url').output.value"),
                    "headers": {"Content-Type": "application/json"},
                    "body": E(
                        '@json(concat(\'{"platform":"ADF","environment":"\', pipeline().globalParameters.gp_environment, \'","severity":"\', pipeline().parameters.severity, \'","pipeline_name":"\', pipeline().parameters.pipeline_name, \'","entity_name":"\', pipeline().parameters.entity_name, \'","adf_run_id":"\', pipeline().parameters.adf_run_id, \'","data_factory":"\', pipeline().DataFactory, \'","message":"\', replace(replace(pipeline().parameters.message, \'"\', \'\'\'\'), decodeUriComponent(\'%0A\'), \' \'), \'","raised_at_utc":"\', utcNow(), \'"}\'))'
                    ),
                },
            },
        ],
        "parameters": {
            "severity": {"type": "string", "defaultValue": "HIGH"},
            "pipeline_name": {"type": "string"},
            "entity_name": {"type": "string", "defaultValue": ""},
            "message": {"type": "string"},
            "adf_run_id": {"type": "string"},
        },
        "folder": {"name": "90_Utilities"},
        "annotations": ["alerting"],
    },
)

# =============================================================================== triggers
write(
    "trigger",
    "TR_Daily_0200_UTC",
    {
        "description": "Daily batch: masters + ERP + API + files (+ MES tables in the DAILY group).",
        "annotations": [],
        "runtimeState": "Stopped",
        "pipelines": [
            {"pipelineReference": {"referenceName": "PL_00_Master_Orchestrator", "type": "PipelineReference"}, "parameters": {"batch_group": "DAILY"}}
        ],
        "type": "ScheduleTrigger",
        "typeProperties": {
            "recurrence": {
                "frequency": "Day",
                "interval": 1,
                "startTime": "2026-09-01T02:00:00Z",
                "timeZone": "UTC",
                "schedule": {"minutes": [0], "hours": [2]},
            }
        },
    },
)
write(
    "trigger",
    "TR_Hourly_MES_Tumbling",
    {
        "description": "Hourly MES CDC. Tumbling window: each window runs exactly once, retried automatically, and the self-dependency guarantees window N never starts before window N-1 succeeded (ordered CDC).",
        "annotations": [],
        "runtimeState": "Stopped",
        "pipeline": {
            "pipelineReference": {"referenceName": "PL_00_Master_Orchestrator", "type": "PipelineReference"},
            "parameters": {"batch_group": "HOURLY", "run_date": "@formatDateTime(trigger().outputs.windowStartTime, 'yyyy-MM-dd')"},
        },
        "type": "TumblingWindowTrigger",
        "typeProperties": {
            "frequency": "Hour",
            "interval": 1,
            "startTime": "2026-09-01T00:00:00Z",
            "delay": "00:05:00",
            "maxConcurrency": 1,
            "retryPolicy": {"count": 2, "intervalInSeconds": 300},
            "dependsOn": [{"type": "SelfDependencyTumblingWindowTriggerReference", "offset": "-01:00:00", "size": "01:00:00"}],
        },
    },
)

# =============================================================================== ADF-NATIVE TRANSFORMS
# Two showcase pipelines that use ADF's own transformation engines (Spark clusters managed by ADF,
# billed per vCore-hour ONLY while they run). Both read cloud storage: Power Query and data flows
# cannot reach on-prem SQL Server/Oracle through the self-hosted IR, so extraction stays in PL_10/PL_11.

ADLS_URL = "https://stnfmfgdev.dfs.core.windows.net"


def standalone_alert(name, severity, message_expr, depends):
    return {
        "name": name,
        "type": "ExecutePipeline",
        "dependsOn": depends,
        "userProperties": [],
        "typeProperties": {
            "pipeline": {"referenceName": "PL_90_Send_Alert", "type": "PipelineReference"},
            "waitOnCompletion": True,
            "parameters": {
                "severity": severity,
                "pipeline_name": E("@pipeline().Pipeline"),
                "entity_name": "ADF_NATIVE_TRANSFORM",
                "message": E(message_expr),
                "adf_run_id": E("@pipeline().RunId"),
            },
        },
    }


def fail(name, guarded):
    return {
        "name": name,
        "type": "Fail",
        "dependsOn": [dep(f"Alert_{guarded}", "Completed")],
        "userProperties": [],
        "typeProperties": {"message": E(f"@activity('{guarded}').error.message"), "errorCode": "ADF_TRANSFORM_FAILED"},
    }


# ---- fixed (non-parameterised) datasets: Power Query does not support dataset parameters
write(
    "dataset",
    "DS_ADLS_Csv_SupplierMerged",
    {
        "description": "Single merged CSV of today's supplier files: the input of the Power Query cleansing.",
        "linkedServiceName": ls_ref("LS_ADLS_Gen2"),
        "type": "DelimitedText",
        "typeProperties": {
            "location": {
                "type": "AzureBlobFSLocation",
                "fileName": "supplier_deliveries_current.csv",
                "folderPath": "supplier_deliveries/powerquery_input",
                "fileSystem": "landing",
            },
            "columnDelimiter": ",",
            "escapeChar": "\\",
            "quoteChar": '"',
            "firstRowAsHeader": True,
            "encodingName": "UTF-8",
        },
        "schema": [],
        "folder": {"name": "PowerQuery"},
    },
)
write(
    "dataset",
    "DS_ADLS_Parquet_SupplierClean",
    {
        "description": "Output of the Power Query cleansing (parquet folder, overwritten each run).",
        "linkedServiceName": ls_ref("LS_ADLS_Gen2"),
        "type": "Parquet",
        "typeProperties": {
            "location": {"type": "AzureBlobFSLocation", "folderPath": "supplier_files/delivery_powerquery", "fileSystem": "raw"},
            "compressionCodec": "snappy",
        },
        "schema": [],
        "folder": {"name": "PowerQuery"},
    },
)

# ---- Power Query (wrangling data flow): business-readable cleansing steps in M
PQ_SOURCE = "SupplierDeliveries"
PQ_M_SCRIPT = (
    "section Section1;\r\n"
    f'shared {PQ_SOURCE} = let AdfDoc = AzureStorage.DataLakeContents("{ADLS_URL}/landing/supplier_deliveries/powerquery_input/supplier_deliveries_current.csv"), '
    'Csv = Csv.Document(AdfDoc, [Delimiter = ",", Encoding = TextEncoding.Utf8, QuoteStyle = QuoteStyle.Csv]), '
    "PromotedHeaders = Table.PromoteHeaders(Csv, [PromoteAllScalars = true]) in PromotedHeaders;\r\n"
    "shared UserQuery = let\r\n"
    f"    Source = {PQ_SOURCE},\r\n"
    "    // 1. trim stray spaces on every text key\r\n"
    '    Trimmed = Table.TransformColumns(Source, {{"delivery_id", Text.Trim}, {"supplier_id", Text.Trim}, {"supplier_name", Text.Trim}, '
    '{"material_code", Text.Trim}, {"plant_id", Text.Trim}, {"lot_number", Text.Trim}}),\r\n'
    "    // 2. standardise codes to upper case, names to proper case\r\n"
    '    Uppercased = Table.TransformColumns(Trimmed, {{"delivery_id", Text.Upper}, {"supplier_id", Text.Upper}, {"material_code", Text.Upper}, '
    '{"plant_id", Text.Upper}, {"lot_number", Text.Upper}}),\r\n'
    '    ProperNames = Table.TransformColumns(Uppercased, {{"supplier_name", Text.Proper}}),\r\n'
    "    // 3. data types\r\n"
    '    Typed = Table.TransformColumnTypes(ProperNames, {{"delivery_date", type date}, {"delivered_qty", Int64.Type}, {"rejected_qty", Int64.Type}}),\r\n'
    "    // 4. business rules: key present, quantities valid\r\n"
    '    HasKey = Table.SelectRows(Typed, each [delivery_id] <> null and [delivery_id] <> ""),\r\n'
    "    ValidQty = Table.SelectRows(HasKey, each [delivered_qty] > 0 and [rejected_qty] >= 0 and [rejected_qty] <= [delivered_qty]),\r\n"
    "    // 5. suppliers re-send lines: one row per delivery_id\r\n"
    '    Deduped = Table.Distinct(ValidQty, {"delivery_id"}),\r\n'
    "    // 6. derived measure\r\n"
    '    WithAccepted = Table.AddColumn(Deduped, "accepted_qty", each [delivered_qty] - [rejected_qty], Int64.Type)\r\n'
    "in\r\n"
    "    WithAccepted;\r\n"
)
write(
    "dataflow",
    "PQ_Supplier_Delivery_Cleansing",
    {
        "type": "WranglingDataFlow",
        "description": "Power Query (M) cleansing of supplier delivery files: trim, standardise case, types, validity rules, dedup, accepted_qty. "
        "Editable by analysts in the Power Query editor; ADF translates the M steps to Spark at run time.",
        "folder": {"name": "PowerQuery"},
        "typeProperties": {
            "sources": [
                {
                    "name": PQ_SOURCE,
                    "script": f"source(allowSchemaDrift: true,\n\tvalidateSchema: false,\n\tignoreNoFilesFound: false) ~> {PQ_SOURCE}",
                    "dataset": ds_ref("DS_ADLS_Csv_SupplierMerged"),
                }
            ],
            "script": PQ_M_SCRIPT,
            "documentLocale": "en-us",
        },
    },
)

write(
    "pipeline",
    "PL_14_PowerQuery_Supplier_Cleansing",
    {
        "description": "Merge today's supplier CSVs into one file -> Power Query cleansing (M) -> parquet in raw. "
        "Self-service alternative to the code path (PL_13 + Databricks silver): analysts own the cleansing rules.",
        "activities": [
            {
                "name": "Copy_Merge_Supplier_Files",
                "description": "Power Query reads one file, so all incoming supplier CSVs are merged into supplier_deliveries_current.csv first.",
                "type": "Copy",
                "dependsOn": [],
                "policy": policy(**RETRY, timeout="0.01:00:00"),
                "userProperties": [],
                "typeProperties": {
                    "source": {
                        "type": "DelimitedTextSource",
                        "storeSettings": {
                            "type": "AzureBlobFSReadSettings",
                            "recursive": False,
                            "wildcardFolderPath": "supplier_deliveries/incoming",
                            "wildcardFileName": "*_deliveries_*.csv",
                            "enablePartitionDiscovery": False,
                        },
                        "formatSettings": {"type": "DelimitedTextReadSettings"},
                    },
                    "sink": {
                        "type": "DelimitedTextSink",
                        "storeSettings": {"type": "AzureBlobFSWriteSettings", "copyBehavior": "MergeFiles"},
                        "formatSettings": {"type": "DelimitedTextWriteSettings", "quoteAllText": True, "fileExtension": ".csv"},
                    },
                    "enableStaging": False,
                },
                "inputs": [ds_ref("DS_ADLS_Csv", {"container": "landing", "folder": "supplier_deliveries/incoming"})],
                "outputs": [ds_ref("DS_ADLS_Csv_SupplierMerged")],
            },
            {
                "name": "Run_PowerQuery_Cleansing",
                "type": "ExecuteWranglingDataflow",
                "dependsOn": [dep("Copy_Merge_Supplier_Files")],
                "policy": policy(retry=1, interval=120, timeout="0.02:00:00"),
                "userProperties": [],
                "typeProperties": {
                    "dataFlow": {"referenceName": "PQ_Supplier_Delivery_Cleansing", "type": "DataFlowReference"},
                    "compute": {"coreCount": 8, "computeType": "General"},
                    "traceLevel": "Fine",
                    "queries": [
                        {
                            "queryName": "UserQuery",
                            "dataflowSinks": [
                                {
                                    "name": "SupplierCleanSink",
                                    "script": "sink(allowSchemaDrift: true,\n\tvalidateSchema: false,\n\tformat: 'parquet',\n\ttruncate: true,\n\t"
                                    "skipDuplicateMapInputs: true,\n\tskipDuplicateMapOutputs: true) ~> SupplierCleanSink",
                                    "dataset": ds_ref("DS_ADLS_Parquet_SupplierClean"),
                                }
                            ],
                        }
                    ],
                },
            },
            standalone_alert(
                "Alert_Copy_Merge_Supplier_Files", "HIGH", "@activity('Copy_Merge_Supplier_Files').error.message", [dep("Copy_Merge_Supplier_Files", "Failed")]
            ),
            fail("Fail_Copy_Merge_Supplier_Files", "Copy_Merge_Supplier_Files"),
            standalone_alert(
                "Alert_Run_PowerQuery_Cleansing", "HIGH", "@activity('Run_PowerQuery_Cleansing').error.message", [dep("Run_PowerQuery_Cleansing", "Failed")]
            ),
            fail("Fail_Run_PowerQuery_Cleansing", "Run_PowerQuery_Cleansing"),
        ],
        "folder": {"name": "15_ADF_Native_Transform"},
        "annotations": ["power-query"],
    },
)

# ---- Mapping data flow: apply landed SQL Server CDC changes (latest per key, delete/upsert) into Delta
CDC_COLS = [
    ("cdc_start_lsn", "string"),
    ("cdc_seqval", "string"),
    ("cdc_operation", "integer"),
    ("cdc_update_mask", "string"),
    ("cdc_commit_ts", "timestamp"),
    ("production_log_id", "long"),
    ("order_id", "string"),
    ("machine_id", "string"),
    ("product_id", "string"),
    ("shift_code", "string"),
    ("start_ts", "timestamp"),
    ("end_ts", "timestamp"),
    ("units_produced", "integer"),
    ("units_scrapped", "integer"),
    ("operator_id", "string"),
    ("modified_at", "timestamp"),
]
KEEP = [c for c, _ in CDC_COLS if c not in ("cdc_seqval", "cdc_update_mask")]
DF_SCRIPT = (
    [
        "parameters{",
        "     cdc_folder as string ('sqlserver_mes/production_log')",
        "}",
        "source(output(",
    ]
    + [f"          {c} as {t}{',' if i < len(CDC_COLS) - 1 else ''}" for i, (c, t) in enumerate(CDC_COLS)]
    + [
        "     ),",
        "     allowSchemaDrift: true,",
        "     validateSchema: false,",
        "     ignoreNoFilesFound: true,",
        "     format: 'parquet',",
        "     fileSystem: 'raw',",
        "     wildcardPaths:[(concat($cdc_folder, '/**/*.parquet'))]) ~> CdcChanges",
        "CdcChanges window(over(production_log_id),",
        "     desc(cdc_start_lsn, true),",
        "     desc(cdc_seqval, true),",
        "     version_rank = rowNumber()) ~> RankVersions",
        "RankVersions filter(version_rank == 1) ~> LatestPerKey",
        "LatestPerKey alterRow(deleteIf(cdc_operation == 1),",
        "     upsertIf(cdc_operation != 1)) ~> MarkRowAction",
        "MarkRowAction select(mapColumn(",
    ]
    + [f"          {c}{',' if i < len(KEEP) - 1 else ''}" for i, c in enumerate(KEEP)]
    + [
        "     ),",
        "     skipDuplicateMapInputs: true,",
        "     skipDuplicateMapOutputs: true) ~> FinalColumns",
        "FinalColumns sink(allowSchemaDrift: true,",
        "     validateSchema: false,",
        "     format: 'delta',",
        "     fileSystem: 'curated',",
        "     folderPath: 'adf_cdc/mes_production_log',",
        "     mergeSchema: false,",
        "     autoCompact: true,",
        "     optimizedWrite: true,",
        "     vacuum: 0,",
        "     deletable: true,",
        "     insertable: true,",
        "     updateable: false,",
        "     upsertable: true,",
        "     keys:['production_log_id'],",
        "     umask: 0022,",
        "     preCommands: [],",
        "     postCommands: [],",
        "     skipDuplicateMapInputs: true,",
        "     skipDuplicateMapOutputs: true) ~> DeltaCurated",
    ]
)
write(
    "dataflow",
    "DF_MES_ProductionLog_CDC_Apply",
    {
        "type": "MappingDataFlow",
        "description": "ADF-native CDC apply: reads the CDC rows PL_10 landed for one run (ops 1/2/4), keeps the LATEST version per "
        "production_log_id (window: lsn desc, seqval desc), marks deletes (op 1) and upserts (op 2/4) with Alter Row, "
        "and MERGEs into a Delta table in the curated container. Alternative to the Databricks silver merge.",
        "folder": {"name": "CDC"},
        "typeProperties": {
            "sources": [{"linkedService": ls_ref("LS_ADLS_Gen2"), "name": "CdcChanges"}],
            "sinks": [{"linkedService": ls_ref("LS_ADLS_Gen2"), "name": "DeltaCurated"}],
            "transformations": [{"name": "RankVersions"}, {"name": "LatestPerKey"}, {"name": "MarkRowAction"}, {"name": "FinalColumns"}],
            "scriptLines": DF_SCRIPT,
        },
    },
)

write(
    "pipeline",
    "PL_15_CDC_Apply_DataFlow",
    {
        "description": "Applies ONE landed CDC run (raw/sqlserver_mes/production_log/load_date=.../run_id=...) to the curated Delta table "
        "via a mapping data flow. Run runs in order (the hourly tumbling window guarantees it), because Alter Row upsert has "
        "no sequence guard against an older replay (the Databricks silver merge does).",
        "activities": [
            {
                "name": "Apply_CDC_To_Delta",
                "type": "ExecuteDataFlow",
                "dependsOn": [],
                "policy": policy(retry=1, interval=120, timeout="0.02:00:00"),
                "userProperties": [],
                "typeProperties": {
                    "dataflow": {
                        "referenceName": "DF_MES_ProductionLog_CDC_Apply",
                        "type": "DataFlowReference",
                        "parameters": {"cdc_folder": {"value": "'@{pipeline().parameters.run_path}'", "type": "Expression"}},
                    },
                    "compute": {"coreCount": 8, "computeType": "General"},
                    "traceLevel": "Fine",
                },
            },
            standalone_alert("Alert_Apply_CDC_To_Delta", "HIGH", "@activity('Apply_CDC_To_Delta').error.message", [dep("Apply_CDC_To_Delta", "Failed")]),
            fail("Fail_Apply_CDC_To_Delta", "Apply_CDC_To_Delta"),
        ],
        "parameters": {
            "run_path": {
                "type": "string",
                "defaultValue": "sqlserver_mes/production_log/load_date=2026-09-25/run_id=mes_production_log_20260925_01",
            }
        },
        "folder": {"name": "15_ADF_Native_Transform"},
        "annotations": ["cdc", "data-flow"],
    },
)

print("ADF artifacts written")
