"""End to end on the offline fixture: scripted model, validator, DuckDB executor."""

import pytest

from datamind.model.adapter import ScriptedAdapter
from datamind.pipeline.context import Conversation
from datamind.pipeline.run import MAX_REPAIRS, Pipeline
from datamind.response import Status
from datamind.snowflake_io.executor import ExecutionError, LocalExecutor

duckdb = pytest.importorskip("duckdb")
from datamind.snowflake_io.fixture import build_fixture  # noqa: E402


@pytest.fixture(scope="module")
def fixture_db():
    return build_fixture()


def scripted(sqls, understanding=None, answer=None, calls=None):
    """Responder per task. `sqls` is one SQL string or a list used in turn for repairs."""
    queue = [sqls] if isinstance(sqls, str) else list(sqls)

    def respond(prompt, version):
        if calls is not None:
            calls.append((version, prompt))
        if version.startswith("understand"):
            q = prompt.rpartition("<question>")[2].partition("</question>")[0].strip()
            return understanding or {"standalone_question": q, "category": "answerable"}
        if version.startswith("answer"):
            return answer or {"answer": ""}
        sql = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"sql": sql, "assumptions": ["assumed"]}

    return ScriptedAdapter(respond)


def run(tpch, fixture_db, sql, question="q", **kw):
    model = scripted(sql, **kw)
    return Pipeline(tpch, model, LocalExecutor(fixture_db), max_rows=100).ask(question)


class Exploding:
    backend = "should-not-run"

    def run(self, sql, *, request_id, max_rows):
        if request_id == "time-anchor":  # the read-only MAX(date) lookup is allowed
            raise ExecutionError("no data")
        raise AssertionError("blocked SQL reached the executor")


def test_answers_with_evidence(tpch, fixture_db):
    sql = tpch.examples[1].sql  # total revenue in 1994
    resp = run(tpch, fixture_db, sql, "What was total revenue in 1994?")
    assert resp.status is Status.ANSWERED
    ev = resp.evidence
    assert ev.query_id.startswith("local-")
    assert ev.rows_returned == 1 and ev.columns == ["revenue"]
    assert ev.tables == ["LINEITEM", "ORDERS"]
    assert "LIMIT 100" in ev.sql
    assert ev.prompt_versions == ["understand_v3", "draft_sql_v2", "answer_v2"]
    assert ev.data_latest_date.startswith("1998")
    assert resp.assumptions == ["assumed", "Dates used: 1994-01-01 to 1994-12-31 (O_ORDERDATE)."]
    assert "1994-01-01 to 1994-12-31" in resp.explanation
    assert ev.verification.startswith("passed")
    assert "ORDERS.O_ORDERDATE" in ev.columns_used and ev.query_facts[0].startswith("Tables:")

    truth = fixture_db.execute(
        "SELECT SUM(L_EXTENDEDPRICE * (1 - L_DISCOUNT)) FROM LINEITEM JOIN ORDERS "
        "ON L_ORDERKEY = O_ORDERKEY WHERE O_ORDERDATE >= DATE '1994-01-01' "
        "AND O_ORDERDATE < DATE '1995-01-01'"
    ).fetchone()[0]
    assert ev.rows[0][0] == pytest.approx(float(truth))


def test_model_answer_numbers_come_from_the_result(tpch, fixture_db):
    resp = run(
        tpch,
        fixture_db,
        "SELECT COUNT(*) AS regions FROM REGION",
        "How many regions are there?",
        answer={"answer": "There are {r1.regions} regions."},
    )
    assert resp.answer == "There are 5 regions."


def test_model_answer_with_its_own_number_is_replaced(tpch, fixture_db):
    resp = run(
        tpch,
        fixture_db,
        "SELECT COUNT(*) AS regions FROM REGION",
        "How many regions are there?",
        answer={"answer": "There are 7 regions."},
    )
    assert resp.answer == "regions: 5"


def test_write_request_is_blocked_before_the_model(tpch):
    calls = []
    model = scripted("DELETE FROM ORDERS", calls=calls)
    resp = Pipeline(tpch, model, Exploding()).ask("Delete all orders from 1994.")
    assert resp.status is Status.BLOCKED
    assert calls == []


