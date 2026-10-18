"""The question-to-answer pipeline (PRD 8.1).

intake -> guard (code) -> understand (Gemma: rewrite + classify) -> draft SQL (Gemma)
-> validate (code) -> execute (Snowflake) -> repair up to 2 times -> answer (Gemma placeholders,
filled by code) -> response with evidence.

Query facts, the full number verifier and explanation checks arrive in phase 2.
"""

from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal

from datamind.config.models import DatasetConfig
from datamind.model.adapter import ModelAdapter, ModelError
from datamind.model.prompts import (
    ANSWER,
    DRAFT_SQL,
    UNDERSTAND,
    render_answer,
    render_draft_sql,
    render_understand,
)
from datamind.model.schemas import AnswerDraft, SqlDraft, Understanding
from datamind.pipeline.answer import (
    fill_placeholders,
    result_summary,
    stray_numbers,
    template_answer,
)
from datamind.pipeline.context import Conversation, Turn
from datamind.pipeline.guards import unsafe_reason
from datamind.pipeline.validator import Catalog, validate
from datamind.response import (
    EVIDENCE_ROWS,
    MAX_QUESTION_CHARS,
    Clarification,
    Evidence,
    Response,
    Status,
)
from datamind.snowflake_io.executor import ExecutionError, Executor, QueryResult

log = logging.getLogger("datamind")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MAX_REPAIRS = 2  # FR-B7
ANSWER_ROWS = 20  # rows shown to the model when phrasing the answer (NFR-15 caps at 50)
# Validator codes that mean the model tried to do something unsafe, not just made a mistake.
_UNSAFE_CODES = {"not_select", "denied_operation", "denied_function", "multiple_statements"}


def clean_question(text: str) -> str:
    return re.sub(r"\s+", " ", _CONTROL_CHARS.sub(" ", text or "")).strip()


