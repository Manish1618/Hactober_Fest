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
