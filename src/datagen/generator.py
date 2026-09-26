"""
Synthetic data generator for NorthForge Industries (fictional manufacturer).

Pure Python (no Spark, no Java) so it runs anywhere. It produces exactly what
each source would hand to ADF, for TWO business days:

  day 1 = initial load   (CDC: all inserts, Oracle: all rows, API: all work orders)
  day 2 = incremental    (CDC: inserts + updates + deletes, SCD2-triggering
                          attribute changes, re-sent supplier file, late events)

Each extracted batch is returned as a `LandedBatch` = the rows ADF's Copy
activity would write to raw/<source>/<entity>/load_date=.../run_id=.../ plus
the manifest ADF writes after count validation.

Dirty data is injected on purpose so the silver layer has something to fix:
lower-case / padded keys, NULLs, negative quantities, scrap > produced, the
same CDC change landing twice (ADF retry), a supplier re-sending yesterday's
file, and out-of-order / duplicate IoT events.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List

SEED = 20260924

PLANTS = [
    ("P01", "NorthForge Pune Assembly", "pune", "IN", "Asia/Kolkata"),
    ("P02", "NorthForge Monterrey Stamping", "monterrey", "MX", "America/Monterrey"),
    ("P03", "NorthForge Brno Machining", "brno", "CZ", "Europe/Prague"),
]
MACHINE_TYPES = [("CNC_MILL", "Haas"), ("STAMPING_PRESS", "Schuler"), ("INJECTION_MOLDER", "Engel"), ("ROBOT_WELDER", "Fanuc")]
PRODUCTS = [
    ("PRD-1001", "SKU-GBX-01", "Gearbox Housing", "powertrain", 42.50, 45),
    ("PRD-1002", "SKU-BRK-02", "Brake Caliper Bracket", "chassis", 18.20, 30),
    ("PRD-1003", "SKU-PMP-03", "Pump Impeller", "fluid_systems", 12.75, 20),
    ("PRD-1004", "SKU-SHF-04", "Drive Shaft Flange", "powertrain", 27.00, 40),
    ("PRD-1005", "SKU-MNT-05", "Engine Mount", "chassis", 9.40, 15),
    ("PRD-1006", "SKU-VLV-06", "Valve Body", "fluid_systems", 55.10, 60),
    ("PRD-1007", "SKU-BRC-07", "Battery Tray Bracket", "ev_components", 14.30, 25),
    ("PRD-1008", "SKU-HSG-08", "Motor Housing", "ev_components", 61.90, 55),
]
SHIFTS = [("A", 6), ("B", 14), ("C", 22)]  # shift code, start hour (UTC); production day = shift START date
DEFECT_CODES = ["SCRATCH", "DIMENSION_OOT", "POROSITY", "BURR", "CRACK", "WELD_SPATTER"]
SUPPLIERS = [("SUP-01", "acme metals ltd"), ("SUP-02", "precision alloys gmbh"), ("SUP-03", "polymer works sa")]
MATERIALS = ["AL-6061-BAR", "STL-1018-COIL", "PA66-GF30-RESIN", "CU-C110-SHEET"]
SENSORS = [
    ("temperature", "C", 65.0, 6.0, 95.0),
    ("vibration", "mm/s", 3.5, 0.8, 9.0),
    ("pressure", "bar", 120.0, 8.0, 160.0),
    ("spindle_speed", "rpm", 8000, 400, 11000),
]


@dataclass
class LandedBatch:
    entity: str
    source_system: str
    load_pattern: str
    run_id: str
    load_date: str
    window_start: str
    window_end: str
    rows: List[Dict] = field(default_factory=list)

    def manifest(self, adf_pipeline_run_id: str = None, rows_copied: int = None) -> Dict:
        """What ADF writes to raw/_manifests/<entity>/<run_id>.json after its own count validation."""
        n = len(self.rows)
        return {
            "run_id": self.run_id,
            "entity_name": self.entity,
            "source_system": self.source_system,
            "load_pattern": self.load_pattern,
            "load_date": self.load_date,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "source_count": n,
            "rows_read": n,
            "rows_copied": n if rows_copied is None else rows_copied,
            "rows_skipped": 0,
            "status": "VALIDATED",
            "adf_pipeline_run_id": adf_pipeline_run_id or f"adf-{self.run_id}",
        }


class LsnSequencer:
    """Mimics SQL Server log sequence numbers: 10-byte values rendered as '0x' + 20 hex chars.
    Fixed width => lexicographic order == numeric order, so strings sort correctly in Spark."""

    def __init__(self, start: int = 0x0000002A000001F80001):
        self.value = start

    def next_lsn(self) -> str:
        self.value += random.randint(16, 512)
        return "0x%020X" % self.value

    @staticmethod
    def seqval(i: int) -> str:
        return "0x%020X" % i


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _run_id(entity: str, d: date) -> str:
    return f"{entity}_{d:%Y%m%d}_01"


class ManufacturingDataGenerator:
    def __init__(self, day1: date = date(2026, 9, 24), seed: int = SEED, sensor_interval_minutes: int = 60):
        random.seed(seed)
        self.day1 = day1
        self.day2 = day1 + timedelta(days=1)
        self.lsn = LsnSequencer()
        self.sensor_interval = sensor_interval_minutes
        self.machines = self._build_machines()
        self._prod_log_id = 100000
        self._inspection_id = 500000
        self._order_seq = 1000
        self._wo_seq = 50000

    # ------------------------------------------------------------------ masters
    def _build_machines(self) -> List[Dict]:
        machines = []
        for plant_id, *_ in PLANTS:
            for i in range(1, 5):
                mtype, maker = MACHINE_TYPES[(i - 1) % len(MACHINE_TYPES)]
                machines.append(
                    {
                        "machine_id": f"M-{plant_id}-{i:02d}",
                        "machine_name": f"{plant_id} {mtype.replace('_', ' ').title()} {i:02d}",
                        "plant_id": plant_id,
                        "line_id": f"{plant_id}-L{1 + (i - 1) // 2}",
                        "machine_type": mtype,
                        "manufacturer": maker.lower(),  # dirty: silver initcaps it
                        "install_date": str(date(2018 + i, i, 10 + i)),
                        "status": "ACTIVE",
                        "rated_units_per_hour": random.choice([60, 90, 120, 150, 180]),
                        "modified_at": _ts(datetime.combine(self.day1, datetime.min.time()) + timedelta(hours=1)),
                    }
                )
        return machines

    def _cdc_row(self, op: int, row: Dict, commit_ts: datetime, seq: int, lsn: str = None) -> Dict:
        """Shape of one row returned by cdc.fn_cdc_get_all_changes_<capture_instance>(...) after
        ADF's CONVERT()s (binary LSNs rendered as hex strings — parquet/Spark friendly)."""
        return {
            "cdc_start_lsn": lsn or self.lsn.next_lsn(),
            "cdc_seqval": LsnSequencer.seqval(seq),
            "cdc_operation": op,  # 1=delete 2=insert 3=update(before image) 4=update(after image)
            "cdc_update_mask": "0x" + "FF" if op in (2, 1) else "0x" + "%02X" % random.randint(1, 254),
            "cdc_commit_ts": _ts(commit_ts),
            **row,
        }

    # ------------------------------------------------------------------ SQL Server MES (CDC)
    def mes_machine(self) -> List[LandedBatch]:
        t1 = datetime.combine(self.day1, datetime.min.time()) + timedelta(hours=1)
        d1 = [self._cdc_row(2, m, t1, i) for i, m in enumerate(self.machines)]

        t2 = datetime.combine(self.day2, datetime.min.time()) + timedelta(hours=2)
        changes = []
        moved = dict(self.machines[2], line_id="P01-L3", modified_at=_ts(t2))  # SCD2 trigger: moved to another line
        changes.append(self._cdc_row(4, moved, t2, 1))
        maint = dict(self.machines[7], status="MAINTENANCE", modified_at=_ts(t2))  # SCD2 trigger: status change
        changes.append(self._cdc_row(4, maint, t2, 2))
        new_machine = dict(self.machines[11], machine_id="M-P03-05", machine_name="P03 Cnc Mill 05", line_id="P03-L3", modified_at=_ts(t2))
        new_machine["manufacturer"] = "  HAAS "
        changes.append(self._cdc_row(2, new_machine, t2, 3))
        self.machines.append(dict(new_machine, manufacturer="haas"))
        return [
            LandedBatch("mes_machine", "SQLSERVER_MES", "SQLSERVER_CDC", _run_id("mes_machine", self.day1), str(self.day1), "", d1[-1]["cdc_start_lsn"], d1),
            LandedBatch(
                "mes_machine",
                "SQLSERVER_MES",
                "SQLSERVER_CDC",
                _run_id("mes_machine", self.day2),
                str(self.day2),
                d1[-1]["cdc_start_lsn"],
                changes[-1]["cdc_start_lsn"],
                changes,
            ),
        ]

    def _production_rows_for_day(self, d: date) -> List[Dict]:
        rows = []
        for m in self.machines[:12]:
            for shift, start_hour in SHIFTS:
                product = random.choice(PRODUCTS)
                start = datetime.combine(d, datetime.min.time()) + timedelta(hours=start_hour, minutes=random.randint(0, 40))
                run_min = random.randint(300, 440)
                ideal_units = int(run_min * 60 / product[5])
                produced = int(ideal_units * random.uniform(0.72, 0.97))
                scrapped = int(produced * random.uniform(0.0, 0.06))
                self._prod_log_id += 1
                rows.append(
                    {
                        "production_log_id": self._prod_log_id,
                        "order_id": f"PO-{d:%Y%m%d}-{m['plant_id']}-{product[0][-4:]}",
                        "machine_id": m["machine_id"],
                        "product_id": product[0],
                        "shift_code": shift,
                        "start_ts": _ts(start),
                        "end_ts": _ts(start + timedelta(minutes=run_min)),
                        "units_produced": produced,
                        "units_scrapped": scrapped,
                        "operator_id": f"OP-{random.randint(100, 160)}",
                        "modified_at": _ts(start + timedelta(minutes=run_min, seconds=30)),
                    }
                )
        return rows

    def mes_production_log_and_inspection(self):
        # ---- day 1: inserts (with some dirty rows)
        d1_rows = self._production_rows_for_day(self.day1)
        d1_rows[3]["machine_id"] = " " + d1_rows[3]["machine_id"].lower() + " "  # standardization case
        d1_rows[5]["shift_code"] = None  # null handling -> 'UNKNOWN' + WARN
        d1_rows[8]["units_scrapped"] = d1_rows[8]["units_produced"] + 5  # ERROR -> quarantine
        d1_rows[9]["units_produced"] = -12  # ERROR -> quarantine
        prod_d1 = [self._cdc_row(2, r, datetime.strptime(r["modified_at"], "%Y-%m-%d %H:%M:%S"), i) for i, r in enumerate(d1_rows)]
        prod_d1.append(dict(prod_d1[10]))  # SAME change landed twice (ADF retry) -> dedup

        # ---- day 2: new inserts + corrections (op 4) + deletions (op 1)
        d2_rows = self._production_rows_for_day(self.day2)
        prod_d2 = [self._cdc_row(2, r, datetime.strptime(r["modified_at"], "%Y-%m-%d %H:%M:%S"), i) for i, r in enumerate(d2_rows)]
        corr_ts = datetime.combine(self.day2, datetime.min.time()) + timedelta(hours=9)
        corrected = dict(d1_rows[0], units_produced=d1_rows[0]["units_produced"] + 25, modified_at=_ts(corr_ts))
        prod_d2.append(self._cdc_row(4, corrected, corr_ts, 900))
        corrected_again = dict(corrected, units_scrapped=corrected["units_scrapped"] + 3, modified_at=_ts(corr_ts + timedelta(minutes=5)))
        prod_d2.append(self._cdc_row(4, corrected_again, corr_ts + timedelta(minutes=5), 901))  # 2 updates same key: last wins
        fixed = dict(d1_rows[8], units_scrapped=4, modified_at=_ts(corr_ts))  # the quarantined row gets fixed at source
        prod_d2.append(self._cdc_row(4, fixed, corr_ts, 902))
        for idx in (1, 2):  # operator entered a run twice -> deleted at source
            prod_d2.append(self._cdc_row(1, d1_rows[idx], corr_ts + timedelta(minutes=10), 903 + idx))

        # ---- inspections: one per production run
        insp_d1 = [self._cdc_row(2, self._inspection(r), datetime.strptime(r["modified_at"], "%Y-%m-%d %H:%M:%S"), i) for i, r in enumerate(d1_rows)]
        insp_d1[6]["defect_count"] = insp_d1[6]["sample_size"] + 3  # ERROR -> quarantine
        insp_d2 = [self._cdc_row(2, self._inspection(r), datetime.strptime(r["modified_at"], "%Y-%m-%d %H:%M:%S"), i) for i, r in enumerate(d2_rows)]
        reinspected = dict({k: v for k, v in insp_d1[4].items() if not k.startswith("cdc_")}, inspection_result="PASS", defect_count=0, defect_code=None)
        reinspected["modified_at"] = _ts(corr_ts)
        insp_d2.append(self._cdc_row(4, reinspected, corr_ts, 950))

        def batch(entity, day, start, rows):
            return LandedBatch(entity, "SQLSERVER_MES", "SQLSERVER_CDC", _run_id(entity, day), str(day), start, rows[-1]["cdc_start_lsn"], rows)

        prod = [batch("mes_production_log", self.day1, "", prod_d1), batch("mes_production_log", self.day2, prod_d1[-1]["cdc_start_lsn"], prod_d2)]
        insp = [
            batch("mes_quality_inspection", self.day1, "", insp_d1),
            batch("mes_quality_inspection", self.day2, insp_d1[-1]["cdc_start_lsn"], insp_d2),
        ]
        return prod, insp

    def _inspection(self, prod_row: Dict) -> Dict:
        self._inspection_id += 1
        sample = random.choice([20, 25, 50])
        defects = random.choices([0, 0, 0, 1, 2, 4], k=1)[0]
        return {
            "inspection_id": self._inspection_id,
            "production_log_id": prod_row["production_log_id"],
            "machine_id": prod_row["machine_id"],
            "product_id": prod_row["product_id"],
            "inspection_ts": prod_row["end_ts"],
            "sample_size": sample,
            "defect_count": defects,
            "defect_code": random.choice(DEFECT_CODES) if defects else None,
            "inspection_result": "FAIL" if defects >= 2 else "PASS",
            "inspector_id": f"QI-{random.randint(10, 30)}",
            "modified_at": prod_row["modified_at"],
        }

    # ------------------------------------------------------------------ Oracle ERP (watermark on LAST_UPDATE_DATE)
    def erp_product(self) -> List[LandedBatch]:
        base = datetime.combine(self.day1 - timedelta(days=30), datetime.min.time())
        d1 = []
        for i, (pid, sku, name, fam, cost, cycle) in enumerate(PRODUCTS):
            d1.append(
                {
                    "PRODUCT_ID": pid,
                    "SKU": sku,
                    "PRODUCT_NAME": name if i != 6 else None,  # WARN: missing name
                    "PRODUCT_FAMILY": fam,  # lower-case -> silver upper-cases
                    "UNIT_COST": cost,
                    "STD_CYCLE_TIME_SEC": cycle,
                    "LAST_UPDATE_DATE": _ts(base + timedelta(hours=i)),
                }
            )
        t2 = datetime.combine(self.day2, datetime.min.time()) + timedelta(hours=3)
        d2 = [
            dict(d1[0], UNIT_COST=44.90, LAST_UPDATE_DATE=_ts(t2)),  # SCD2: cost revision
            dict(d1[5], STD_CYCLE_TIME_SEC=52, LAST_UPDATE_DATE=_ts(t2 + timedelta(minutes=1))),  # SCD2: new cycle time after tooling change
            dict(d1[6], PRODUCT_NAME="Battery Tray Bracket", LAST_UPDATE_DATE=_ts(t2 + timedelta(minutes=2))),
            {
                "PRODUCT_ID": "PRD-1009",
                "SKU": "SKU-INV-09",
                "PRODUCT_NAME": "Inverter Cover",
                "PRODUCT_FAMILY": "ev_components",
                "UNIT_COST": 21.30,
                "STD_CYCLE_TIME_SEC": 35,
                "LAST_UPDATE_DATE": _ts(t2 + timedelta(minutes=3)),
            },
        ]
        return [
            LandedBatch(
                "erp_product",
                "ORACLE_ERP",
                "ORACLE_WATERMARK",
                _run_id("erp_product", self.day1),
                str(self.day1),
                "1900-01-01 00:00:00",
                d1[-1]["LAST_UPDATE_DATE"],
                d1,
            ),
            LandedBatch(
                "erp_product",
                "ORACLE_ERP",
                "ORACLE_WATERMARK",
                _run_id("erp_product", self.day2),
                str(self.day2),
                d1[-1]["LAST_UPDATE_DATE"],
                d2[-1]["LAST_UPDATE_DATE"],
                d2,
            ),
        ]

    def erp_plant(self) -> List[LandedBatch]:
        ts = _ts(datetime.combine(self.day1 - timedelta(days=90), datetime.min.time()))
        rows = [{"PLANT_ID": p, "PLANT_NAME": n, "CITY": c, "COUNTRY_CODE": cc, "TIMEZONE": tz, "LAST_UPDATE_DATE": ts} for p, n, c, cc, tz in PLANTS]
        d2 = [dict(r) for r in rows]
        d2[1]["PLANT_NAME"] = "NorthForge Monterrey Stamping & Welding"
        d2[1]["LAST_UPDATE_DATE"] = _ts(datetime.combine(self.day2, datetime.min.time()))
        return [
            LandedBatch("erp_plant", "ORACLE_ERP", "ORACLE_FULL", _run_id("erp_plant", self.day1), str(self.day1), "", "", rows),
            LandedBatch("erp_plant", "ORACLE_ERP", "ORACLE_FULL", _run_id("erp_plant", self.day2), str(self.day2), "", "", d2),
        ]

    def erp_production_order(self) -> List[LandedBatch]:
        def orders_for(d: date, status: str) -> List[Dict]:
            out = []
            for plant_id, *_ in PLANTS:
                for pid, *_rest in PRODUCTS:
                    out.append(
                        {
                            "ORDER_ID": f"PO-{d:%Y%m%d}-{plant_id}-{pid[-4:]}",
                            "PRODUCT_ID": pid,
                            "PLANT_ID": plant_id,
                            "PLANNED_QTY": random.randint(400, 2400),
                            "PLANNED_START": _ts(datetime.combine(d, datetime.min.time()) + timedelta(hours=6)),
                            "PLANNED_END": _ts(datetime.combine(d, datetime.min.time()) + timedelta(hours=30)),
                            "ORDER_STATUS": status,
                            "LAST_UPDATE_DATE": _ts(datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=18, seconds=len(out))),
                        }
                    )
            return out

        d1 = orders_for(self.day1, "RELEASED")
        t2 = datetime.combine(self.day2, datetime.min.time()) + timedelta(hours=1)
        closed = [dict(o, ORDER_STATUS="COMPLETED", LAST_UPDATE_DATE=_ts(t2 + timedelta(seconds=i))) for i, o in enumerate(d1)]
        d2 = orders_for(self.day2, "RELEASED") + closed
        d2.sort(key=lambda r: r["LAST_UPDATE_DATE"])
        return [
            LandedBatch(
                "erp_production_order",
                "ORACLE_ERP",
                "ORACLE_WATERMARK",
                _run_id("erp_production_order", self.day1),
                str(self.day1),
                "1900-01-01 00:00:00",
                max(r["LAST_UPDATE_DATE"] for r in d1),
                d1,
            ),
            LandedBatch(
                "erp_production_order",
                "ORACLE_ERP",
                "ORACLE_WATERMARK",
                _run_id("erp_production_order", self.day2),
                str(self.day2),
                max(r["LAST_UPDATE_DATE"] for r in d1),
                d2[-1]["LAST_UPDATE_DATE"],
                d2,
            ),
        ]

    # ------------------------------------------------------------------ CMMS REST API
    def cmms_work_order(self) -> List[LandedBatch]:
        def wo(machine, d, wtype, status, hour, downtime):
            self._wo_seq += 1
            reported = datetime.combine(d, datetime.min.time()) + timedelta(hours=hour)
            return {
                "work_order_id": f"WO-{self._wo_seq}",
                "machine_id": machine["machine_id"],
                "work_type": wtype,
                "priority": {"BREAKDOWN": "P1", "CORRECTIVE": "P2", "PREVENTIVE": "P3"}[wtype],
                "status": status,
                "reported_at": _ts(reported),
                "started_at": _ts(reported + timedelta(minutes=15)),
                "completed_at": _ts(reported + timedelta(minutes=15 + downtime)) if status == "COMPLETED" else None,
                "downtime_minutes": downtime if status == "COMPLETED" else None,
                "technician": random.choice(["r. sharma", "l. garcia", "p. novak", "a. kumar"]),
                "updated_at": _ts(reported + timedelta(minutes=16 + downtime)),
            }

        d1 = [
            wo(self.machines[0], self.day1, "PREVENTIVE", "COMPLETED", 5, 45),
            wo(self.machines[4], self.day1, "BREAKDOWN", "COMPLETED", 11, 95),
            wo(self.machines[7], self.day1, "CORRECTIVE", "IN_PROGRESS", 20, 0),
            wo(self.machines[9], self.day1, "BREAKDOWN", "COMPLETED", 15, 130),
        ]
        t2 = datetime.combine(self.day2, datetime.min.time()) + timedelta(hours=2)
        still_open = dict(d1[2], status="COMPLETED", completed_at=_ts(t2), downtime_minutes=210, updated_at=_ts(t2))  # same WO, now closed
        d2 = [still_open, wo(self.machines[2], self.day2, "BREAKDOWN", "COMPLETED", 9, 60), wo(self.machines[10], self.day2, "PREVENTIVE", "COMPLETED", 6, 30)]
        return [
            LandedBatch(
                "cmms_work_order",
                "CMMS_API",
                "REST_API",
                _run_id("cmms_work_order", self.day1),
                str(self.day1),
                "1900-01-01T00:00:00Z",
                max(r["updated_at"] for r in d1),
                d1,
            ),
            LandedBatch(
                "cmms_work_order",
                "CMMS_API",
                "REST_API",
                _run_id("cmms_work_order", self.day2),
                str(self.day2),
                max(r["updated_at"] for r in d1),
                max(r["updated_at"] for r in d2),
                d2,
            ),
        ]

    @staticmethod
    def api_pages(rows: List[Dict], page_size: int = 2) -> List[Dict]:
        """What the CMMS API returns page by page — ADF's REST connector walks these."""
        pages = []
        for i in range(0, len(rows), page_size):
            pages.append({"meta": {"page": i // page_size + 1, "page_size": page_size, "total_count": len(rows)}, "data": rows[i : i + page_size]})
        pages.append({"meta": {"page": len(pages) + 1, "page_size": page_size, "total_count": len(rows)}, "data": []})  # end condition
        return pages

    # ------------------------------------------------------------------ Supplier CSV files on ADLS
    def supplier_delivery(self) -> List[LandedBatch]:
        def deliveries(d: date, n: int) -> List[Dict]:
            out = []
            for i in range(n):
                sup_id, sup_name = SUPPLIERS[i % len(SUPPLIERS)]
                qty = random.randint(200, 2000)
                out.append(
                    {
                        "delivery_id": f"DLV-{d:%Y%m%d}-{i + 1:03d}",
                        "supplier_id": sup_id,
                        "supplier_name": sup_name,
                        "material_code": random.choice(MATERIALS).lower(),
                        "plant_id": random.choice(PLANTS)[0].lower(),
                        "delivery_date": str(d),
                        "delivered_qty": qty,
                        "rejected_qty": int(qty * random.choice([0, 0, 0.01, 0.03])),
                        "lot_number": f"lot-{random.randint(10000, 99999)}",
                    }
                )
            return out

        d1 = deliveries(self.day1, 6)
        d2 = deliveries(self.day2, 5)
        d2.append(dict(d1[0]))  # supplier re-sent yesterday's line -> dedup on delivery_id
        d2[1]["rejected_qty"] = d2[1]["delivered_qty"] + 10  # ERROR -> quarantine
        return [
            LandedBatch("supplier_delivery", "SUPPLIER_FILES", "ADLS_FILE", _run_id("supplier_delivery", self.day1), str(self.day1), "", "", d1),
            LandedBatch("supplier_delivery", "SUPPLIER_FILES", "ADLS_FILE", _run_id("supplier_delivery", self.day2), str(self.day2), "", "", d2),
        ]

    # ------------------------------------------------------------------ IoT sensor events (Event Hub message bodies)
    def iot_events(self, d: date) -> List[Dict]:
        events = []
        start = datetime.combine(d, datetime.min.time())
        steps = int(24 * 60 / self.sensor_interval)
        for m in self.machines[:12]:
            for step in range(steps):
                ts = start + timedelta(minutes=step * self.sensor_interval, seconds=random.randint(0, 20))
                for sensor, unit, mean, sd, alarm in SENSORS:
                    value = random.gauss(mean, sd)
                    if m["machine_id"] == "M-P02-01" and 13 <= ts.hour <= 15 and sensor in ("temperature", "vibration"):
                        value = alarm * random.uniform(1.02, 1.15)  # anomaly episode before the breakdown work order
                    events.append(
                        {
                            "device_id": f"GW-{m['machine_id']}-{sensor[:4].upper()}",
                            "machine_id": m["machine_id"],
                            "sensor_type": sensor,
                            "reading_value": round(value, 3),
                            "unit": unit,
                            "event_ts": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            "firmware": "3.4.1",
                        }
                    )
        events.append(dict(events[10]))  # device retransmit -> duplicate
        late = dict(events[20])
        events.append(late)  # arrives late (sent again at the end of the stream)
        return events

    # ------------------------------------------------------------------ everything
    def generate_all(self) -> Dict[str, List[LandedBatch]]:
        machine = self.mes_machine()
        prod, insp = self.mes_production_log_and_inspection()
        return {
            "mes_machine": machine,
            "mes_production_log": prod,
            "mes_quality_inspection": insp,
            "erp_product": self.erp_product(),
            "erp_plant": self.erp_plant(),
            "erp_production_order": self.erp_production_order(),
            "cmms_work_order": self.cmms_work_order(),
            "supplier_delivery": self.supplier_delivery(),
        }
