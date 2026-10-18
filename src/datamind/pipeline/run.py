"""The question-to-answer pipeline.

Phase 0 wiring: intake, SQL draft (model), validation, execution, and a template answer built
by code from the result rows. Classification, repair, query facts, model-phrased answers and
the number verifier arrive in phases 1 and 2.
"""

from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal

from datamind.config.models import DatasetConfig
from datamind.model.adapter import ModelAdapter, ModelError
from datamind.model.prompts import DRAFT_SQL, render_draft_sql
from datamind.model.schemas import SqlDraft
from datamind.pipeline.validator import Catalog, validate
from datamind.response import EVIDENCE_ROWS, MAX_QUESTION_CHARS, Evidence, Response, Status
from datamind.snowflake_io.executor import ExecutionError, Executor

log = logging.getLogger("datamind")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


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
    ):
        self.dataset = dataset
        self.model = model
        self.executor = executor
        self.max_rows = max_rows
        self.catalog = Catalog(
            tables=dataset.catalog(),
            database=dataset.snowflake.database,
            schema=dataset.snowflake.schema_,
        )

    def ask(self, question: str) -> Response:
        request_id = uuid.uuid4().hex[:16]
        q = clean_question(question)
        evidence = Evidence(
            question_as_understood=q,
            dataset=self.dataset.display_name,
            licence_note=self.dataset.licence_note,
            model=self.model.name,
            prompt_version=DRAFT_SQL,
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
                    "prompt_version": evidence.prompt_version,
                },
            )
            return resp

        if not q:
            return done(Status.UNSUPPORTED, reason="Please type a question.")
        if len(q) > MAX_QUESTION_CHARS:
            return done(
                Status.UNSUPPORTED,
                reason=f"Questions are limited to {MAX_QUESTION_CHARS} characters.",
            )

        try:
            draft = self.model.generate(
                render_draft_sql(self.dataset, q), SqlDraft, prompt_version=DRAFT_SQL
            )
        except ModelError as e:
            return done(Status.ERROR, reason=f"The language model could not help: {e}")

        checked = validate(draft.sql, self.catalog, max_rows=self.max_rows)
        if not checked.ok:
            evidence.limits.append(f"Rejected SQL ({checked.code}): {checked.reason}")
            return done(Status.BLOCKED, reason=checked.reason)

        evidence.sql = checked.sql
        evidence.tables = checked.tables
        try:
            result = self.executor.run(checked.sql, request_id=request_id, max_rows=self.max_rows)
        except ExecutionError as e:
            return done(Status.ERROR, reason=str(e))

        evidence.query_id = result.query_id
        evidence.elapsed_seconds = result.elapsed_seconds
        evidence.executed_at = result.executed_at
        evidence.columns = result.columns
        evidence.rows_returned = result.row_count
        evidence.rows = [[_cell(v) for v in row] for row in result.rows[:EVIDENCE_ROWS]]
        evidence.rows_shown = len(evidence.rows)
        if result.truncated or (checked.limit_added and result.row_count >= self.max_rows):
            evidence.limits.append(f"Only the first {self.max_rows} rows were fetched.")
        if evidence.rows_shown < result.row_count:
            evidence.limits.append(f"Showing {evidence.rows_shown} of {result.row_count} rows.")

        if result.row_count == 0:
            return done(
                Status.NO_DATA,
                answer="The query ran but no rows matched.",
                assumptions=draft.assumptions,
            )
        return done(
            Status.ANSWERED,
            answer=self._template_answer(result.columns, evidence.rows, result.row_count),
            explanation="Phase 0 placeholder: plain-language explanation arrives in phase 2.",
            assumptions=draft.assumptions,
        )

    @staticmethod
    def _template_answer(columns: list[str], rows: list[list], row_count: int) -> str:
        # Numbers come straight from result cells; nothing here is model-written.
        if row_count == 1 and len(columns) == 1:
            return f"{columns[0]}: {rows[0][0]}"
        if row_count == 1:
            return "; ".join(f"{c}: {v}" for c, v in zip(columns, rows[0], strict=True))
        return f"{row_count} rows returned. See the result table in the evidence."