def _cell(value):
    """JSON-friendly cell value for the evidence panel."""
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class Pipeline:
    def __init__(
        self,
        dataset: DatasetConfig,
        model: ModelAdapter,
        executor: Executor,
        max_rows: int = 1000,
        phrase_answers: bool = True,
    ):
        self.dataset = dataset
        self.model = model
        self.executor = executor
        self.max_rows = max_rows
        self.phrase_answers = phrase_answers
        self.catalog = Catalog(
            tables=dataset.catalog(),
            database=dataset.snowflake.database,
            schema=dataset.snowflake.schema_,
        )
        self._latest_date: str | None = None

    # ---- helpers -------------------------------------------------------------------------

    def latest_date(self) -> str:
        """Latest value of the configured time column; anchors relative dates (FR-A4)."""
        if self._latest_date is None:
            t = self.dataset.time
            self._latest_date = "unknown"
            if t:
                try:
                    r = self.executor.run(
                        f"SELECT MAX({t.anchor_column}) FROM {t.anchor_table}",
                        request_id="time-anchor",
                        max_rows=1,
                    )
                    if r.rows and r.rows[0][0] is not None:
                        self._latest_date = str(_cell(r.rows[0][0]))
                except ExecutionError as e:
                    log.warning("could not read latest date: %s", e)
        return self._latest_date

    def _draft_and_run(
        self, question: str, request_id: str, evidence: Evidence
    ) -> tuple[SqlDraft | None, QueryResult | None, str, str]:
        """Draft, validate and execute with up to MAX_REPAIRS repairs.

        Returns (draft, result, failure_code, failure_reason). result is None on failure.
        """
        failed: list[tuple[str, str]] = []
        draft: SqlDraft | None = None
        code, reason = "", ""
        for attempt in range(MAX_REPAIRS + 1):
            draft = self.model.generate(
                render_draft_sql(self.dataset, question, self.latest_date(), failed),
                SqlDraft,
                prompt_version=DRAFT_SQL,
            )
            checked = validate(draft.sql, self.catalog, max_rows=self.max_rows)
            if not checked.ok:
                code, reason = checked.code, checked.reason
                evidence.limits.append(f"Rejected SQL ({checked.code}): {checked.reason}")
                if code in _UNSAFE_CODES:
                    return draft, None, code, reason  # never "repair" an unsafe draft
                failed.append((draft.sql, reason))
                evidence.repair_attempts = attempt + 1 if attempt < MAX_REPAIRS else attempt
                continue
            evidence.sql = checked.sql
            evidence.tables = checked.tables
            try:
                result = self.executor.run(
                    checked.sql, request_id=request_id, max_rows=self.max_rows
                )
            except ExecutionError as e:
                code, reason = "execution_error", str(e)
                failed.append((checked.sql, reason))
                evidence.repair_attempts = attempt + 1 if attempt < MAX_REPAIRS else attempt
                continue
            if checked.limit_added and result.row_count >= self.max_rows:
                evidence.limits.append(f"Only the first {self.max_rows} rows were fetched.")
            return draft, result, "", ""
        return draft, None, code, reason

    def _phrase(self, question: str, columns: list[str], rows: list[list], n: int) -> str:
        fallback = template_answer(columns, rows, n)
        if not self.phrase_answers:
            return fallback
        try:
            out = self.model.generate(
                render_answer(question, columns, rows[:ANSWER_ROWS], n),
                AnswerDraft,
                prompt_version=ANSWER,
            )
        except ModelError as e:
            log.warning("answer phrasing failed, using template: %s", e)
            return fallback
        filled, missing = fill_placeholders(out.answer, columns, rows[:ANSWER_ROWS])
        if missing or stray_numbers(out.answer, question) or not filled.strip():
            # A number the model typed itself, or a bad reference: use the code-only answer.
            log.info("model answer rejected (missing=%s)", missing)
            return fallback
        return filled

    # ---- main entry ----------------------------------------------------------------------

    def ask(self, question: str, conversation: Conversation | None = None) -> Response:
        request_id = uuid.uuid4().hex[:16]
        q = clean_question(question)
        evidence = Evidence(
            question_as_understood=q,
            dataset=self.dataset.display_name,
            licence_note=self.dataset.licence_note,
            model=self.model.name,
            backend=self.executor.backend,
        )

        def done(status: Status, **kw) -> Response:
            resp = Response(request_id=request_id, status=status, evidence=evidence, **kw)
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "status": status.value,
                    "sql": evidence.sql,
                    "rows": evidence.rows_returned,
                    "model": evidence.model,
                    "prompt_versions": evidence.prompt_versions,
                },
            )
            if conversation is not None:
                conversation.add(
                    Turn(
                        question=q,
                        standalone_question=evidence.question_as_understood,
                        status=status.value,
                        sql=evidence.sql,
                        result_summary=result_summary(
                            evidence.columns, evidence.rows, evidence.rows_returned
                        )
                        if evidence.query_id
                        else "",
                    )
                )
            return resp

        # 1. Intake
        if not q:
            return done(Status.UNSUPPORTED, reason="Please type a question.")
        if len(q) > MAX_QUESTION_CHARS:
            return done(
                Status.UNSUPPORTED,
                reason=f"Questions are limited to {MAX_QUESTION_CHARS} characters.",
            )

        # 2. Code guard: write requests never reach the model or Snowflake.
        unsafe = unsafe_reason(q)
        if unsafe:
            log.warning("blocked request %s: %s", request_id, unsafe)
            return done(Status.BLOCKED, reason=unsafe)

        # 3. Understand: rewrite follow-ups and classify (Gemma).
        history = conversation.history_text() if conversation else ""
        try:
            evidence.prompt_versions.append(UNDERSTAND)
            u = self.model.generate(
                render_understand(self.dataset, q, history),
                Understanding,
                prompt_version=UNDERSTAND,
            )
        except ModelError as e:
            return done(Status.ERROR, reason=f"The language model could not help: {e}")

        standalone = clean_question(u.standalone_question) or q
        evidence.question_as_understood = standalone
        if u.used_context and conversation:
            last = conversation.turns[-1]
            evidence.context_used = f"Previous question: {last.standalone_question}"

        # The rewritten question gets the same code guard as the original.
        unsafe = unsafe_reason(standalone)
        if u.category == "unsafe" or unsafe:
            return done(Status.BLOCKED, reason=unsafe or u.reason or "This request is not allowed.")
        if u.category == "follow_up_unclear" or (u.used_context and not history):
            return done(
                Status.CLARIFY,
                reason="This looks like a follow-up, but there is no earlier question to build on.",
                clarification=Clarification(
                    question="What would you like to know? Please ask the full question.",
                    options=self.dataset.starter_questions[:3],
                ),
            )
        if u.category == "ambiguous":
            options = [o for o in u.options if o.strip()][:4]
            if len(options) >= 2:
                return done(
                    Status.CLARIFY,
                    reason=u.reason,
                    clarification=Clarification(
                        question=u.clarifying_question or "Which did you mean?", options=options
                    ),
                )
            # No usable options: answer with the most common reading instead of asking badly.
        if u.category == "unsupported":
            suggestions = [u.alternative] if u.alternative else []
            return done(
                Status.UNSUPPORTED,
                reason=u.reason or "The data cannot answer this question.",
                suggestions=suggestions,
            )

        # 4-7. Draft, validate, execute, repair.
        evidence.data_latest_date = self.latest_date()
        evidence.prompt_versions.append(DRAFT_SQL)
        try:
            draft, result, code, reason = self._draft_and_run(standalone, request_id, evidence)
        except ModelError as e:
            return done(Status.ERROR, reason=f"The language model could not help: {e}")
        assumptions = draft.assumptions if draft else []
        if result is None:
            if code in _UNSAFE_CODES:
                return done(Status.BLOCKED, reason=reason)
            return done(
                Status.ERROR,
                reason="I could not build a working query for this question "
                f"after {MAX_REPAIRS} repair attempts. Last error: {reason}",
                assumptions=assumptions,
            )

        evidence.query_id = result.query_id
        evidence.elapsed_seconds = result.elapsed_seconds
        evidence.executed_at = result.executed_at
        evidence.columns = result.columns
        evidence.rows_returned = result.row_count
        evidence.rows = [[_cell(v) for v in row] for row in result.rows[:EVIDENCE_ROWS]]
        evidence.rows_shown = len(evidence.rows)
        if result.truncated:
            evidence.limits.append(f"Only the first {self.max_rows} rows were fetched.")
        if evidence.rows_shown < result.row_count:
            evidence.limits.append(f"Showing {evidence.rows_shown} of {result.row_count} rows.")

        if result.row_count == 0:
            return done(
                Status.NO_DATA,
                answer="The query ran but no rows matched.",
                assumptions=assumptions,
            )

        # 9. Answer: Gemma phrases with placeholders; code fills every number.
        evidence.prompt_versions.append(ANSWER)
        answer = self._phrase(standalone, result.columns, evidence.rows, result.row_count)
        return done(
            Status.ANSWERED,
            answer=answer,
            explanation="Plain-language explanation from query facts arrives in phase 2.",
            assumptions=assumptions,
        )
