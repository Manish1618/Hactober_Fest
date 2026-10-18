"""Alignment check: near-miss queries that are valid SQL but answer another question."""

import pytest

from datamind.model.adapter import ScriptedAdapter
from datamind.pipeline.align import code_problems
from datamind.pipeline.facts import extract_facts
from datamind.pipeline.run import Pipeline
from datamind.pipeline.validator import Catalog
from datamind.response import Status
from datamind.snowflake_io.executor import LocalExecutor

duckdb = pytest.importorskip("duckdb")
from datamind.snowflake_io.fixture import build_fixture  # noqa: E402

REV_1995 = (
    "SELECT SUM(L_EXTENDEDPRICE * (1 - L_DISCOUNT)) AS revenue FROM LINEITEM "
    "JOIN ORDERS ON L_ORDERKEY = O_ORDERKEY "
    "WHERE O_ORDERDATE >= '{y}-01-01' AND O_ORDERDATE < '{n}-01-01'"
)
COUNT_1995 = (
    "SELECT COUNT(*) AS n FROM ORDERS "
    "WHERE O_ORDERDATE >= '1995-01-01' AND O_ORDERDATE < '1996-01-01'"
)
TOP_NATIONS = (
    "SELECT N_NAME, COUNT(*) AS n FROM CUSTOMER JOIN NATION ON C_NATIONKEY = N_NATIONKEY "
    "GROUP BY N_NAME ORDER BY n DESC{limit}"
)


@pytest.fixture(scope="module")
def fixture_db():
    return build_fixture()


def problems(tpch, question, sql):
    cat = Catalog(tables=tpch.catalog(), joins=tpch.join_pairs())
    facts = extract_facts(sql, cat, {"ORDERS.O_ORDERDATE"})
    return code_problems(question, sql, facts)


def test_matching_queries_pass(tpch):
    assert problems(tpch, "What was total revenue in 1995?", REV_1995.format(y=1995, n=1996)) == []
    q = "Which five nations have the most customers?"
    assert problems(tpch, q, TOP_NATIONS.format(limit=" LIMIT 5")) == []
    month = (
        "SELECT DATE_TRUNC('MONTH', O_ORDERDATE) AS m, COUNT(*) AS n FROM ORDERS "
        "WHERE O_ORDERDATE >= '1996-01-01' AND O_ORDERDATE < '1997-01-01' GROUP BY m"
    )
    assert problems(tpch, "How did order counts change month by month in 1996?", month) == []


def test_wrong_year_is_caught(tpch):
    found = problems(tpch, "What was total revenue in 1995?", REV_1995.format(y=1994, n=1995))
    assert found and "1995" in found[0]


def test_missing_top_n_limit_is_caught(tpch):
    q = "Which five nations have the most customers?"
    assert "5 results" in problems(tpch, q, TOP_NATIONS.format(limit=""))[0]
    assert "LIMIT 3" in problems(tpch, q, TOP_NATIONS.format(limit=" LIMIT 3"))[0]


def test_missing_breakdown_is_caught(tpch):
    sql = "SELECT COUNT(*) AS n FROM ORDERS WHERE O_ORDERDATE >= '1996-01-01'"
    q = "How many orders were placed per month in 1996?"
    assert any("GROUP BY" in p for p in problems(tpch, q, sql))


def test_listing_without_ranking_needs_no_limit(tpch):
    sql = "SELECT R_NAME FROM REGION"
    assert problems(tpch, "List the five regions.", sql) == []


def _model(sqls, verdicts=None):
    queue, judged = list(sqls), list(verdicts or [])

    def respond(prompt, version):
        if version.startswith("understand"):
            q = prompt.rpartition("<question>")[2].partition("</question>")[0].strip()
            return {"standalone_question": q, "category": "answerable"}
        if version.startswith("align"):
            if not judged:
                raise KeyError("align")
            return judged.pop(0)
        if version.startswith("answer"):
            return {"answer": ""}
        return {"sql": queue.pop(0) if len(queue) > 1 else queue[0]}

    return ScriptedAdapter(respond)


def ask(tpch, db, question, sqls, verdicts=None):
    pipe = Pipeline(tpch, _model(sqls, verdicts), LocalExecutor(db), max_rows=100)
    return pipe.ask(question)


def test_wrong_year_is_repaired(tpch, fixture_db):
    resp = ask(
        tpch, fixture_db, "What was total revenue in 1995?",
        [REV_1995.format(y=1994, n=1995), REV_1995.format(y=1995, n=1996)],
    )  # fmt: skip
    assert resp.status is Status.ANSWERED
    assert "1995-01-01" in resp.evidence.sql
    assert resp.evidence.alignment.startswith("repaired")


def test_unrepairable_mismatch_asks_instead_of_answering(tpch, fixture_db):
    resp = ask(
        tpch, fixture_db, "What was total revenue in 1995?", [REV_1995.format(y=1994, n=1995)]
    )
    assert resp.status is Status.CLARIFY
    assert "1995" in resp.reason
    assert resp.evidence.alignment.startswith("failed")


def test_model_second_opinion_flags_a_wrong_measure(tpch, fixture_db):
    no = {
        "restated": "Counts orders in 1995.",
        "matches": False,
        "problems": ["Counts orders, not revenue."],
    }
    sql = COUNT_1995
    resp = ask(tpch, fixture_db, "What was total revenue in 1995?", [sql], [no, no])
    assert resp.status is Status.ANSWERED
    assert resp.evidence.alignment.startswith("flagged")
    assert any("could not confirm" in x for x in resp.evidence.limits)


def test_model_second_opinion_drives_a_repair(tpch, fixture_db):
    no = {
        "restated": "Counts orders.",
        "matches": False,
        "problems": ["Counts orders, not revenue."],
    }
    yes = {"restated": "Sums 1995 revenue.", "matches": True}
    wrong = COUNT_1995
    resp = ask(
        tpch, fixture_db, "What was total revenue in 1995?",
        [wrong, REV_1995.format(y=1995, n=1996)], [no, yes],
    )  # fmt: skip
    assert resp.status is Status.ANSWERED
    assert "SUM" in resp.evidence.sql
    assert resp.evidence.alignment.startswith("repaired")
    assert resp.evidence.alignment_restated == "Sums 1995 revenue."


def test_alignment_passes_when_both_layers_agree(tpch, fixture_db):
    yes = {"restated": "Sums 1995 revenue.", "matches": True}
    resp = ask(
        tpch,
        fixture_db,
        "What was total revenue in 1995?",
        [REV_1995.format(y=1995, n=1996)],
        [yes],
    )
    assert resp.evidence.alignment == "passed"
    assert "align_v1" in resp.evidence.prompt_versions