def test_destructive_sql_is_blocked_before_execution(tpch, fixture_db):
    model = scripted("DELETE FROM ORDERS")
    resp = Pipeline(tpch, model, Exploding()).ask("Which orders are oldest?")
    assert resp.status is Status.BLOCKED
    assert resp.evidence.query_id == ""
    assert resp.evidence.repair_attempts == 0
    assert fixture_db.execute("SELECT COUNT(*) FROM ORDERS").fetchone()[0] > 0


def test_model_classifying_unsafe_is_blocked(tpch):
    model = scripted("SELECT 1", understanding={"standalone_question": "x", "category": "unsafe"})
    assert Pipeline(tpch, model, Exploding()).ask("x").status is Status.BLOCKED


def test_bad_sql_is_repaired(tpch, fixture_db):
    calls = []
    resp = run(
        tpch,
        fixture_db,
        ["SELECT N_NAME FROM ORDERS", "SELECT COUNT(*) AS n FROM NATION"],
        "How many nations?",
        calls=calls,
    )
    assert resp.status is Status.ANSWERED
    assert resp.evidence.repair_attempts == 1
    drafts = [p for v, p in calls if v.startswith("draft")]
    assert len(drafts) == 2 and "Earlier attempts failed" in drafts[1]
    assert "SELECT N_NAME FROM ORDERS" in drafts[1]


def test_repairs_stop_after_the_limit(tpch, fixture_db):
    calls = []
    resp = run(tpch, fixture_db, "SELECT NOPE FROM NATION", "q", calls=calls)
    assert resp.status is Status.ERROR
    assert len([v for v, _ in calls if v.startswith("draft")]) == MAX_REPAIRS + 1
    assert resp.evidence.repair_attempts == MAX_REPAIRS


def test_ambiguous_question_asks_for_clarification(tpch):
    u = {
        "standalone_question": "Which products are doing best?",
        "category": "ambiguous",
        "reason": "Best could mean revenue or quantity.",
        "clarifying_question": "Best by what?",
        "options": ["Revenue", "Quantity sold"],
    }
    resp = Pipeline(tpch, scripted("SELECT 1", understanding=u), Exploding()).ask("q")
    assert resp.status is Status.CLARIFY
    assert resp.clarification.options == ["Revenue", "Quantity sold"]


def test_unsupported_question_suggests_an_alternative(tpch):
    u = {
        "standalone_question": "What will revenue be next year?",
        "category": "unsupported",
        "reason": "That is a forecast.",
        "alternative": "What was revenue in each of the last three years?",
    }
    resp = Pipeline(tpch, scripted("SELECT 1", understanding=u), Exploding()).ask("q")
    assert resp.status is Status.UNSUPPORTED
    assert resp.suggestions == ["What was revenue in each of the last three years?"]


def test_follow_up_without_history_asks_for_the_full_question(tpch):
    u = {"standalone_question": "only the top 3", "used_context": True, "category": "answerable"}
    resp = Pipeline(tpch, scripted("SELECT 1", understanding=u), Exploding()).ask("only top 3")
    assert resp.status is Status.CLARIFY


def test_follow_up_uses_the_conversation(tpch, fixture_db):
    conv = Conversation()
    p = Pipeline(tpch, scripted("SELECT N_NAME AS n FROM NATION"), LocalExecutor(fixture_db))
    p.ask("List the nations", conv)
    calls = []
    u = {
        "standalone_question": "List the first 3 nations",
        "used_context": True,
        "category": "answerable",
    }
    p.model = scripted("SELECT N_NAME AS n FROM NATION LIMIT 3", understanding=u, calls=calls)
    resp = p.ask("only 3", conv)
    assert resp.status is Status.ANSWERED
    assert resp.evidence.question_as_understood == "List the first 3 nations"
    assert resp.evidence.context_used == "Previous question: List the nations"
    assert "Turn 1 question: List the nations" in calls[0][1]
    assert len(conv.turns) == 2


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


