"""
Test layout:
  * pure-Python tests (default): config/registry contract, synthetic data + CDC semantics,
    reconciliation rules, MERGE SQL builders, ADF artifact integrity, Power BI model integrity.
    No Spark, no Java.
  * Spark tests (test_spark_*.py): run only when pyspark + a JDK are available.
"""

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def spark_available() -> bool:
    try:
        import pyspark  # noqa: F401
    except ImportError:
        return False
    return shutil.which("java") is not None and _java_runs()


def _java_runs() -> bool:
    import subprocess

    try:
        return subprocess.run(["java", "-version"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


requires_spark = pytest.mark.skipif(not spark_available(), reason="pyspark + JDK not available")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def generated():
    from src.datagen.generator import ManufacturingDataGenerator

    gen = ManufacturingDataGenerator()
    return gen, gen.generate_all()
