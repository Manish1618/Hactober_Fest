"""Writers for the data-change test (T8, FR-K5). Only the evaluation runner uses them.

A writer restores the demo tables from their base copies and applies a test's change_sql. Each
statement must be a plain INSERT, UPDATE or DELETE on a table of the demo dataset; anything else
is refused before it runs. The pipeline itself never writes: it still reads as DATAMIND_READER.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from datamind.config.models import DatasetConfig
from datamind.settings import SnowflakeSettings
from datamind.snowflake_io.executor import ExecutionError, SnowflakeExecutor

_WRITES = (exp.Insert, exp.Update, exp.Delete)


def check_change(sql: str, tables: set[str]) -> None:
    """Refuse anything but a single INSERT, UPDATE or DELETE on the demo tables."""
    statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    if len(statements) != 1 or not isinstance(statements[0], _WRITES):
        raise ExecutionError(f"change_sql must be one INSERT, UPDATE or DELETE: {sql!r}")
    touched = {t.name.upper() for t in statements[0].find_all(exp.Table)}
    if not touched or touched - tables:
        raise ExecutionError(f"change_sql may only touch {sorted(tables)}: {sql!r}")


class LocalWriter:
    """Changes the offline DuckDB fixture shared with the LocalExecutor."""

    def __init__(self, connection, cfg: DatasetConfig):
        self._conn = connection
        self.tables = set(cfg.tables)
        for t in sorted(self.tables):
            self._conn.execute(f"CREATE TABLE IF NOT EXISTS {t}_BASE AS SELECT * FROM {t}")

    def reset(self) -> None:
        for t in sorted(self.tables):
            self._conn.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM {t}_BASE")

    def apply(self, statements: list[str]) -> None:
        for sql in statements:
            check_change(sql, self.tables)
            self._conn.execute(sqlglot.transpile(sql, read="snowflake", write="duckdb")[0])


class SnowflakeWriter:
    """Changes DATAMIND_DB.DEMO under the loader role. The tables are re-cloned from *_BASE."""

    def __init__(self, settings: SnowflakeSettings, cfg: DatasetConfig, role: str):
        location = cfg.snowflake.model_copy(update={"role": role})
        loader = cfg.model_copy(update={"snowflake": location})
        self._executor = SnowflakeExecutor(settings, loader)
        self.tables = set(cfg.tables)

    def _run(self, sql: str) -> None:
        self._executor.run(sql, request_id="datachange-test", max_rows=1)

    def reset(self) -> None:
        for t in sorted(self.tables):
            self._run(f"CREATE OR REPLACE TABLE {t} CLONE {t}_BASE COPY GRANTS")

    def apply(self, statements: list[str]) -> None:
        for sql in statements:
            check_change(sql, self.tables)
            self._run(sql)
