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
        from datamind.snowflake_io.fixture import build_fixture

        executor = LocalExecutor(build_fixture())
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
            st.markdown(f"**Query ID:** `{ev.query_id}`")
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
        if ev.verification:
            st.markdown(f"**Answer check:** {ev.verification}")
        for limit in ev.limits:
            st.markdown(f"**Limit:** {limit}")
        st.download_button(
            "Download evidence (JSON)",
            json.dumps(resp.model_dump(mode="json"), indent=2),
            file_name=f"datamind-{resp.request_id}.json",
            mime="application/json",
            key=f"dl-{resp.request_id}",
        )


def answer_card(resp: Response, question: str, index: int) -> None:
    label, colour = STATUS_LABEL[resp.status]
    st.markdown(f":{colour}-badge[{label}]")
    if resp.status is Status.ANSWERED:
        st.markdown(resp.answer)
        chip_row(resp.assumptions)
        if resp.explanation:
            st.caption(resp.explanation)
    elif resp.status is Status.CLARIFY and resp.clarification:
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
    if resp.status in (Status.ANSWERED, Status.NO_DATA) or resp.evidence.rejected_sql:
        evidence_panel(resp)


def main() -> None:
    settings = get_settings()
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
        cfg = load_dataset(dataset)
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
        if settings.backend == "local":
            st.warning("Local test data: numbers are synthetic, not from Snowflake.")
        if settings.model.provider == "google":
            st.info("Hosted Gemma: questions and result rows are sent to Google.")

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
