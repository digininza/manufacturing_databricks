import re

import pytest

from src.common.config import EntityConfig, load_config, load_registry, validate_entity


def test_all_environments_load_and_have_no_secrets(repo_root):
    for env in ("dev", "test", "prod", "local_test", "sandbox"):
        cfg = load_config(env)
        assert cfg.environment == env
        text = (repo_root / "configs" / f"{env}.yml").read_text().lower()
        for forbidden in ("password:", "accountkey", "sharedaccesssignature", "begin private key"):
            assert forbidden not in text, f"{env}.yml looks like it contains a secret"


def test_fq_uses_three_part_names_except_local():
    assert load_config("dev").fq("gold", "dim_machine") == "mfg_dev.gold.dim_machine"
    assert load_config("local_test").fq("gold", "dim_machine") == "gold.dim_machine"


def test_registry_is_valid():
    registry = load_registry()
    assert len(registry) == 8
    assert registry["mes_production_log"].is_cdc
    assert not registry["erp_product"].is_cdc


def test_invalid_entity_is_rejected():
    bad = EntityConfig(
        "bad",
        {
            "load_pattern": "SQLSERVER_CDC",
            "source_system": "X",
            "raw_path": "x",
            "silver_table": "x",
            "primary_keys": ["id"],
            "sequence_by": ["cdc_start_lsn"],
            "columns": {},
        },
    )
    with pytest.raises(ValueError) as err:
        validate_entity(bad)
    assert "primary key id not in columns" in str(err.value)
    assert "delete_condition" in str(err.value)


def test_registry_and_adf_control_table_describe_the_same_entities(repo_root):
    """The contract between ADF and Databricks: same entity names, same raw folder."""
    seed = (repo_root / "sql" / "control_db" / "03_seed_ingestion_control.sql").read_text()
    registry = load_registry()
    for name, entity in registry.items():
        assert f"('{name}'" in seed, f"{name} missing from ctl.ingestion_control seed"
        assert f"'{entity.raw_path}'" in seed, f"{name}: raw_path {entity.raw_path} != control target_folder"
    seeded = set(re.findall(r"\('([a-z_]+)',\s*'(?:SQLSERVER_MES|ORACLE_ERP|CMMS_API|SUPPLIER_FILES)'", seed))
    assert seeded == set(registry), f"control table and registry disagree: {seeded ^ set(registry)}"


def test_fq_rejects_non_identifier_table_names():
    cfg = load_config("dev")
    with pytest.raises(ValueError, match="invalid SQL identifier"):
        cfg.fq("gold", "dim_machine; DROP TABLE x")


def test_require_https_blocks_other_schemes():
    from src.common.notebook_utils import require_https

    assert require_https("https://example.com/hook") == "https://example.com/hook"
    with pytest.raises(ValueError):
        require_https("file:///etc/passwd")
