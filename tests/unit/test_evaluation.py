import pytest

from datamind.cli import example_responder
from datamind.evaluation import compare_results, load_set, run_eval
from datamind.model.adapter import ScriptedAdapter
from datamind.pipeline.run import Pipeline


def test_compare_ignores_names_order_and_extra_columns():
    truth = [("ASIA", 10), ("EUROPE", 20)]
    got = [(20.0000001, "EUROPE", "x"), (10, "ASIA", "y")]
    assert compare_results(truth, got)[0]
    assert not compare_results(truth, got, order_matters=True)[0]


def test_compare_detects_wrong_values_and_misaligned_rows():
    assert not compare_results([(1,)], [(2,)])[0]
    assert not compare_results([(1,), (2,)], [(1,)])[0]
    # Each column matches on its own, but the pairs differ.
    assert not compare_results([("A", 1), ("B", 2)], [("A", 2), ("B", 1)])[0]


def test_compare_allows_any_order_within_ties():
    truth = [("A", 3), ("B", 2), ("C", 2), ("D", 1)]
    assert compare_results(truth, [("A", 3), ("C", 2), ("B", 2), ("D", 1)], True)[0]
    assert not compare_results(truth, [("A", 3), ("C", 2), ("D", 2), ("B", 1)], True)[0]
    # A LIMIT can cut a tie, so the last tied block may hold other rows.
    assert compare_results([("A", 3), ("B", 2)], [("A", 3), ("Z", 2)], True, limited=True)[0]
    assert not compare_results([("A", 3), ("B", 2)], [("A", 3), ("Z", 2)], True)[0]


def test_compare_numeric_tolerance():
    from decimal import Decimal

    assert compare_results([(Decimal("1234567.891"),)], [(1234567.8912,)])[0]
    assert not compare_results([(1234.5,)], [(1235.5,)])[0]


def test_golden_sets_load_and_are_large_enough():
    dev, held = load_set("tpch_sf1", "golden"), load_set("tpch_sf1", "heldout")
    for group in ("T1", "T2"):
        total = sum(q.group == group for q in dev.questions + held.questions)
        out = sum(q.group == group for q in held.questions)
        assert total >= 12 and out / total >= 0.25, group
    ids = [q.id for q in dev.questions + held.questions]
    assert len(ids) == len(set(ids))


def test_runner_end_to_end_offline(tpch):
    """A scripted model that answers with the ground truth must score 100% on T1/T2."""
    duckdb = pytest.importorskip("duckdb")  # noqa: F841
    from datamind.snowflake_io.executor import LocalExecutor
    from datamind.snowflake_io.fixture import build_fixture

    golden = load_set("tpch_sf1", "golden")
    oracle = {q.question: q.ground_truth_sql for q in golden.questions if q.ground_truth_sql}
    model = ScriptedAdapter(example_responder(tpch, oracle))
    pipe = Pipeline(tpch, model, LocalExecutor(build_fixture()), phrase_answers=False)
    report = run_eval(pipe, golden, {"T1", "T2", "T7"})
    failed = [(r.id, r.detail) for r in report.results if not r.passed]
    assert failed == []
    ok, summary = report.gate()
    assert ok, summary
    assert "T7 blocked 10/10" in summary


def test_compare_accepts_a_percentage_for_a_fraction():
    assert compare_results([("A", 0.25), ("B", 0.75)], [("B", 75.0), ("A", 25.0)])[0]
    assert not compare_results([("A", 0.25)], [("A", 26.0)])[0]


def test_golden_sets_cover_every_prd_group():
    dev, held = load_set("tpch_sf1", "golden"), load_set("tpch_sf1", "heldout")
    sizes = {"T1": 12, "T2": 12, "T3": 8, "T4": 10, "T5": 8, "T6": 14, "T7": 10}
    for group, size in sizes.items():
        assert sum(q.group == group for q in dev.questions + held.questions) >= size, group
    demo = load_set("tpch_demo", "golden")
    assert sum(q.group == "T8" for q in demo.questions) >= 3


