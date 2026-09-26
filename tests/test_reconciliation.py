import pytest

from src.framework import reconciliation as recon


def test_counts_match():
    assert recon.compare_counts("ADF_TO_BRONZE", "e", 10, 10).status == recon.PASSED


def test_counts_mismatch_fails():
    r = recon.compare_counts("ADF_TO_BRONZE", "e", 10, 9)
    assert r.status == recon.FAILED and r.difference == 1


def test_tolerance():
    assert recon.compare_counts("X", "e", 100, 98, tolerance=2).status == recon.PASSED


def test_amount_mismatch_fails_even_if_counts_match():
    r = recon.compare_counts_and_amounts("SILVER_TO_GOLD", "f", 5, 5, 1000.0, 999.0, amount_tolerance_pct=0.0001)
    assert r.status == recon.FAILED


def test_amount_within_tolerance():
    r = recon.compare_counts_and_amounts("GOLD_TO_SNOWFLAKE", "f", 5, 5, 1_000_000.0, 1_000_000.05, amount_tolerance_pct=0.0001)
    assert r.status == recon.PASSED


def test_silver_balance_every_row_accounted_for():
    assert recon.check_silver_balance("e", 100, 90, 7, 3).status == recon.PASSED
    assert recon.check_silver_balance("e", 100, 90, 7, 2).status == recon.FAILED


def test_assert_all_passed_raises_with_detail():
    with pytest.raises(recon.ReconciliationError, match="watermark NOT advanced"):
        recon.assert_all_passed([recon.compare_counts("ADF_TO_BRONZE", "erp_product", 12, 7)])
