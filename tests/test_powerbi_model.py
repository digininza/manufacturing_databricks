"""Power BI TMDL model integrity: tables, relationships and measure references resolve."""

import re

import pytest


@pytest.fixture(scope="module")
def model(repo_root):
    d = repo_root / "powerbi" / "NorthForge_Manufacturing.SemanticModel" / "definition"
    tables = {}
    for f in (d / "tables").glob("*.tmdl"):
        text = f.read_text()
        name = re.search(r"^table '?([^'\n]+?)'?$", text, re.M).group(1)
        cols = set(re.findall(r"^\tcolumn '?([^'\n]+?)'?$", text, re.M))
        measures = set(re.findall(r"^\tmeasure '([^']+)'", text, re.M))
        mode = re.findall(r"^\t\tmode: (\w+)", text, re.M)
        tables[name] = {"cols": cols, "measures": measures, "mode": mode, "text": text}
    return d, tables


def test_model_lists_every_table(model):
    d, tables = model
    refs = set(re.findall(r"^ref table '?([^'\n]+?)'?$", (d / "model.tmdl").read_text(), re.M))
    assert refs == set(tables)


def test_tmdl_uses_tabs_not_spaces(model):
    d, _ = model
    for f in d.rglob("*.tmdl"):
        for line in f.read_text().splitlines():
            assert not line.startswith("    "), f"{f.name}: TMDL must be tab-indented"


def test_relationships_resolve(model):
    d, tables = model
    rels = re.findall(r"fromColumn: '?([^'.\n]+)'?\.(\w+)\n\ttoColumn: '?([^'.\n]+)'?\.(\w+)", (d / "relationships.tmdl").read_text())
    assert len(rels) >= 15
    for ft, fc, tt, tc in rels:
        assert fc in tables[ft]["cols"], f"{ft}.{fc}"
        assert tc in tables[tt]["cols"], f"{tt}.{tc}"


def test_measure_references_resolve(model):
    _, tables = model
    all_measures = set().union(*(t["measures"] for t in tables.values()))
    text = tables["_Measures"]["text"]
    for tbl, col in re.findall(r"'([^']+)'\[(\w+)\]", text):
        assert col in tables[tbl]["cols"], f"measure references missing column {tbl}[{col}]"
    for ref in re.findall(r"(?<!')\[([A-Za-z][^\]]*)\]", text):
        if ref in all_measures or any(ref in t["cols"] for t in tables.values()):
            continue
        pytest.fail(f"unresolved reference [{ref}]")


def test_storage_modes(model):
    _, tables = model
    assert tables["Shopfloor Status"]["mode"] == ["directQuery"]
    assert tables["Exec KPI Monthly"]["mode"] == ["directQuery"]
    assert tables["Plant"]["mode"] == ["dual"]
    assert "refreshPolicy" in tables["Fact Production Daily"]["text"]


def test_directquery_tables_read_governed_views(model):
    _, tables = model
    for name in ("Shopfloor Status", "Exec KPI Monthly"):
        assert 'Name = "REPORTING"' in tables[name]["text"]


def test_views_used_by_power_bi_exist_in_snowflake_sql(repo_root, model):
    _, tables = model
    views_sql = (repo_root / "sql" / "snowflake" / "03_reporting_views.sql").read_text()
    for t in tables.values():
        for view in re.findall(r'Name = "(VW_\w+)"', t["text"]):
            assert f"VIEW {view} AS" in views_sql, view
