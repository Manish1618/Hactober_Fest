from datamind.config.loader import load_dataset
from datamind.response import Evidence, Response, Status
from datamind.ui.smart import chart_spec, follow_ups, proof_bundle, rows_csv, snowsight_url


def test_single_number_is_a_kpi():
    spec = chart_spec(["TOTAL_REVENUE"], [[38.4]])
    assert spec.kind == "kpi" and spec.label == "total revenue"


def test_year_and_value_is_a_line_whatever_the_column_order():
    assert chart_spec(["YEAR", "VALUE"], [[2019, 1.0], [2020, 2.0]]).kind == "line"
    spec = chart_spec(["VALUE", "YEAR"], [[1.0, 2019], [2.0, 2020]])
    assert (spec.kind, spec.x, spec.y) == ("line", "YEAR", "VALUE")


def test_iso_dates_make_a_line():
    spec = chart_spec(["O_ORDERDATE", "N"], [["1995-01-01", 3], ["1995-02-01", 4]])
    assert spec.kind == "line"


def test_labels_and_numbers_make_bars_until_too_many():
    rows = [[f"N{i}", i] for i in range(5)]
    assert chart_spec(["NATION", "REVENUE"], rows).kind == "bar"
    assert chart_spec(["NATION", "REVENUE"], [[f"N{i}", i] for i in range(40)]) is None


def test_text_only_results_get_no_chart():
    assert chart_spec(["NAME"], [["a"], ["b"]]) is None
    assert chart_spec([], []) is None


def _answered(question, rows=([1.0],)):
    ev = Evidence(question_as_understood=question, columns=["X"], rows=[list(r) for r in rows])
    return Response(request_id="r1", status=Status.ANSWERED, answer="a", evidence=ev)


def test_follow_ups_come_from_the_question():
    cfg = load_dataset("tpch_sf1")
    nexts = follow_ups(_answered("What was total revenue in 1995?"), cfg)
    assert nexts[0] == "What was total revenue in 1994?"
    assert nexts[1] == "What was total revenue by year?"
    assert len(nexts) == 3


def test_no_follow_ups_after_a_refusal():
    resp = Response(request_id="r", status=Status.BLOCKED)
    assert follow_ups(resp, load_dataset("tpch_sf1")) == []


def test_exports_hold_only_shown_rows_and_no_settings():
    resp = _answered("q", rows=([1.0], [2.0]))
    assert rows_csv(resp).splitlines() == ["X", "1.0", "2.0"]
    bundle = proof_bundle(resp)
    assert bundle["result"]["rows_shown"] == [[1.0], [2.0]]
    text = str(bundle).lower()
    assert "key" not in text and "password" not in text and "account" not in text


def test_snowsight_url_needs_an_org_account_identifier():
    url = snowsight_url("ABCDEF-XY12345", "01ab")
    assert url == "https://app.snowflake.com/abcdef/xy12345/#/compute/history/queries/01ab/detail"
    assert snowsight_url("xy12345.us-east-1", "01ab") == ""


def test_no_by_year_follow_up_for_a_question_already_over_time():
    cfg = load_dataset("tpch_sf1")
    resp = _answered("How did order counts change month by month in 1996?", rows=[[1]] * 12)
    nexts = follow_ups(resp, cfg)
    assert nexts[0] == "How did order counts change month by month in 1995?"
    assert not any("by year" in n or "top 5" in n for n in nexts)


def test_kpi_counts_up_to_the_whole_number_and_settles_on_the_exact_value():
    from datamind.ui.style import kpi_html

    out = kpi_html(2229864.66, "revenue", "The total revenue was 2,229,864.66.")
    assert "--dm-target:2229864" in out and ">2,229,864.66<" in out
    assert "dm-count" not in kpi_html(8687897464.58, "revenue", "a")  # too big to count in CSS
    assert "dm-now" in kpi_html(-3.5, "change", "a")
