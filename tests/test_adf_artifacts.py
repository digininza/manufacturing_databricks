"""ADF artifacts are importable: valid JSON, and every cross-reference resolves.
(These are the mistakes that otherwise only show up when you click 'Publish' in ADF Studio.)"""

import json
import re

import pytest

KINDS = {
    "linkedService": "LinkedServiceReference",
    "dataset": "DatasetReference",
    "pipeline": "PipelineReference",
    "integrationRuntime": "IntegrationRuntimeReference",
}


@pytest.fixture(scope="module")
def adf(repo_root):
    root = repo_root / "adf"
    out = {}
    for folder in ("factory", "integrationRuntime", "linkedService", "dataset", "pipeline", "trigger"):
        out[folder] = {p.stem: json.loads(p.read_text()) for p in (root / folder).glob("*.json")}
    return out


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _activities(acts):
    for a in acts:
        yield a
        tp = a.get("typeProperties", {})
        for key in ("activities", "ifTrueActivities", "ifFalseActivities", "defaultActivities"):
            yield from _activities(tp.get(key, []))
        for case in tp.get("cases", []):
            yield from _activities(case["activities"])


def test_every_file_name_matches_artifact_name(adf):
    for folder, items in adf.items():
        for stem, doc in items.items():
            assert doc["name"] == stem, f"{folder}/{stem}.json has name {doc['name']}"


def test_all_references_resolve(adf):
    for folder, items in adf.items():
        for name, doc in items.items():
            for node in _walk(doc):
                ref_type = node.get("type")
                if "referenceName" in node:
                    target_folder = next(f for f, t in KINDS.items() if t == ref_type)
                    assert node["referenceName"] in adf[target_folder], f"{folder}/{name}: missing {ref_type} {node['referenceName']}"


def test_activity_references_exist_within_pipeline(adf):
    for name, doc in adf["pipeline"].items():
        acts = list(_activities(doc["properties"]["activities"]))
        names = {a["name"] for a in acts}
        assert len(names) == len(acts), f"{name}: duplicate activity names"
        text = json.dumps(doc)
        for ref in set(re.findall(r"activity\('([^']+)'\)", text)):
            assert ref in names, f"{name}: expression references unknown activity '{ref}'"
        for a in acts:
            for d in a.get("dependsOn", []):
                assert d["activity"] in names, f"{name}/{a['name']} depends on unknown {d['activity']}"


def test_no_nested_control_activities(adf):
    """ADF rejects If/Switch/ForEach/Until nested inside If/Switch."""
    containers = {"IfCondition", "Switch", "ForEach", "Until"}
    for name, doc in adf["pipeline"].items():
        for a in _activities(doc["properties"]["activities"]):
            if a["type"] in ("IfCondition", "Switch"):
                tp = a["typeProperties"]
                inner = tp.get("ifTrueActivities", []) + tp.get("ifFalseActivities", []) + tp.get("defaultActivities", [])
                for c in tp.get("cases", []):
                    inner += c["activities"]
                assert not [i for i in inner if i["type"] in containers], f"{name}/{a['name']} nests a control activity"


def test_router_covers_every_source_type_in_control_table(adf, repo_root):
    seed = (repo_root / "sql" / "control_db" / "03_seed_ingestion_control.sql").read_text()
    types = set(re.findall(r"'(SQLSERVER_CDC|ORACLE_WATERMARK|ORACLE_FULL|REST_API|ADLS_FILE)'", seed))
    router = adf["pipeline"]["PL_01_Ingest_Entity_Router"]["properties"]["activities"][0]["typeProperties"]
    assert types <= {c["value"] for c in router["cases"]}


def test_no_inline_secrets(adf):
    text = json.dumps(adf).lower()
    for bad in ("accountkey=", "password=", "sharedaccesssignature", "client_secret="):
        assert bad not in text.replace("&client_secret=', activity", ""), bad
    for ls in adf["linkedService"].values():
        pw = ls["properties"]["typeProperties"].get("password")
        if pw:
            assert pw["type"] == "AzureKeyVaultSecret"


def test_watermark_only_advanced_after_validation(adf):
    """The golden rule, checked structurally for every ingestion pipeline."""
    for name in ("PL_10_Ingest_SqlServer_CDC", "PL_11_Ingest_Oracle", "PL_12_Ingest_RestApi", "PL_13_Ingest_ADLS_Files"):
        acts = {a["name"]: a for a in _activities(adf["pipeline"][name]["properties"]["activities"])}
        chain, current = [], "Complete_Run_Advance_Watermark"
        while acts[current].get("dependsOn"):
            current = acts[current]["dependsOn"][0]["activity"]
            chain.append(current)
        assert "Validate_Counts" in chain and "Copy_To_Staging" in chain, f"{name}: watermark not gated by validation ({chain})"
