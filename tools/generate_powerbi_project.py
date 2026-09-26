"""
Generates the Power BI PBIP project in powerbi/ (TMDL semantic model + report scaffold).

TMDL is tab-indented and whitespace-sensitive, so the files are generated. Once
opened in Power BI Desktop, Desktop becomes the editor and saves back to TMDL.

    python tools/generate_powerbi_project.py
"""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "powerbi"
NAME = "NorthForge_Manufacturing"
SM = ROOT / f"{NAME}.SemanticModel"
RP = ROOT / f"{NAME}.Report"
if SM.exists():
    shutil.rmtree(SM)
if RP.exists():
    shutil.rmtree(RP)
DEF = SM / "definition"
(DEF / "tables").mkdir(parents=True)
(DEF / "roles").mkdir(parents=True)
RP.mkdir(parents=True)

T = "\t"


def w(path: Path, text: str):
    path.write_text(text.rstrip() + "\n")


# ------------------------------------------------------------------ project files
w(
    ROOT / f"{NAME}.pbip",
    json.dumps({"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}}, indent=2),
)
w(SM / "definition.pbism", json.dumps({"version": "4.0", "settings": {}}, indent=2))
w(RP / "definition.pbir", json.dumps({"version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2))

w(DEF / "database.tmdl", "database\n\tcompatibilityLevel: 1601\n")

# ------------------------------------------------------------------ parameters (Power Query)
params = [
    ("SnowflakeServer", '"REPLACE_WITH_ACCOUNT.snowflakecomputing.com"', "Text"),
    ("SnowflakeWarehouse", '"WH_MFG_BI"', "Text"),
    ("SnowflakeDatabase", '"MFG_DW"', "Text"),
    ("RangeStart", "#datetime(2024, 1, 1, 0, 0, 0)", "DateTime"),
    ("RangeEnd", "#datetime(2030, 12, 31, 0, 0, 0)", "DateTime"),
]
expr = []
for n, v, t in params:
    expr.append(f'expression {n} = {v} meta [IsParameterQuery=true, Type="{t}", IsParameterQueryRequired=true]\n\tqueryGroup: Parameters\n')
w(DEF / "expressions.tmdl", "\n".join(expr))


def m_source(schema: str, obj: str, incremental_col: str = None) -> str:
    lines = [
        "let",
        '    Source = Snowflake.Databases(SnowflakeServer, SnowflakeWarehouse, [Role = "MFG_BI_READER_ROLE", Implementation = "2.0"]),',
        '    Db = Source{[Name = SnowflakeDatabase, Kind = "Database"]}[Data],',
        f'    Sch = Db{{[Name = "{schema}", Kind = "Schema"]}}[Data],',
        f'    Obj = Sch{{[Name = "{obj}", Kind = "{"View" if schema == "REPORTING" else "Table"}"]}}[Data]' + ("," if incremental_col else ""),
    ]
    if incremental_col:
        lines.append(
            f"    Filtered = Table.SelectRows(Obj, each [{incremental_col}] >= Date.From(RangeStart) and [{incremental_col}] < Date.From(RangeEnd))  // folds to Snowflake WHERE"
        )
    lines += ["in", "    " + ("Filtered" if incremental_col else "Obj")]
    return "\n".join(lines)


def indent_block(text: str, level: int) -> str:
    return "\n".join(T * level + line for line in text.splitlines())


def column(name, dtype, source=None, fmt=None, hidden=False, summarize="none", extra=None):
    out = [f"{T}column {name}", f"{T*2}dataType: {dtype}"]
    if fmt:
        out.append(f"{T*2}formatString: {fmt}")
    if hidden:
        out.append(f"{T*2}isHidden")
    out.append(f"{T*2}summarizeBy: {summarize}")
    out.append(f"{T*2}sourceColumn: {source or name.upper()}")
    for e in extra or []:
        out.append(f"{T*2}{e}")
    return "\n".join(out) + "\n"


def table(
    file_name, table_name, cols, schema, obj, mode="import", incremental_col=None, description=None, hidden=False, extra_header=None, refresh_policy=None
):
    parts = []
    if description:
        parts.append(f"/// {description}")
    parts.append(f"table '{table_name}'")
    if hidden:
        parts.append(f"{T}isHidden")
    for e in extra_header or []:
        parts.append(f"{T}{e}")
    parts.append("")
    for c in cols:
        parts.append(column(*c[:2], **(c[2] if len(c) > 2 else {})))
    src = m_source(schema, obj, incremental_col)
    parts.append(f"{T}partition '{table_name}' = m\n{T*2}mode: {mode}\n{T*2}source =\n{indent_block(src, 4)}\n")
    if refresh_policy:
        parts.append(refresh_policy)
    w(DEF / "tables" / f"{file_name}.tmdl", "\n".join(parts))


def incremental_policy(col):
    src = m_source("GOLD", "X", col)  # placeholder replaced below
    return src


def refresh_policy(schema, obj, col, years=3, days=10):
    src = m_source(schema, obj, col)
    return (
        f"{T}refreshPolicy\n"
        f"{T*2}policyType: basic\n"
        f"{T*2}rollingWindowGranularity: year\n"
        f"{T*2}rollingWindowPeriods: {years}\n"
        f"{T*2}incrementalGranularity: day\n"
        f"{T*2}incrementalPeriods: {days}\n"
        f"{T*2}sourceExpression =\n{indent_block(src, 4)}\n"
    )


KEYS = {"hidden": True}
INT_KEY = ("int64", KEYS)

# ------------------------------------------------------------------ dimensions
table(
    "Date",
    "Date",
    [
        ("date_key", "int64", {"hidden": True}),
        ("calendar_date", "dateTime", {"fmt": "yyyy-mm-dd", "extra": ["isKey"]}),
        ("year", "int64"),
        ("quarter", "int64"),
        ("month", "int64", {"hidden": True}),
        ("month_name", "string", {"extra": ["sortByColumn: month"]}),
        ("iso_week", "int64"),
        ("day_name", "string"),
        ("is_weekend", "boolean"),
        ("fiscal_year", "string"),
    ],
    "GOLD",
    "DIM_DATE",
    description="Calendar. Marked as the model date table so time-intelligence DAX works.",
    extra_header=["dataCategory: Time"],
)
table(
    "Plant",
    "Plant",
    [
        ("plant_sk", "int64", KEYS),
        ("plant_id", "string"),
        ("plant_name", "string"),
        ("city", "string"),
        ("country_code", "string", {"extra": ["dataCategory: Country"]}),
    ],
    "GOLD",
    "DIM_PLANT",
    mode="dual",
    description="DUAL storage: acts as Import for the star schema AND as DirectQuery when filtering the DirectQuery report tables (no limited relationships).",
)
table(
    "Machine",
    "Machine",
    [
        ("machine_sk", "int64", KEYS),
        ("machine_id", "string"),
        ("machine_name", "string"),
        ("plant_id", "string"),
        ("line_id", "string"),
        ("machine_type", "string"),
        ("manufacturer", "string"),
        ("status", "string"),
        ("rated_units_per_hour", "int64"),
        ("effective_from", "dateTime", {"fmt": "yyyy-mm-dd hh:nn"}),
        ("effective_to", "dateTime", {"fmt": "yyyy-mm-dd hh:nn"}),
        ("is_current", "boolean"),
    ],
    "GOLD",
    "DIM_MACHINE",
    description="SCD2: every version is loaded; facts point at the version valid on the production day, so a machine moved from line L2 to L3 reports history under L2.",
)
table(
    "Product",
    "Product",
    [
        ("product_sk", "int64", KEYS),
        ("product_id", "string"),
        ("sku", "string"),
        ("product_name", "string"),
        ("product_family", "string"),
        ("unit_cost", "decimal", {"fmt": '"$"#,0.00'}),
        ("std_cycle_time_sec", "int64"),
        ("effective_from", "dateTime"),
        ("effective_to", "dateTime"),
        ("is_current", "boolean"),
    ],
    "GOLD",
    "DIM_PRODUCT",
    description="SCD2: unit_cost / std_cycle_time as of the production day -> historic scrap cost and performance are not restated.",
)
table(
    "Defect Type",
    "Defect Type",
    [("defect_code", "string"), ("defect_description", "string"), ("defect_category", "string"), ("severity", "string")],
    "GOLD",
    "DIM_DEFECT_TYPE",
)
table("Supplier", "Supplier", [("supplier_sk", "int64", KEYS), ("supplier_id", "string"), ("supplier_name", "string")], "GOLD", "DIM_SUPPLIER")
table(
    "Plant Entitlement",
    "Plant Entitlement",
    [("user_email", "string"), ("plant_id", "string")],
    "SECURITY",
    "PLANT_ENTITLEMENT",
    hidden=True,
    description="Same entitlement table the Snowflake row access policy uses -> ONE source of truth for Import RLS and DirectQuery RLS.",
)

# ------------------------------------------------------------------ facts (import + incremental refresh)
table(
    "Fact Production Daily",
    "Fact Production Daily",
    [
        ("date_key", *[INT_KEY[0]], KEYS),
        ("production_date", "dateTime", {"fmt": "yyyy-mm-dd", "hidden": True}),
        ("plant_sk", "int64", KEYS),
        ("machine_sk", "int64", KEYS),
        ("product_sk", "int64", KEYS),
        ("production_runs", "int64", {"hidden": True}),
        ("units_produced", "int64", {"hidden": True}),
        ("units_scrapped", "int64", {"hidden": True}),
        ("units_good", "int64", {"hidden": True}),
        ("run_minutes", "decimal", {"hidden": True}),
        ("ideal_units", "int64", {"hidden": True}),
        ("inspections", "int64", {"hidden": True}),
        ("sampled_units", "int64", {"hidden": True}),
        ("defect_units", "int64", {"hidden": True}),
        ("failed_inspections", "int64", {"hidden": True}),
        ("_gold_updated_ts", "dateTime", {"hidden": True}),
    ],
    "GOLD",
    "FACT_PRODUCTION_DAILY",
    incremental_col="PRODUCTION_DATE",
    description="GRAIN: production_date x machine x product. Incremental refresh: 3 years kept, last 10 days refreshed (covers late MES corrections via CDC).",
    refresh_policy=refresh_policy("GOLD", "FACT_PRODUCTION_DAILY", "PRODUCTION_DATE"),
)
table(
    "Fact Machine Daily",
    "Fact Machine Daily",
    [
        ("date_key", "int64", KEYS),
        ("plant_sk", "int64", KEYS),
        ("machine_sk", "int64", KEYS),
        ("planned_minutes", "int64", {"hidden": True}),
        ("run_minutes", "decimal", {"hidden": True}),
        ("downtime_minutes", "int64", {"hidden": True}),
        ("available_minutes", "int64", {"hidden": True}),
        ("breakdowns", "int64", {"hidden": True}),
        ("ideal_units", "int64", {"hidden": True}),
        ("units_produced", "int64", {"hidden": True}),
        ("units_good", "int64", {"hidden": True}),
        ("anomaly_minutes", "int64", {"hidden": True}),
        ("avg_temperature_c", "decimal", {"hidden": True}),
        ("max_vibration_mm_s", "decimal", {"hidden": True}),
    ],
    "GOLD",
    "FACT_MACHINE_DAILY",
    incremental_col="PRODUCTION_DATE",
    description="GRAIN: production_date x machine. OEE components are summed and OEE re-derived in DAX (never average OEE %).",
    refresh_policy=refresh_policy("GOLD", "FACT_MACHINE_DAILY", "PRODUCTION_DATE"),
)
table(
    "Fact Quality Inspection",
    "Fact Quality Inspection",
    [
        ("inspection_id", "int64", {"hidden": True}),
        ("date_key", "int64", KEYS),
        ("machine_sk", "int64", KEYS),
        ("product_sk", "int64", KEYS),
        ("defect_code", "string", KEYS),
        ("inspection_ts", "dateTime"),
        ("sample_size", "int64", {"hidden": True}),
        ("defect_count", "int64", {"hidden": True}),
        ("inspection_result", "string"),
        ("inspector_id", "string"),
    ],
    "GOLD",
    "FACT_QUALITY_INSPECTION",
    description="GRAIN: one inspection. Used for defect drill-through.",
)
table(
    "Fact Maintenance Event",
    "Fact Maintenance Event",
    [
        ("work_order_id", "string"),
        ("reported_date_key", "int64", KEYS),
        ("machine_sk", "int64", KEYS),
        ("work_type", "string"),
        ("priority", "string"),
        ("status", "string"),
        ("reported_at", "dateTime"),
        ("completed_at", "dateTime"),
        ("downtime_minutes", "int64", {"hidden": True}),
        ("repair_minutes", "int64", {"hidden": True}),
        ("response_minutes", "int64", {"hidden": True}),
        ("technician", "string"),
    ],
    "GOLD",
    "FACT_MAINTENANCE_EVENT",
    description="GRAIN: one work order (accumulating snapshot).",
)
table(
    "Fact Supplier Delivery",
    "Fact Supplier Delivery",
    [
        ("delivery_id", "string", {"hidden": True}),
        ("date_key", "int64", KEYS),
        ("supplier_sk", "int64", KEYS),
        ("plant_sk", "int64", KEYS),
        ("material_code", "string"),
        ("delivered_qty", "int64", {"hidden": True}),
        ("rejected_qty", "int64", {"hidden": True}),
    ],
    "GOLD",
    "FACT_SUPPLIER_DELIVERY",
)

# ------------------------------------------------------------------ DirectQuery tables on Snowflake REPORTING views
table(
    "Shopfloor Status",
    "Shopfloor Status",
    [
        ("plant_id", "string"),
        ("line_id", "string"),
        ("machine_id", "string"),
        ("machine_name", "string"),
        ("mes_status", "string"),
        ("units_produced", "int64", {"summarize": "sum"}),
        ("oee", "double", {"fmt": "0.0%", "summarize": "average"}),
        ("downtime_minutes", "int64", {"summarize": "sum"}),
        ("anomaly_minutes", "int64", {"summarize": "sum"}),
        ("open_work_orders", "int64", {"summarize": "sum"}),
        ("health_rag", "string"),
        ("data_as_of", "dateTime"),
    ],
    "REPORTING",
    "VW_SHOPFLOOR_MACHINE_STATUS",
    mode="directQuery",
    description="DirectQuery: hourly-published MES data must show without waiting for a dataset refresh; tiny result set; Snowflake row access policy filters per SSO user.",
)
table(
    "Exec KPI Monthly",
    "Exec KPI Monthly",
    [
        ("month_start", "dateTime", {"fmt": "mmm yyyy"}),
        ("plant_id", "string"),
        ("units_produced", "int64", {"summarize": "sum"}),
        ("scrap_rate", "double", {"fmt": "0.00%"}),
        ("first_pass_yield", "double", {"fmt": "0.00%"}),
        ("availability", "double", {"fmt": "0.0%"}),
        ("scrap_cost", "decimal", {"fmt": '"$"#,0', "summarize": "sum"}),
    ],
    "REPORTING",
    "VW_EXEC_KPI_MONTHLY",
    mode="directQuery",
    description="DirectQuery on the governed SQL KPI view: identical numbers to Finance's Snowflake SQL; <100 rows/year so DQ is instant.",
)

# ------------------------------------------------------------------ measures table
MEASURES = [
    # (name, expression, format, folder, description)
    ("Units Produced", "SUM('Fact Production Daily'[units_produced])", "#,0", "Production", None),
    ("Units Good", "SUM('Fact Production Daily'[units_good])", "#,0", "Production", None),
    ("Units Scrapped", "SUM('Fact Production Daily'[units_scrapped])", "#,0", "Production", None),
    ("Scrap Rate %", "DIVIDE([Units Scrapped], [Units Produced])", "0.00%", "Production", "Scrapped / produced."),
    ("Production Runs", "SUM('Fact Production Daily'[production_runs])", "#,0", "Production", None),
    ("Run Hours", "DIVIDE(SUM('Fact Production Daily'[run_minutes]), 60)", "#,0.0", "Production", None),
    ("Ideal Units", "SUM('Fact Production Daily'[ideal_units])", "#,0", "Production", "Units possible at the standard cycle time (as of the production day)."),
    ("Units Produced PY", "CALCULATE([Units Produced], SAMEPERIODLASTYEAR('Date'[calendar_date]))", "#,0", "Production\\Time Intelligence", None),
    ("Units Produced YoY %", "DIVIDE([Units Produced] - [Units Produced PY], [Units Produced PY])", "0.0%", "Production\\Time Intelligence", None),
    ("Units Produced MTD", "TOTALMTD([Units Produced], 'Date'[calendar_date])", "#,0", "Production\\Time Intelligence", None),
    ("Sampled Units", "SUM('Fact Production Daily'[sampled_units])", "#,0", "Quality", None),
    ("Defect Units", "SUM('Fact Production Daily'[defect_units])", "#,0", "Quality", None),
    ("First Pass Yield %", "1 - DIVIDE([Defect Units], [Sampled Units])", "0.00%", "Quality", "Share of sampled units with no defect."),
    ("Defects PPM", "DIVIDE([Defect Units], [Sampled Units]) * 1000000", "#,0", "Quality", "Defective parts per million sampled."),
    (
        "Inspection Fail Rate %",
        "DIVIDE(SUM('Fact Production Daily'[failed_inspections]), SUM('Fact Production Daily'[inspections]))",
        "0.0%",
        "Quality",
        None,
    ),
    ("Planned Minutes", "SUM('Fact Machine Daily'[planned_minutes])", "#,0", "OEE", None),
    ("Downtime Minutes", "SUM('Fact Machine Daily'[downtime_minutes])", "#,0", "OEE", None),
    ("Availability %", "DIVIDE(SUM('Fact Machine Daily'[available_minutes]), [Planned Minutes])", "0.0%", "OEE", "Available time / planned time."),
    (
        "Performance %",
        "MIN(1, DIVIDE(SUM('Fact Machine Daily'[units_produced]), SUM('Fact Machine Daily'[ideal_units])))",
        "0.0%",
        "OEE",
        "Actual output / output possible at standard cycle time, capped at 100%.",
    ),
    ("Quality %", "DIVIDE(SUM('Fact Machine Daily'[units_good]), SUM('Fact Machine Daily'[units_produced]))", "0.0%", "OEE", "Good units / all units."),
    (
        "OEE %",
        "[Availability %] * [Performance %] * [Quality %]",
        "0.0%",
        "OEE",
        "Overall Equipment Effectiveness. Re-derived from summed components at every level - never an average of daily OEE.",
    ),
    ("OEE Target", "0.85", "0%", "OEE", "World-class benchmark used as the plant target."),
    ("OEE vs Target", "[OEE %] - [OEE Target]", "+0.0%;-0.0%;0.0%", "OEE", None),
    (
        "OEE 7D Rolling",
        "CALCULATE([OEE %], DATESINPERIOD('Date'[calendar_date], MAX('Date'[calendar_date]), -7, DAY))",
        "0.0%",
        "OEE",
        None,
    ),
    ("Sensor Anomaly Minutes", "SUM('Fact Machine Daily'[anomaly_minutes])", "#,0", "OEE", "Minutes with any sensor outside alarm limits (IoT stream)."),
    (
        "Breakdowns",
        "CALCULATE(COUNTROWS('Fact Maintenance Event'), 'Fact Maintenance Event'[work_type] = \"BREAKDOWN\", 'Fact Maintenance Event'[status] = \"COMPLETED\")",
        "#,0",
        "Maintenance",
        None,
    ),
    (
        "MTTR (min)",
        "DIVIDE(CALCULATE(SUM('Fact Maintenance Event'[repair_minutes]), 'Fact Maintenance Event'[work_type] = \"BREAKDOWN\", 'Fact Maintenance Event'[status] = \"COMPLETED\"), [Breakdowns])",
        "#,0",
        "Maintenance",
        "Mean time to repair.",
    ),
    ("MTBF (hrs)", "DIVIDE(DIVIDE(SUM('Fact Machine Daily'[run_minutes]), 60), [Breakdowns])", "#,0.0", "Maintenance", "Mean time between failures."),
    (
        "Open Work Orders",
        "CALCULATE(COUNTROWS('Fact Maintenance Event'), 'Fact Maintenance Event'[status] <> \"COMPLETED\")",
        "#,0",
        "Maintenance",
        None,
    ),
    (
        "Scrap Cost",
        "SUMX('Fact Production Daily', 'Fact Production Daily'[units_scrapped] * RELATED(Product[unit_cost]))",
        '"$"#,0',
        "Cost",
        "Scrap x unit cost of the product version valid on the production day (SCD2 via product_sk).",
    ),
    (
        "Supplier Reject Rate %",
        "DIVIDE(SUM('Fact Supplier Delivery'[rejected_qty]), SUM('Fact Supplier Delivery'[delivered_qty]))",
        "0.00%",
        "Supplier",
        None,
    ),
    (
        "Data As Of",
        "MAX('Fact Production Daily'[_gold_updated_ts])",
        "yyyy-mm-dd hh:nn",
        "Operational",
        "When gold was last published - shown on every page footer.",
    ),
]
mt = ["/// Home table for all measures (keeps the field list clean).", "table _Measures", ""]
for name, dax, fmt, folder, desc in MEASURES:
    if desc:
        mt.append(f"{T}/// {desc}")
    mt.append(f"{T}measure '{name}' = {dax}")
    mt.append(f"{T*2}formatString: {fmt}")
    mt.append(f"{T*2}displayFolder: {folder}")
    mt.append("")
mt.append(f"{T}column Placeholder\n{T*2}dataType: string\n{T*2}isHidden\n{T*2}summarizeBy: none\n{T*2}sourceColumn: [Placeholder]\n")
mt.append(f'{T}partition _Measures = calculated\n{T*2}mode: import\n{T*2}source = ROW("Placeholder", BLANK())\n')
w(DEF / "tables" / "_Measures.tmdl", "\n".join(mt))

# ------------------------------------------------------------------ relationships
rels = [
    ("Fact Production Daily", "date_key", "Date", "date_key"),
    ("Fact Production Daily", "plant_sk", "Plant", "plant_sk"),
    ("Fact Production Daily", "machine_sk", "Machine", "machine_sk"),
    ("Fact Production Daily", "product_sk", "Product", "product_sk"),
    ("Fact Machine Daily", "date_key", "Date", "date_key"),
    ("Fact Machine Daily", "plant_sk", "Plant", "plant_sk"),
    ("Fact Machine Daily", "machine_sk", "Machine", "machine_sk"),
    ("Fact Quality Inspection", "date_key", "Date", "date_key"),
    ("Fact Quality Inspection", "machine_sk", "Machine", "machine_sk"),
    ("Fact Quality Inspection", "product_sk", "Product", "product_sk"),
    ("Fact Quality Inspection", "defect_code", "Defect Type", "defect_code"),
    ("Fact Maintenance Event", "reported_date_key", "Date", "date_key"),
    ("Fact Maintenance Event", "machine_sk", "Machine", "machine_sk"),
    ("Fact Supplier Delivery", "date_key", "Date", "date_key"),
    ("Fact Supplier Delivery", "supplier_sk", "Supplier", "supplier_sk"),
    ("Fact Supplier Delivery", "plant_sk", "Plant", "plant_sk"),
    ("Shopfloor Status", "plant_id", "Plant", "plant_id"),
    ("Exec KPI Monthly", "plant_id", "Plant", "plant_id"),
]


def q(t):
    return f"'{t}'" if " " in t else t


out = []
for i, (ft, fc, dt, dc) in enumerate(rels, 1):
    rid = f"r{i:02d}_{ft.replace(' ', '')}_{fc}"
    out.append(f"relationship {rid}\n{T}fromColumn: {q(ft)}.{fc}\n{T}toColumn: {q(dt)}.{dc}\n")
w(DEF / "relationships.tmdl", "\n".join(out))

# ------------------------------------------------------------------ RLS role
ENT = "UPPER(USERPRINCIPALNAME())"
allowed = (
    f"VAR _me = {ENT}\n"
    "VAR _plants = CALCULATETABLE(VALUES('Plant Entitlement'[plant_id]), 'Plant Entitlement'[user_email] = _me)\n"
    'RETURN "*" IN _plants || [plant_id] IN _plants'
)
w(
    DEF / "roles" / "Plant Scoped.tmdl",
    "/// Plant managers see only their plant(s). Entitlements come from Snowflake SECURITY.PLANT_ENTITLEMENT\n"
    "/// (the same table behind the Snowflake row access policy used by the DirectQuery tables).\n"
    "role 'Plant Scoped'\n"
    f"{T}modelPermission: read\n\n"
    f"{T}tablePermission Plant =\n{indent_block(allowed, 3)}\n\n"
    f"{T}tablePermission Machine =\n{indent_block(allowed, 3)}\n",
)

# ------------------------------------------------------------------ model
tables = [
    "_Measures",
    "Date",
    "Plant",
    "Machine",
    "Product",
    "Defect Type",
    "Supplier",
    "Plant Entitlement",
    "Fact Production Daily",
    "Fact Machine Daily",
    "Fact Quality Inspection",
    "Fact Maintenance Event",
    "Fact Supplier Delivery",
    "Shopfloor Status",
    "Exec KPI Monthly",
]
model = [
    "model Model",
    f"{T}culture: en-US",
    f"{T}defaultPowerBIDataSourceVersion: powerBI_V3",
    f"{T}discourageImplicitMeasures",
    f"{T}sourceQueryCulture: en-US",
    "",
    f"{T}annotation PBI_QueryOrder = {json.dumps(tables)}",
    "",
]
model += [f"ref table {q(t)}" for t in tables]
model += ["", "ref role 'Plant Scoped'"]
w(DEF / "model.tmdl", "\n".join(model))

# ------------------------------------------------------------------ report scaffold (pages; visuals per REPORT_SPEC.md)
pages = ["Executive Overview", "Plant OEE", "Machine Drill-down", "Quality & Defects", "Maintenance", "Supplier Quality", "Shopfloor Live (DirectQuery)"]
report = {
    "config": json.dumps({"version": "5.43", "themeCollection": {"baseTheme": {"name": "CY24SU06", "version": "5.55", "type": 2}}}),
    "layoutOptimization": 0,
    "resourcePackages": [],
    "filters": "[]",
    "sections": [
        {
            "name": f"ReportSection{i}",
            "displayName": p,
            "displayOption": 1,
            "filters": "[]",
            "ordinal": i,
            "height": 720,
            "width": 1280,
            "visualContainers": [],
            "config": "{}",
        }
        for i, p in enumerate(pages)
    ],
}
w(RP / "report.json", json.dumps(report, indent=2))
print("PBIP written")
