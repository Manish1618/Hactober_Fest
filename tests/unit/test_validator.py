import pytest

from datamind.pipeline.validator import validate


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM ORDERS",
        "select count(*) from orders;",
        "SELECT o.O_ORDERKEY FROM ORDERS o WHERE o.O_ORDERDATE >= '1995-01-01'",
        "WITH t AS (SELECT O_CUSTKEY k FROM ORDERS) SELECT k, COUNT(*) FROM t GROUP BY k",
        "SELECT N_NAME FROM NATION UNION ALL SELECT R_NAME FROM REGION",
        "SELECT C_NAME, RANK() OVER (ORDER BY C_ACCTBAL DESC) r FROM CUSTOMER",
        "SELECT CORR(C_ACCTBAL, C_NATIONKEY) FROM CUSTOMER",
        "SELECT DATE_TRUNC('MONTH', O_ORDERDATE) m, COUNT(*) FROM ORDERS GROUP BY m ORDER BY m",
        "SELECT * FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.REGION",
        "SELECT * FROM TPCH_SF1.REGION",
    ],
)
def test_allows_read_only_queries(sql, catalog):
    r = validate(sql, catalog)
    assert r.ok, r.reason


@pytest.mark.parametrize(
    "sql, code",
    [
        ("", "empty"),
        ("SELEC * FROM ORDERS", "parse_error"),
        ("SELECT 1 FROM ORDERS; SELECT 2 FROM ORDERS", "multiple_statements"),
        ("SELECT * FROM ORDERS; DROP TABLE ORDERS", "multiple_statements"),
        ("DELETE FROM ORDERS", "not_select"),
        ("UPDATE ORDERS SET O_TOTALPRICE = 0", "not_select"),
        ("INSERT INTO ORDERS SELECT * FROM ORDERS", "not_select"),
        ("MERGE INTO ORDERS USING ORDERS s ON 1=1 WHEN MATCHED THEN DELETE", "not_select"),
        ("CREATE TABLE X AS SELECT * FROM ORDERS", "not_select"),
        ("DROP TABLE ORDERS", "not_select"),
        ("TRUNCATE TABLE ORDERS", "not_select"),
        ("ALTER SESSION SET QUERY_TAG = 'x'", "not_select"),
        ("USE ROLE ACCOUNTADMIN", "not_select"),
        ("GRANT ROLE DATAMIND_READER TO ROLE PUBLIC", "not_select"),
        ("CALL my_proc()", "not_select"),
        ("COPY INTO @stage FROM ORDERS", "not_select"),
        ("PUT file:///tmp/x @stage", "not_select"),
        ("EXECUTE IMMEDIATE $$ DELETE FROM ORDERS $$", "not_select"),
        ("SHOW TABLES", "not_select"),
        ("SELECT * INTO X FROM ORDERS", "denied_operation"),
        ("SELECT SYSTEM$CANCEL_ALL_QUERIES()", "denied_function"),
        ("SELECT * FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))", "denied_function"),
        ("SELECT * FROM IDENTIFIER('ORDERS')", "unknown_table"),
        ("SELECT * FROM @my_stage", "unknown_table"),
        ("SELECT * FROM SECRET_TABLE", "unknown_table"),
        ("SELECT * FROM OTHER_DB.TPCH_SF1.ORDERS", "unknown_table"),
        ("SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY", "unknown_table"),
        ("SELECT O_BOGUS FROM ORDERS", "unknown_column"),
        ("SELECT C_NAME FROM ORDERS", "unknown_column"),
        ("SELECT 1", "no_table"),
        ("SELECT * FROM ORDERS LIMIT 2 + 3", "bad_limit"),
    ],
)
def test_blocks_unsafe_or_invalid(sql, code, catalog):
    r = validate(sql, catalog)
    assert not r.ok
    assert r.code == code, r.reason
    assert r.reason


def test_adds_limit_when_missing(catalog):
    r = validate("SELECT O_ORDERKEY FROM ORDERS", catalog, max_rows=100)
    assert r.ok and r.limit_added and r.limit == 100
    assert "LIMIT 100" in r.sql


def test_keeps_small_limit(catalog):
    r = validate("SELECT O_ORDERKEY FROM ORDERS LIMIT 5", catalog, max_rows=100)
    assert r.ok and not r.limit_added and r.limit == 5
    assert "LIMIT 5" in r.sql


def test_caps_large_limit(catalog):
    r = validate("SELECT O_ORDERKEY FROM ORDERS LIMIT 50000", catalog, max_rows=100)
    assert r.ok and r.limit == 100
    assert "LIMIT 100" in r.sql and "50000" not in r.sql


def test_reports_tables_used(catalog):
    r = validate(
        "SELECT N_NAME, COUNT(*) FROM CUSTOMER JOIN NATION ON C_NATIONKEY = N_NATIONKEY "
        "GROUP BY N_NAME",
        catalog,
    )
    assert r.tables == ["CUSTOMER", "NATION"]


def test_cte_cannot_shadow_into_unknown_schema(catalog):
    r = validate("WITH ORDERS AS (SELECT 1 x) SELECT * FROM OTHER.ORDERS", catalog)
    assert not r.ok
