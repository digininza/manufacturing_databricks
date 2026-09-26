"""
CLI: write the synthetic sources to local disk in the SAME folder layout ADF
produces in the ADLS `raw` container, so you can open and inspect every file.

    python -m src.datagen.generate_synthetic_data --out-dir sample_data

Locally files are written as CSV/JSON (human-readable). On Databricks,
notebooks/00_setup/01_simulate_adf_landing.py calls the same generator and
writes real parquet to ADLS, exactly as the ADF Copy activity would.

Output layout:
  raw/<source>/<entity>/load_date=YYYY-MM-DD/run_id=<id>/<run_id>.csv
  raw/_manifests/<entity>/<run_id>.json          (ADF's count manifest)
  source_extracts/cmms_api_pages_<date>.json      (raw API pages, pre-flattening)
  source_extracts/supplier_landing/<file>.csv     (files as suppliers drop them)
  iot/eventhub_messages_<date>.jsonl              (Event Hub message bodies)
  cdc_explained/                                  (annotated CDC examples, see docs/CDC_EXPLAINED.md)
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Dict, List

from src.datagen.generator import ManufacturingDataGenerator

RAW_PATHS = {
    "mes_machine": "sqlserver_mes/machine",
    "mes_production_log": "sqlserver_mes/production_log",
    "mes_quality_inspection": "sqlserver_mes/quality_inspection",
    "erp_product": "oracle_erp/product",
    "erp_plant": "oracle_erp/plant",
    "erp_production_order": "oracle_erp/production_order",
    "cmms_work_order": "cmms_api/work_order",
    "supplier_delivery": "supplier_files/delivery",
}


def _write_csv(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns: List[str] = []
    for r in rows:
        for k in r:
            if k not in columns:
                columns.append(k)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))


def write_cdc_explainer(out: Path, gen: ManufacturingDataGenerator, batches) -> None:
    """Annotated view of the day-2 production_log CDC batch, one file per CDC operation,
    plus the 'all update old' variant that also returns BEFORE images (op 3)."""
    day2 = batches["mes_production_log"][1].rows
    by_op = {1: "1_delete", 2: "2_insert", 4: "4_update_after_image"}
    for op, label in by_op.items():
        _write_csv(out / "cdc_explained" / f"production_log_op{label}.csv", [r for r in day2 if r["cdc_operation"] == op])

    # 'all update old' row filter: for each update SQL Server returns TWO rows sharing the
    # same __$start_lsn/__$seqval: op 3 (values BEFORE) then op 4 (values AFTER).
    day1_by_key = {r["production_log_id"]: r for r in batches["mes_production_log"][0].rows}
    pairs = []
    for r in day2:
        if r["cdc_operation"] == 4 and r["production_log_id"] in day1_by_key:
            before = {k: v for k, v in day1_by_key[r["production_log_id"]].items() if not k.startswith("cdc_")}
            pairs.append({**{k: r[k] for k in r if k.startswith("cdc_")}, "cdc_operation": 3, **before})
            pairs.append(r)
            day1_by_key[r["production_log_id"]] = {k: v for k, v in r.items() if not k.startswith("cdc_")}
    _write_csv(out / "cdc_explained" / "production_log_all_update_old_before_after_pairs.csv", pairs)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="sample_data")
    ap.add_argument("--day1", default="2026-09-24", help="initial-load business date; day 2 = day1 + 1")
    ap.add_argument("--sensor-interval-minutes", type=int, default=60)
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    gen = ManufacturingDataGenerator(day1=date.fromisoformat(args.day1), sensor_interval_minutes=args.sensor_interval_minutes)
    batches = gen.generate_all()

    for entity, entity_batches in batches.items():
        for b in entity_batches:
            folder = out / "raw" / RAW_PATHS[entity] / f"load_date={b.load_date}" / f"run_id={b.run_id}"
            _write_csv(folder / f"{b.run_id}.csv", b.rows)
            _write_json(out / "raw" / "_manifests" / entity / f"{b.run_id}.json", b.manifest())

    for b in batches["cmms_work_order"]:
        _write_json(out / "source_extracts" / f"cmms_api_pages_{b.load_date}.json", gen.api_pages(b.rows))
    for b in batches["supplier_delivery"]:
        by_supplier: Dict[str, List[Dict]] = {}
        for r in b.rows:
            by_supplier.setdefault(r["supplier_id"], []).append(r)
        for sup, rows in by_supplier.items():
            _write_csv(out / "source_extracts" / "supplier_landing" / f"{sup}_deliveries_{b.load_date.replace('-', '')}.csv", rows)

    for d in (gen.day1, gen.day2):
        events = gen.iot_events(d)
        path = out / "iot" / f"eventhub_messages_{d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    write_cdc_explainer(out, gen, batches)
    total = sum(len(b.rows) for bs in batches.values() for b in bs)
    print(f"wrote {total} source rows for {len(batches)} entities + IoT events to {out.resolve()}")


if __name__ == "__main__":
    main()
