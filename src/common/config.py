"""
Environment + source-registry configuration loader.

`load_config(env)` resolves configs/{env}.yml (no secrets, only secret *names*).
`load_registry()` loads configs/source_registry.yml, the Databricks half of the
metadata-driven framework (the ADF half is the ctl.ingestion_control table).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs"

VALID_LOAD_PATTERNS = {"SQLSERVER_CDC", "ORACLE_WATERMARK", "ORACLE_FULL", "REST_API", "ADLS_FILE"}
VALID_SEVERITIES = {"ERROR", "WARN"}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALID_TRANSFORMS = {"trim", "upper", "lower", "initcap", "empty_to_null"}


@dataclass
class Config:
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def environment(self) -> str:
        return self.raw["environment"]

    @property
    def catalog(self) -> str:
        return self.raw["unity_catalog"]["catalog"]

    def schema(self, layer: str) -> str:
        return self.raw["unity_catalog"]["schemas"][layer]

    def fq(self, layer: str, table: str) -> str:
        """catalog.schema.table in Unity Catalog; schema.table when catalog is empty (local tests)."""
        parts = ([self.catalog] if self.catalog else []) + [self.schema(layer), table]
        # Table names end up inside SQL strings (MERGE / DESCRIBE HISTORY). They come from config,
        # never from users, but we still refuse anything that is not a plain identifier, so a
        # bad config value can never become SQL injection (bandit B608).
        for part in parts:
            if not _IDENTIFIER.match(part):
                raise ValueError(f"invalid SQL identifier in config: {part!r}")
        return ".".join(parts)

    def get(self, dotted_path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def checkpoint(self, *parts: str) -> str:
        return "/".join([self.get("storage.checkpoint_root")] + list(parts))


@dataclass
class EntityConfig:
    name: str
    raw: Dict[str, Any]

    @property
    def load_pattern(self) -> str:
        return self.raw["load_pattern"]

    @property
    def source_system(self) -> str:
        return self.raw["source_system"]

    @property
    def raw_path(self) -> str:
        return self.raw["raw_path"]

    @property
    def silver_table(self) -> str:
        return self.raw["silver_table"]

    @property
    def primary_keys(self) -> List[str]:
        return list(self.raw["primary_keys"])

    @property
    def sequence_by(self) -> List[str]:
        return list(self.raw["sequence_by"])

    @property
    def delete_condition(self) -> str:
        return self.raw.get("delete_condition") or ""

    @property
    def is_cdc(self) -> bool:
        return self.load_pattern == "SQLSERVER_CDC"

    @property
    def columns(self) -> Dict[str, Dict[str, Any]]:
        return self.raw["columns"]

    @property
    def null_defaults(self) -> Dict[str, Any]:
        return self.raw.get("null_defaults") or {}

    @property
    def dq_rules(self) -> List[Dict[str, str]]:
        return list(self.raw.get("dq_rules") or [])


def load_config(env: str = None) -> Config:
    env = env or os.environ.get("NORTHFORGE_ENV") or "dev"
    path = CONFIG_DIR / f"{env}.yml"
    if not path.exists():
        raise FileNotFoundError(f"No config for environment '{env}' at {path}")
    with open(path) as fh:
        return Config(yaml.safe_load(fh))


def load_registry(path: Path = None) -> Dict[str, EntityConfig]:
    with open(path or CONFIG_DIR / "source_registry.yml") as fh:
        doc = yaml.safe_load(fh)
    registry = {name: EntityConfig(name, body) for name, body in doc["entities"].items()}
    for entity in registry.values():
        validate_entity(entity)
    return registry


def validate_entity(entity: EntityConfig) -> None:
    """Fail fast on a malformed registry entry — a developer standard, enforced in CI."""
    problems = []
    if entity.load_pattern not in VALID_LOAD_PATTERNS:
        problems.append(f"unknown load_pattern {entity.load_pattern}")
    target_cols = {spec["name"] for spec in entity.columns.values()}
    for key in entity.primary_keys:
        if key not in target_cols:
            problems.append(f"primary key {key} not in columns")
    for col in entity.sequence_by:
        if col not in target_cols and not col.startswith("cdc_") and not col.startswith("_"):
            problems.append(f"sequence column {col} not in columns")
    if entity.is_cdc and not entity.delete_condition:
        problems.append("CDC entity must define delete_condition")
    for spec in entity.columns.values():
        for t in spec.get("transforms", []):
            if t not in VALID_TRANSFORMS:
                problems.append(f"unknown transform {t}")
    for rule in entity.dq_rules:
        if rule.get("severity") not in VALID_SEVERITIES:
            problems.append(f"rule {rule.get('name')} has invalid severity")
    if problems:
        raise ValueError(f"source_registry entity '{entity.name}' is invalid: {problems}")
