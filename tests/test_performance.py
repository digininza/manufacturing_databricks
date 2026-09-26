"""Guard-rails on the performance / maintenance configuration (pure Python)."""

from src.common.config import load_config
from src.common.performance import files_for
from src.framework.run_context import RunContext
from src.framework.table_maintenance import TABLE_STRATEGIES, VACUUM_RETAIN_HOURS, maintenance_statements


def test_vacuum_never_below_seven_days():
    assert VACUUM_RETAIN_HOURS >= 168


def test_zorder_only_on_partitioned_tables_and_scoped_to_recent_partitions():
    for s in TABLE_STRATEGIES:
        if s.mode == "ZORDER":
            assert s.partition_col, f"{s.table}: ZORDER must be scoped to recent partitions"
            assert 1 <= len(s.zorder_by) <= 4, f"{s.table}: too many ZORDER columns dilutes skipping"


def test_zorder_never_on_liquid_clustered_gold_or_silver_entities(repo_root):
    ddl = (repo_root / "sql" / "databricks" / "02_gold_tables.sql").read_text()
    for s in TABLE_STRATEGIES:
        if s.layer == "gold" and f"gold.{s.table} (" in ddl:
            assert s.mode != "ZORDER", f"{s.table} is liquid-clustered: ZORDER is not allowed"


def test_statements_are_well_formed():
    ctx = RunContext(load_config("dev"), "t", "r")
    stmts = maintenance_statements(ctx)
    assert "OPTIMIZE mfg_dev.silver.iot_sensor_reading WHERE event_date >= current_date() - INTERVAL 3 DAYS ZORDER BY (machine_id, sensor_type)" in stmts
    assert "VACUUM mfg_dev.gold.fact_production_daily RETAIN 168 HOURS" in stmts
    assert "ANALYZE TABLE mfg_dev.gold.dim_machine COMPUTE STATISTICS FOR ALL COLUMNS" in stmts
    assert not any(s.startswith("OPTIMIZE mfg_dev.silver.erp_plant") for s in stmts)  # tiny table skipped


def test_files_for_sizing():
    assert files_for(0) == 1
    assert files_for(5_000_000, rows_per_file=1_000_000) == 5
    assert files_for(10**12) == 64  # capped
