"""DataMind interface (PRD section 11). Run with `datamind ui` or `streamlit run` on this file.

The page calls the library directly: every answer card shows the status, the answer, assumption
chips, the explanation and a collapsed evidence section with the fields in PRD section 8.5.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from datamind.config import datasets_dir, load_dataset
from datamind.model.adapter import ModelError, build_adapter
from datamind.pipeline.context import Conversation
from datamind.pipeline.run import Pipeline
from datamind.response import Response, Status
from datamind.settings import get_settings
from datamind.snowflake_io.executor import LocalExecutor, SnowflakeExecutor
from datamind.ui.smart import chart_spec, follow_ups, proof_bundle, rows_csv, snowsight_url
from datamind.ui.style import CSS, hero, kpi_html

st.set_page_config(page_title="DataMind", page_icon="🔎", layout="wide")

STATUS_LABEL = {
    Status.ANSWERED: ("Answered", "green"),
    Status.CLARIFY: ("Needs one detail", "blue"),
    Status.NO_DATA: ("No matching data", "orange"),
    Status.UNSUPPORTED: ("Not supported", "gray"),
    Status.BLOCKED: ("Blocked", "red"),
    Status.ERROR: ("Something failed", "red"),
}


def available_datasets() -> list[str]:
    return sorted(p.name for p in datasets_dir().iterdir() if (p / "dataset.yaml").is_file())


@st.cache_resource(show_spinner=False)
def get_pipeline(dataset: str) -> Pipeline:
    settings = get_settings()
    cfg = load_dataset(dataset)
    if settings.backend == "local":
        from datamind.snowflake_io.fixture import build_fixture_for

        executor = LocalExecutor(build_fixture_for(cfg))
    else:
        executor = SnowflakeExecutor(settings.snowflake, cfg)
    return Pipeline(cfg, build_adapter(settings.model), executor, max_rows=settings.max_rows)


@st.cache_data(ttl=300, show_spinner=False)
def connection_status(dataset: str) -> tuple[str, str]:
    pipe = get_pipeline(dataset)
    latest = pipe.latest_date()
    data = f"connected, data up to {latest}" if latest != "unknown" else "not reachable"
    try:
        model = pipe.model.ping() if hasattr(pipe.model, "ping") else f"ready: {pipe.model.name}"
    except ModelError as e:
        model = f"not reachable ({e})"
    return data, model


def ask(question: str) -> None:
    st.session_state.pending = question


def chip_row(items: list[str]) -> None:
    if items:
        st.markdown(" ".join(f":blue-badge[{a}]" for a in items))


def evidence_panel(resp: Response) -> None:
    ev = resp.evidence
    with st.expander("Evidence", expanded=False):
        st.markdown(f"**Question as understood:** {ev.question_as_understood}")
        if ev.context_used:
            st.markdown(f"**Context used:** {ev.context_used}")
        if resp.assumptions:
            st.markdown("**Assumptions:** " + "; ".join(resp.assumptions))
        if ev.sql:
            st.markdown("**SQL** (exactly as run)")
            st.code(ev.sql, language="sql")
        elif ev.rejected_sql:
            st.markdown("**Last SQL attempt** (rejected, never run)")
            st.code(ev.rejected_sql, language="sql")
        if ev.query_id:
            st.markdown("**Query ID** (copy with the button on the right)")
            st.code(ev.query_id, language=None)
            details = [
                f"{ev.elapsed_seconds}s",
                f"{ev.rows_returned} rows returned, {ev.rows_shown} shown",
            ]
            if ev.warehouse:
                details.append(f"warehouse {ev.warehouse}")
            if ev.role:
                details.append(f"role {ev.role}")
            st.markdown("**Run details:** " + ", ".join(details))
        if ev.columns:
            st.markdown("**Result rows**")
            st.dataframe(
                pd.DataFrame(ev.rows, columns=ev.columns), hide_index=True, width="stretch"
            )
        if ev.query_facts:
            st.markdown("**Query facts** (read from the SQL by code)")
            st.markdown("\n".join(f"- {f}" for f in ev.query_facts))
        if ev.tables or ev.columns_used:
            st.markdown(
                f"**Sources:** {ev.dataset}. Tables {', '.join(ev.tables)}. "
                f"Columns {', '.join(ev.columns_used)}."
            )
            st.caption(ev.licence_note)
        snapshot = []
        if ev.executed_at:
            snapshot.append(f"ran {ev.executed_at}")
        if ev.data_coverage:
            snapshot.append(f"data covers {ev.data_coverage}")
        elif ev.data_latest_date:
            snapshot.append(f"latest date in the data {ev.data_latest_date}")
        if snapshot:
            st.markdown("**Data snapshot:** " + "; ".join(snapshot))
        st.markdown(
            f"**Model:** {ev.model}, prompts {', '.join(ev.prompt_versions) or 'none'}"
            + (f", {ev.repair_attempts} repair(s)" if ev.repair_attempts else "")
        )
        if ev.alignment:
            st.markdown(f"**Alignment check:** {ev.alignment}")
            if ev.alignment_restated:
                st.caption(f"Second reading of the SQL: {ev.alignment_restated}")
        if ev.verification:
            st.markdown(f"**Answer check:** {ev.verification}")
        for limit in ev.limits:
            st.markdown(f"**Limit:** {limit}")
        name = ev.query_id or resp.request_id
        left, right = st.columns(2)
        if ev.columns:
            left.download_button(
                "Download data (CSV)",
                rows_csv(resp),
                file_name=f"datamind-{name}.csv",
                mime="text/csv",
                key=f"csv-{resp.request_id}",
                width="stretch",
            )
        right.download_button(
            "Download proof bundle (JSON)",
            json.dumps(proof_bundle(resp), indent=2),
            file_name=f"datamind-proof-{name}.json",
            mime="application/json",
            key=f"dl-{resp.request_id}",
            width="stretch",
        )
        if ev.rows_shown < ev.rows_returned:
            st.caption(f"The CSV holds the {ev.rows_shown} rows shown, not all {ev.rows_returned}.")


def kpi_card(value, label: str, answer: str) -> None:
    st.markdown(kpi_html(value, label, answer), unsafe_allow_html=True)


def chart(resp: Response, spec, index: int) -> None:
    import plotly.express as px

    ev = resp.evidence
    df = pd.DataFrame(ev.rows, columns=ev.columns)
    if spec.kind == "line":
        df = df.sort_values(spec.x)
        fig = px.area(df, x=spec.x, y=spec.y, markers=True)
        fig.update_traces(line={"width": 3, "color": "#8b5cf6"}, fillcolor="rgba(139,92,246,0.18)")
    else:
        df = df.sort_values(spec.y)
        fig = px.bar(
            df,
            x=spec.y,
            y=spec.x,
            orientation="h",
            color=spec.y,
            color_continuous_scale=["#22d3ee", "#8b5cf6", "#ec4899"],
        )
        fig.update_layout(coloraxis_showscale=False)
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        height=320,
        font={"family": "Inter, sans-serif"},
        transition={"duration": 600, "easing": "cubic-in-out"},
    )
    st.plotly_chart(fig, key=f"chart-{index}", config={"displaylogo": False})
    if ev.rows_shown < ev.rows_returned:
        st.caption(f"Chart shows the first {ev.rows_shown} of {ev.rows_returned} rows.")


def audit_trail(resp: Response) -> None:
    ev = resp.evidence
    if not ev.query_id:
        return
    if ev.backend != "snowflake":
        return
    url = snowsight_url(get_settings().snowflake.account, ev.query_id)
    link = f" [Open in Snowsight]({url})" if url else ""
    st.info(
        f"**Snowflake audit trail:** query ID `{ev.query_id}`. Paste it into Snowsight query "
        f"history to see the run.{link}",
        icon="❄️",
    )


def answer_card(resp: Response, question: str, index: int) -> None:
    label, colour = STATUS_LABEL[resp.status]
    st.markdown(f":{colour}-badge[{label}]")
    if resp.status is Status.ANSWERED:
        spec = chart_spec(resp.evidence.columns, resp.evidence.rows)
        if spec and spec.kind == "kpi":
            kpi_card(resp.evidence.rows[0][0], spec.label, resp.answer)
        else:
            st.markdown(resp.answer)
        chip_row(resp.assumptions)
        if resp.explanation:
            st.caption(resp.explanation)
        if spec and spec.kind != "kpi":
            chart(resp, spec, index)
        audit_trail(resp)
    elif resp.status is Status.CLARIFY and resp.clarification:
        if resp.evidence.alignment.startswith("failed") and resp.reason:
            st.markdown(resp.reason)
        st.markdown(resp.clarification.question)
        cols = st.columns(len(resp.clarification.options) or 1)
        for i, option in enumerate(resp.clarification.options):
            cols[i].button(
                option,
                key=f"opt-{index}-{i}",
                on_click=ask,
                args=(f"{resp.evidence.question_as_understood} ({option})",),
            )
    elif resp.status is Status.NO_DATA:
        st.markdown(resp.answer)
        if resp.reason:
            st.markdown(resp.reason)
        chip_row(resp.assumptions)
    else:
        st.markdown(resp.reason or "Something went wrong.")
    if resp.suggestions:
        st.markdown("Try instead:")
        for i, s in enumerate(resp.suggestions):
            st.button(s, key=f"sug-{index}-{i}", on_click=ask, args=(s,))
    if resp.status is Status.ERROR:
        st.button("Retry", key=f"retry-{index}", on_click=ask, args=(question,))
    if (
        resp.status in (Status.ANSWERED, Status.NO_DATA)
        or resp.evidence.rejected_sql
        or resp.evidence.alignment.startswith("failed")
    ):
        evidence_panel(resp)
    nexts = follow_ups(resp, dataset_cfg)
    if nexts:
        st.markdown('<p class="dm-label">Ask next</p>', unsafe_allow_html=True)
        short = all(len(s) <= 40 for s in nexts)
        slots = st.columns(len(nexts)) if short else [st.container() for _ in nexts]
        for i, s in enumerate(nexts):
            slots[i].button(
                s,
                key=f"next-{index}-{i}",
                on_click=ask,
                args=(s,),
                width="stretch" if short else "content",
            )


dataset_cfg = None  # the selected dataset's config, set in main() before any card is drawn


def main() -> None:
    global dataset_cfg
    settings = get_settings()
    st.markdown(CSS, unsafe_allow_html=True)
    if "history" not in st.session_state:
        st.session_state.history = []
        st.session_state.conversation = Conversation()
        st.session_state.pending = None

    with st.sidebar:
        st.title("DataMind")
        st.caption("Ask questions about your Snowflake data in plain English.")
        names = available_datasets()
        default = names.index(settings.dataset) if settings.dataset in names else 0
        dataset = st.selectbox("Dataset", names, index=default)
        cfg = dataset_cfg = load_dataset(dataset)
        st.markdown("**Try asking**")
        for i, q in enumerate(cfg.starter_questions):
            st.button(q, key=f"starter-{i}", on_click=ask, args=(q,), width="stretch")
        if st.button("Clear conversation", width="stretch"):
            st.session_state.history = []
            st.session_state.conversation = Conversation()
        st.divider()
        data, model = connection_status(dataset)
        st.caption(f"Data: {data}")
        st.caption(f"Model: {model}")
        if settings.model.provider == "google":
            st.info("Hosted Gemma: questions and result rows are sent to Google.")

    if not st.session_state.history and not st.session_state.pending:
        st.markdown(hero(cfg.display_name), unsafe_allow_html=True)

    for i, (question, resp) in enumerate(st.session_state.history):
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            answer_card(resp, question, i)

    typed = st.chat_input("Ask a question about the data")
    question = typed or st.session_state.pending
    st.session_state.pending = None
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.status("Understanding the question, running the query, checking the answer"):
                resp = get_pipeline(dataset).ask(question, st.session_state.conversation)
            answer_card(resp, question, len(st.session_state.history))
        st.session_state.history.append((question, resp))


main()