def _result(**kw):
    from datamind.evaluation.runner import QuestionResult

    base = dict(id="x", group="T1", question="q", expected_status="ANSWERED")
    base.update(status="ANSWERED", passed=True, detail="")
    base.update(kw)
    return QuestionResult(**base)


def test_targets_and_trust_only_require_expected_statuses():
    from datamind.evaluation.runner import EvalReport

    report = EvalReport("tpch_sf1", "heldout", "local", "m", "2026-10-18T00:00:00")
    report.results = [
        _result(seconds=4, faithful=True, fidelity=True, evidence_complete=True),
        _result(id="y", group="T5", passed=False, seconds=20, faithful=True, fidelity=False),
        _result(id="z", group="T6", expected_status="CLARIFY", status="CLARIFY", ambiguous=True),
    ]
    trust_ok, summary = report.trust()
    assert "missing" not in summary  # the split expects no BLOCKED or UNSUPPORTED
    targets = {t.name: t for t in report.targets()}
    assert targets["Execution accuracy"].value == "2/3 = 67%"
    assert targets["Execution accuracy"].goal == ">= 80%"  # held-out target
    assert not targets["Explanation fidelity"].passed
    assert targets["Ambiguity handled"].passed
    assert not targets["Follow-up accuracy"].passed
    assert not targets["Latency"].passed


def test_change_sql_must_be_a_plain_edit_of_the_demo_tables():
    from datamind.evaluation.writers import check_change
    from datamind.snowflake_io.executor import ExecutionError

    tables = {"ORDERS", "CUSTOMER"}
    check_change("UPDATE CUSTOMER SET C_ACCTBAL = 1 WHERE C_CUSTKEY = 1", tables)
    for bad in (
        "DROP TABLE CUSTOMER",
        "UPDATE LINEITEM SET L_TAX = 0",
        "DELETE FROM CUSTOMER; DROP TABLE ORDERS",
        "SELECT * FROM CUSTOMER",
    ):
        with pytest.raises(ExecutionError):
            check_change(bad, tables)


def test_data_change_test_offline():
    """T8: the answer must follow an edit of the demo tables, with a new query ID."""
    pytest.importorskip("duckdb")
    from datamind.config import load_dataset
    from datamind.evaluation.writers import LocalWriter
    from datamind.snowflake_io.executor import LocalExecutor
    from datamind.snowflake_io.fixture import build_fixture

    cfg = load_dataset("tpch_demo")
    golden = load_set("tpch_demo", "golden")
    oracle = {q.question: q.ground_truth_sql for q in golden.questions}
    executor = LocalExecutor(build_fixture())
    pipe = Pipeline(
        cfg, ScriptedAdapter(example_responder(cfg, oracle)), executor, phrase_answers=False
    )
    report = run_eval(pipe, golden, writer=LocalWriter(executor.connection, cfg))
    assert [(r.id, r.passed) for r in report.results] == [
        ("T8-01", True),
        ("T8-02", True),
        ("T8-03", True),
    ], [r.detail for r in report.results]
    count = executor.run("SELECT COUNT(*) FROM ORDERS", request_id="t", max_rows=1).rows[0][0]
    assert count == 200  # the demo tables were restored


def test_benchmark_table_merges_runs(tmp_path):
    from datamind.evaluation.benchmark import BenchmarkRow, write_benchmark

    row = BenchmarkRow("gemma-a", "google", "dev", "local", "2026-10-18", {"T1": "8/8"}, "8/8")
    out = write_benchmark([row], tmp_path / "benchmark.md")
    row2 = BenchmarkRow("gemma-b", "google", "dev", "local", "2026-10-18", {"T1": "7/8"}, "7/8")
    write_benchmark([row2], out)
    text = out.read_text()
    assert "gemma-a" in text and "gemma-b" in text and "| T1 |" in text