def test_missing_value_gives_no_data_with_valid_values(tpch, fixture_db):
    resp = run(
        tpch,
        fixture_db,
        "SELECT COUNT(*) AS n FROM CUSTOMER JOIN NATION ON C_NATIONKEY = N_NATIONKEY "
        "WHERE N_NAME = 'ATLANTIS'",
        "How many customers are in Atlantis?",
    )
    assert resp.status is Status.NO_DATA or resp.evidence.rows[0][0] == 0


def test_empty_filter_explains_the_missing_value(tpch, fixture_db):
    resp = run(
        tpch,
        fixture_db,
        "SELECT C_NAME FROM CUSTOMER JOIN NATION ON C_NATIONKEY = N_NATIONKEY "
        "WHERE N_NAME = 'ATLANTIS'",
        "Show customers in Atlantis",
    )
    assert resp.status is Status.NO_DATA
    assert "ATLANTIS" in resp.reason and "Values that exist include" in resp.reason
    assert resp.suggestions and "Atlantis" not in resp.suggestions[0]


def test_empty_date_range_reports_coverage(tpch, fixture_db):
    resp = run(
        tpch,
        fixture_db,
        "SELECT O_ORDERKEY FROM ORDERS WHERE O_ORDERDATE >= '2005-01-01' "
        "AND O_ORDERDATE < '2006-01-01'",
        "Orders in 2005",
    )
    assert resp.status is Status.NO_DATA
    assert "the data covers ORDERS.O_ORDERDATE from 1992" in resp.reason


def test_model_explanation_missing_a_fact_is_replaced(tpch, fixture_db):
    resp = run(
        tpch,
        fixture_db,
        "SELECT COUNT(*) AS n FROM CUSTOMER WHERE C_MKTSEGMENT = 'BUILDING'",
        "How many BUILDING customers?",
        answer={"answer": "There are {r1.n} such customers.", "explanation": "I counted rows."},
    )
    assert resp.answer.startswith("There are ")
    assert "BUILDING" in resp.explanation
    assert "replaced" in resp.evidence.verification


def test_model_explanation_with_invented_number_is_replaced(tpch, fixture_db):
    resp = run(
        tpch,
        fixture_db,
        "SELECT COUNT(*) AS n FROM CUSTOMER WHERE C_MKTSEGMENT = 'BUILDING'",
        "How many BUILDING customers?",
        answer={"answer": "{r1.n}.", "explanation": "Counted BUILDING customers, about 300."},
    )
    assert "300" not in resp.explanation


def test_non_english_question_is_unsupported(tpch):
    resp = Pipeline(tpch, scripted("SELECT 1"), Exploding()).ask("¿Cuántos clientes hay?")
    assert resp.status is Status.UNSUPPORTED
    assert "English" in resp.reason


def test_snowflake_outage_is_an_error_without_repairs(tpch):
    from datamind.snowflake_io.executor import BackendUnavailable

    class Down:
        backend = "snowflake"

        def run(self, *a, **k):
            raise BackendUnavailable("Could not connect to Snowflake")

    calls = []
    resp = Pipeline(tpch, scripted("SELECT 1 FROM REGION", calls=calls), Down()).ask("q")
    assert resp.status is Status.ERROR
    assert "not reachable" in resp.reason
    assert len([v for v, _ in calls if v.startswith("draft")]) == 1


def test_error_keeps_the_last_rejected_sql(tpch, fixture_db):
    resp = run(tpch, fixture_db, "SELECT NOPE FROM NATION", "q")
    assert resp.status is Status.ERROR
    assert "NOPE" in resp.evidence.rejected_sql


def test_total_over_no_rows_is_no_data(tpch, fixture_db):
    resp = run(
        tpch,
        fixture_db,
        "SELECT SUM(O_TOTALPRICE) AS total FROM ORDERS JOIN CUSTOMER ON O_CUSTKEY = C_CUSTKEY "
        "JOIN NATION ON C_NATIONKEY = N_NATIONKEY WHERE N_NAME = 'ATLANTIS'",
        "Show sales for Atlantis.",
    )
    assert resp.status is Status.NO_DATA
    assert "ATLANTIS" in resp.reason
