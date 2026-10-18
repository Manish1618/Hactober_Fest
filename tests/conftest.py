from pathlib import Path

import pytest

from datamind.config import load_dataset
from datamind.pipeline.validator import Catalog

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def tpch():
    return load_dataset(REPO / "datasets" / "tpch_sf1")


@pytest.fixture(scope="session")
def catalog(tpch):
    return Catalog(tpch.catalog(), tpch.snowflake.database, tpch.snowflake.schema_)
