from src.publish.snowflake_publisher import PUBLISH_SPECS, build_snowflake_merge
from src.silver.cdc import build_merge_sql


def test_silver_merge_is_sequence_guarded_and_skips_unknown_deletes():
    sql = build_merge_sql("mfg_dev.silver.x", "src", ["id"], ["id", "v", "_sequence", "_is_deleted"])
    assert "WHEN MATCHED AND s._sequence > t._sequence THEN UPDATE" in sql  # replay/out-of-order safe
    assert "WHEN NOT MATCHED AND s._is_deleted = false THEN INSERT" in sql  # never insert a delete
    assert "t.`id` <=> s.`id`" in sql


def test_composite_key_merge():
    sql = build_merge_sql("t", "s", ["a", "b"], ["a", "b", "_sequence", "_is_deleted"])
    assert "t.`a` <=> s.`a` AND t.`b` <=> s.`b`" in sql


def test_snowflake_merge_handles_deletes_and_upserts():
    sql = build_snowflake_merge("GOLD.FACT_X", "STAGE.FACT_X_CHANGES", ["production_date", "machine_id"], ["production_date", "machine_id", "units"])
    assert "ON t.PRODUCTION_DATE = s.PRODUCTION_DATE AND t.MACHINE_ID = s.MACHINE_ID" in sql
    assert "WHEN MATCHED AND s._CHANGE_TYPE = 'delete' THEN DELETE" in sql
    assert "WHEN NOT MATCHED AND s._CHANGE_TYPE <> 'delete' THEN INSERT" in sql


def test_publish_order_dimensions_before_facts():
    names = [s.table for s in PUBLISH_SPECS]
    last_dim = max(i for i, n in enumerate(names) if n.startswith("dim_"))
    first_fact = min(i for i, n in enumerate(names) if n.startswith("fact_"))
    assert last_dim < first_fact


def test_every_published_table_exists_in_both_ddls(repo_root):
    dbx = (repo_root / "sql" / "databricks" / "02_gold_tables.sql").read_text()
    sf = (repo_root / "sql" / "snowflake" / "02_gold_tables.sql").read_text()
    for spec in PUBLISH_SPECS:
        assert f"gold.{spec.table} (" in dbx, spec.table
        assert f"TABLE IF NOT EXISTS {spec.table.upper()} (" in sf, spec.table
