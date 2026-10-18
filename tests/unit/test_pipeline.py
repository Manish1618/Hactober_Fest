"""End to end on the offline fixture: scripted model, validator, DuckDB executor."""

import pytest

from datamind.model.adapter import ScriptedAdapter
from datamind.pipeline.run import Pipeline
from datamind.response import Status
from datamind.snowflake_io.executor import LocalExecutor

duckdb = pytest.importorskip("duckdb")
from datamind.snowflake_io.fixture import build_fixture  # noqa: E402


@pytest.fixture(scope="module")
def fixture_db():
    return build_fixture()


def run(tpch, fixture_db, sql, question="q"):
    model = ScriptedAdapter(lambda p: {"sql": sql, "assumptions": ["assumed"]})
    return Pipeline(tpch, model, LocalExecutor(fixture_db), max_rows=100).ask(question)


def test_answers_with_evidence(tpch, fixture_db):
    sql = tpch.examples[1].sql  # total revenue in 1994
    resp = run(tpch, fixture_db, sql, "What was total revenue in 1994?")
    assert resp.status is Status.ANSWERED
    ev = resp.evidence
    assert ev.query_id.startswith("local-")
    assert ev.rows_returned == 1 and ev.columns == ["revenue"]
    assert ev.tables == ["LINEITEM", "ORDERS"]
    assert "LIMIT 100" in ev.sql
    assert str(ev.rows[0][0]) in resp.answer  # number comes from the result cell
    assert resp.assumptions == ["assumed"]

    truth = fixture_db.execute(
        "SELECT SUM(L_EXTENDEDPRICE * (1 - L_DISCOUNT)) FROM LINEITEM JOIN ORDERS "
        "ON L_ORDERKEY = O_ORDERKEY WHERE O_ORDERDATE >= DATE '1994-01-01' "
        "AND O_ORDERDATE < DATE '1995-01-01'"
    ).fetchone()[0]
    assert ev.rows[0][0] == pytest.approx(float(truth))


def test_destructive_sql_is_blocked_before_execution(tpch, fixture_db):
    class Exploding:
        backend = "should-not-run"

        def run(self, *a, **k):
            raise AssertionError("blocked SQL reached the executor")

    model = ScriptedAdapter(lambda p: {"sql": "DELETE FROM ORDERS"})
    resp = Pipeline(tpch, model, Exploding()).ask("Delete all orders from 1994.")
    assert resp.status is Status.BLOCKED
    assert resp.evidence.query_id == ""
    assert fixture_db.execute("SELECT COUNT(*) FROM ORDERS").fetchone()[0] > 0


def test_empty_result_is_no_data(tpch, fixture_db):
    resp = run(tpch, fixture_db, "SELECT N_NAME FROM NATION WHERE N_NAME = 'ATLANTIS'")
    assert resp.status is Status.NO_DATA
    assert resp.evidence.query_id


def test_truncation_is_reported(tpch, fixture_db):
    resp = run(tpch, fixture_db, "SELECT O_ORDERKEY FROM ORDERS")
    assert resp.evidence.rows_returned == 100
    assert resp.evidence.rows_shown == 50
    assert any("first 100 rows" in x for x in resp.evidence.limits)


def test_overlong_question_is_refused(tpch, fixture_db):
    resp = run(tpch, fixture_db, "SELECT 1", "x" * 501)
    assert resp.status is Status.UNSUPPORTED


def test_control_characters_are_removed(tpch, fixture_db):
    resp = run(tpch, fixture_db, "SELECT COUNT(*) FROM REGION", "how\x00 many\tregions?")
    assert resp.evidence.question_as_understood == "how many regions?"
