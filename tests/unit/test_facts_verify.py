import pytest

from datamind.pipeline.answer import explain
from datamind.pipeline.facts import extract_facts
from datamind.pipeline.validator import validate
from datamind.pipeline.verify import missing_facts, untraced_numbers, verify

DATES = {"ORDERS.O_ORDERDATE", "LINEITEM.L_SHIPDATE"}

SQL = (
    "SELECT N_NAME AS nation, SUM(L_EXTENDEDPRICE * (1 - L_DISCOUNT)) AS revenue "
    "FROM LINEITEM JOIN ORDERS ON L_ORDERKEY = O_ORDERKEY "
    "JOIN CUSTOMER ON O_CUSTKEY = C_CUSTKEY JOIN NATION n ON C_NATIONKEY = n.N_NATIONKEY "
    "JOIN REGION ON N_REGIONKEY = R_REGIONKEY "
    "WHERE O_ORDERDATE >= '1995-01-01' AND O_ORDERDATE < '1996-01-01' AND R_NAME = 'ASIA' "
    "GROUP BY N_NAME ORDER BY revenue DESC LIMIT 5"
)


@pytest.fixture
def facts(catalog):
    v = validate(SQL, catalog)
    return extract_facts(v.sql, catalog, DATES, v.limit_added)


def test_facts_come_from_the_sql(facts):
    assert facts.tables == ["LINEITEM", "ORDERS", "CUSTOMER", "NATION", "REGION"]
    assert "ORDERS.O_CUSTKEY = CUSTOMER.C_CUSTKEY" in facts.joins
    assert facts.date_range == ("1995-01-01", "1995-12-31")
    assert [f.describe() for f in facts.filters if f.column == "REGION.R_NAME"] == ["R_NAME = ASIA"]
    assert facts.group_by == ["N_NAME"]
    assert facts.order_by == ["revenue DESC"]
    assert facts.limit == 5
    assert facts.measures == ["SUM(L_EXTENDEDPRICE * (1 - L_DISCOUNT)) AS revenue"]


def test_safety_limit_is_not_a_fact(catalog):
    v = validate("SELECT COUNT(*) FROM ORDERS WHERE O_ORDERSTATUS IN ('O', 'P')", catalog)
    f = extract_facts(v.sql, catalog, DATES, v.limit_added)
    assert f.limit is None
    assert f.filters[0].describe() == "O_ORDERSTATUS is one of O, P"


@pytest.mark.parametrize(
    "where, expected",
    [
        ("O_ORDERDATE BETWEEN '1996-03-01' AND '1996-03-31'", ("1996-03-01", "1996-03-31")),
        ("O_ORDERDATE > '1997-12-31'", ("1998-01-01", "the end of the data")),
        ("O_ORDERDATE <= DATE '1993-06-30'", ("the start of the data", "1993-06-30")),
    ],
)
def test_date_ranges(catalog, where, expected):
    v = validate(f"SELECT COUNT(*) FROM ORDERS WHERE {where}", catalog)
    assert extract_facts(v.sql, catalog, DATES).date_range == expected


def test_code_explanation_covers_every_fact(facts, tpch):
    text = explain(facts, tpch)
    assert missing_facts(text, facts, tpch) == []
    assert "1995-01-01 to 1995-12-31" in text and "ASIA" in text and "first 5 rows" in text


def test_explanation_that_skips_a_filter_fails(facts, tpch):
    missing = missing_facts("Revenue per nation in 1995, top five.", facts, tpch)
    assert missing == ["R_NAME = ASIA"]


def test_numbers_must_trace_to_cells_facts_or_question():
    cells = ["CHINA", 1234567.891, 42, "Customer#000000042", "1996-01-01"]
    assert untraced_numbers("CHINA earned 1,234,567.89 from 42 orders.", cells) == []
    assert untraced_numbers("Customer#000000042 ordered on 1996-01-01.", cells) == []
    assert untraced_numbers("CHINA earned 1,234,568 in 1995.", cells, "revenue in 1995?") == []
    assert untraced_numbers("CHINA earned 1.2 million.", cells) == ["1.2"]
    assert untraced_numbers("About 43 orders.", cells) == ["43"]


def test_verify_combines_numbers_and_facts(facts, tpch):
    rows = [["INDIA", 100.5], ["CHINA", 90.25]]
    ok = verify("INDIA led with 100.50.", explain(facts, tpch), "q", rows, facts, 2, tpch)
    assert ok.passed, ok.summary()
    bad = verify("INDIA led with 101.", "Grouped by nation.", "q", rows, facts, 2, tpch)
    assert not bad.passed and bad.untraced_numbers == ["101"] and bad.missing_facts
