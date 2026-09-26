"""
Config-driven Data Quality engine.

Rules come from configs/source_registry.yml:
  severity ERROR -> the row is QUARANTINED (written to quarantine.<entity> with
                    the list of failed rules) and never reaches silver.
  severity WARN  -> the row flows to silver; `_dq_warnings` lists failed rules.

A rule expression evaluating to NULL counts as a FAILURE (e.g. "units > 0"
where units is NULL). That is deliberate: unknown is not "passed".
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


def rule_pass_expr(rule: Dict[str, str]) -> str:
    """SQL that is TRUE only when the rule definitely passes (NULL -> failed)."""
    return f"coalesce(({rule['expr']}), false)"


def apply_dq(df: DataFrame, rules: List[Dict[str, str]]) -> Tuple[DataFrame, DataFrame, Dict[str, int]]:
    errors = [r for r in rules if r["severity"] == "ERROR"]
    warns = [r for r in rules if r["severity"] == "WARN"]

    def failed_names(rule_list):
        if not rule_list:
            return F.array().cast("array<string>")
        return F.filter(
            F.array(*[F.when(~F.expr(rule_pass_expr(r)), F.lit(r["name"])) for r in rule_list]),
            lambda x: x.isNotNull(),
        )

    checked = df.withColumn("_dq_errors", failed_names(errors)).withColumn("_dq_warnings", failed_names(warns)).cache()

    valid = checked.filter(F.size("_dq_errors") == 0).drop("_dq_errors")
    quarantined = checked.filter(F.size("_dq_errors") > 0)

    # per-rule failure counts in ONE pass (feeds control.dq_results)
    agg_exprs = [F.sum(F.when(~F.expr(rule_pass_expr(r)), 1).otherwise(0)).alias(r["name"]) for r in rules]
    counts = checked.agg(*agg_exprs).collect()[0].asDict() if rules else {}
    stats = {name: int(v or 0) for name, v in counts.items()}
    return valid, quarantined, stats
