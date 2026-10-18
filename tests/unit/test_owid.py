"""OWID dataset: CSV reshaping in the load script, offline fixture and its routing."""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from datamind.config.loader import load_dataset
from datamind.evaluation.golden import load_set

duckdb = pytest.importorskip("duckdb")

from datamind.snowflake_io.executor import LocalExecutor  # noqa: E402
from datamind.snowflake_io.fixture import build_fixture_for  # noqa: E402

SCRIPT = Path(__file__).parents[2] / "datasets" / "owid_indicators" / "download_and_load.py"


@pytest.fixture(scope="module")
def loader():
    spec = importlib.util.spec_from_file_location("owid_download_and_load", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up while it loads
    spec.loader.exec_module(mod)
    return mod


HEADER = (
    "Entity,Code,Year,Population (historical),Population (future projections),"
    "World region according to OWID\n"
)
CSV = (
    HEADER
    + """Japan,JPN,2022,124000000,,
Japan,JPN,2023,,123500000,Asia
Europe,,2022,744000000,,
World,OWID_WRL,2022,7950000000,,
Kosovo,OWID_KOS,2022,1760000,,Europe
Atlantis,ATL,2022,,,
"""
)


def test_reshape_values_and_projection_flag(loader):
    values, _, name = loader.reshape(CSV, "POPULATION")
    assert name == "Population (historical)"
    assert list(values.columns) == ["COUNTRY_CODE", "YEAR", "VALUE", "IS_PROJECTED"]
    rows = {(r.COUNTRY_CODE, r.YEAR): (r.VALUE, r.IS_PROJECTED) for r in values.itertuples()}
    assert rows[("JPN", 2022)] == (124000000, False)
    assert rows[("JPN", 2023)] == (123500000, True)
    assert not any(code == "ATL" for code, _ in rows)  # no value, no row


def test_reshape_countries_and_aggregates(loader):
    _, countries, _ = loader.reshape(CSV, "POPULATION")
    by_code = {r.COUNTRY_CODE: r for r in countries.itertuples()}
    assert by_code["JPN"].REGION == "Asia" and not by_code["JPN"].IS_AGGREGATE
    assert by_code["OWID_WRL"].IS_AGGREGATE and by_code["OWID_WRL"].REGION is None
    assert not by_code["OWID_KOS"].IS_AGGREGATE  # Kosovo is a country despite its OWID_ code
    europe = loader.aggregate_code("Europe")
    assert len(europe) == 8 and by_code[europe].IS_AGGREGATE
    assert by_code[europe].COUNTRY_NAME == "Europe"


def test_reshape_rejects_a_csv_without_values(loader):
    with pytest.raises(loader.LoadError):
        loader.reshape("Entity,Code,Year\nJapan,JPN,2020\n", "LIFE_EXPECTANCY")


def test_build_tables_fills_country_and_attribution(loader):
    texts = {ind.table: CSV for ind in loader.INDICATORS}
    tables = loader.build_tables(texts, datetime(2026, 10, 1, tzinfo=UTC))
    assert set(tables) == {ind.table for ind in loader.INDICATORS} | {"COUNTRY", "ATTRIBUTION"}
    assert tables["COUNTRY"]["COUNTRY_CODE"].is_unique
    assert set(tables["ATTRIBUTION"]["LICENCE"]) == {"CC BY 4.0"}


def test_fixture_is_picked_by_dataset():
    owid = build_fixture_for(load_dataset("owid_indicators"))
    assert owid.execute("SELECT COUNT(*) FROM COUNTRY WHERE IS_AGGREGATE").fetchone()[0] == 2
    tpch = build_fixture_for(load_dataset("tpch_sf1"))
    assert tpch.execute("SELECT COUNT(*) FROM NATION").fetchone()[0] == 25


def test_owid_ground_truth_runs_on_the_fixture():
    ex = LocalExecutor(build_fixture_for(load_dataset("owid_indicators")))
    for split in ("golden", "heldout"):
        for q in load_set("owid_indicators", split).questions:
            if q.ground_truth_sql:
                assert ex.run(q.ground_truth_sql, request_id=q.id, max_rows=50).rows, q.id


def test_reshape_shortens_overlong_aggregate_codes(loader):
    csv = "Entity,Code,Year,GDP\nEuropean Union (27),OWID_EU27,2010,30000\nIndia,IND,2010,1300\n"
    values, countries, _ = loader.reshape(csv, "GDP_PER_CAPITA")
    codes = set(values["COUNTRY_CODE"])
    assert "IND" in codes and all(len(c) <= loader.CODE_WIDTH for c in codes)
    eu = countries[countries["COUNTRY_NAME"] == "European Union (27)"].iloc[0]
    assert eu["IS_AGGREGATE"] and eu["COUNTRY_CODE"].startswith("AGG")


def test_reshape_drops_years_before_the_common_era(loader):
    csv = "Entity,Code,Year,Population\nIndia,IND,-10000,1000\nIndia,IND,2020,1380000000\n"
    values, _, _ = loader.reshape(csv, "POPULATION")
    assert list(values["YEAR"]) == [2020]
