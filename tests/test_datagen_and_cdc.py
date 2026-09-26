"""Synthetic data behaves like the real sources, and CDC semantics are what silver expects."""

from typing import Dict, List


def apply_cdc_reference(batches) -> Dict[int, Dict]:
    """Pure-Python reference of silver's CDC rule: latest (lsn, seqval) per key wins; op 1 => deleted."""
    state: Dict[int, Dict] = {}
    for b in batches:
        rows: List[Dict] = sorted(b.rows, key=lambda r: (r["cdc_start_lsn"], r["cdc_seqval"]))
        for r in rows:
            key = r["production_log_id"]
            seq = (r["cdc_start_lsn"], r["cdc_seqval"])
            if key in state and state[key]["_seq"] >= seq:
                continue  # replay / out of order -> ignored
            state[key] = dict(r, _seq=seq, _is_deleted=r["cdc_operation"] == 1)
    return state


def test_generator_is_deterministic():
    from src.datagen.generator import ManufacturingDataGenerator

    a = ManufacturingDataGenerator().generate_all()
    b = ManufacturingDataGenerator().generate_all()
    assert [len(x.rows) for v in a.values() for x in v] == [len(x.rows) for v in b.values() for x in v]
    assert a["mes_production_log"][1].rows[0] == b["mes_production_log"][1].rows[0]


def test_lsns_are_fixed_width_hex(generated):
    _, batches = generated
    for b in batches["mes_production_log"]:
        for r in b.rows:
            assert r["cdc_start_lsn"].startswith("0x") and len(r["cdc_start_lsn"]) == 22
            assert len(r["cdc_seqval"]) == 22


def test_day2_contains_every_cdc_operation(generated):
    _, batches = generated
    ops = {r["cdc_operation"] for r in batches["mes_production_log"][1].rows}
    assert ops == {1, 2, 4}


def test_cdc_reference_final_state(generated):
    _, batches = generated
    day1 = {r["production_log_id"]: r for r in batches["mes_production_log"][0].rows}
    state = apply_cdc_reference(batches["mes_production_log"])
    # 100001 corrected twice on day 2: units +25, then scrap +3 -> last update wins
    assert state[100001]["units_produced"] == day1[100001]["units_produced"] + 25
    assert state[100001]["units_scrapped"] == day1[100001]["units_scrapped"] + 3
    # 100002 / 100003 deleted at source
    assert state[100002]["_is_deleted"] and state[100003]["_is_deleted"]
    # the duplicated day-1 change does not create a second version
    assert sum(1 for r in batches["mes_production_log"][0].rows if r["production_log_id"] == 100011) == 2
    assert not state[100011]["_is_deleted"]


def test_replaying_a_batch_is_a_noop(generated):
    _, batches = generated
    once = apply_cdc_reference(batches["mes_production_log"])
    twice = apply_cdc_reference(batches["mes_production_log"] + batches["mes_production_log"])
    assert once == twice


def test_manifest_matches_rows(generated):
    _, batches = generated
    for entity_batches in batches.values():
        for b in entity_batches:
            m = b.manifest()
            assert m["source_count"] == m["rows_read"] == m["rows_copied"] == len(b.rows)
            assert m["run_id"].startswith(b.entity)


def test_dirty_data_is_present_for_silver_to_fix(generated):
    _, batches = generated
    d1 = batches["mes_production_log"][0].rows
    assert any(r["machine_id"] != r["machine_id"].strip() for r in d1)  # padded key
    assert any(r["shift_code"] is None for r in d1)  # null handling
    assert any(r["units_produced"] < 0 for r in d1)  # quarantine
    supplier_d2 = {r["delivery_id"] for r in batches["supplier_delivery"][1].rows}
    assert supplier_d2 & {r["delivery_id"] for r in batches["supplier_delivery"][0].rows}  # re-sent line


def test_scd2_triggers_exist(generated):
    _, batches = generated
    d2_machines = {r["machine_id"]: r for r in batches["mes_machine"][1].rows}
    assert d2_machines["M-P01-03"]["line_id"] == "P01-L3"
    assert d2_machines["M-P02-04"]["status"] == "MAINTENANCE"


def test_api_pages_end_with_empty_page(generated):
    gen, batches = generated
    pages = gen.api_pages(batches["cmms_work_order"][0].rows, page_size=2)
    assert pages[-1]["data"] == []
    assert sum(len(p["data"]) for p in pages) == pages[0]["meta"]["total_count"]
