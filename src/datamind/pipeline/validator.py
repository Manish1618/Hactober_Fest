"""SQL safety validator (FR-B2 to FR-B5, PRD 8.7).

Parses model-drafted SQL with sqlglot's Snowflake dialect and allows exactly one read-only
query over known tables and columns. Every rejection carries a plain reason that can be shown
to the user or fed back to the model for repair. This is one of two independent safety layers;
the other is the read-only Snowflake role.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

DIALECT = "snowflake"
DEFAULT_MAX_ROWS = 1000

# sqlglot logs a warning when it falls back to an opaque Command; we reject those anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Any of these anywhere in the tree means the statement can change something.
_DENIED_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Undrop",
        "Copy",
        "Put",
        "Get",
        "Use",
        "Set",
        "Command",
        "Into",
        "Grant",
        "Revoke",
        "Transaction",
        "Commit",
        "Rollback",
        "Describe",
        "Show",
        "Pragma",
        "LoadData",
    )
    if hasattr(exp, name)
)

# Read-only in themselves but can reach outside the catalog or other users' results.
_DENIED_FUNCTIONS = {"RESULT_SCAN", "LAST_QUERY_ID", "IDENTIFIER", "TABLE", "GETVARIABLE"}
_DENIED_FUNCTION_PREFIXES = ("SYSTEM$",)


@dataclass
class ValidationResult:
    ok: bool
    sql: str = ""
    reason: str = ""
    code: str = ""
    tables: list[str] = field(default_factory=list)
    limit_added: bool = False
    limit: int | None = None

    @classmethod
    def reject(cls, code: str, reason: str) -> ValidationResult:
        return cls(ok=False, code=code, reason=reason)


@dataclass
class Catalog:
    """Tables and columns the active role may query, plus where they live."""

    tables: dict[str, set[str]]
    database: str | None = None
    schema: str | None = None

    def __post_init__(self) -> None:
        self.tables = {t.upper(): {c.upper() for c in cols} for t, cols in self.tables.items()}
        self.database = self.database.upper() if self.database else None
        self.schema = self.schema.upper() if self.schema else None

    def as_sqlglot_schema(self) -> dict[str, dict[str, str]]:
        return {t: {c: "VARCHAR" for c in cols} for t, cols in self.tables.items()}


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).upper()
    return node.sql_name().upper()


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name.upper() for cte in tree.find_all(exp.CTE)}


def _literal_int(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.this)
        except ValueError:
            return None
    return None


def validate(sql: str, catalog: Catalog, max_rows: int = DEFAULT_MAX_ROWS) -> ValidationResult:
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return ValidationResult.reject("empty", "No SQL was produced.")

    # 1. Parse, exactly one statement.
    try:
        statements = [s for s in sqlglot.parse(text, read=DIALECT) if s is not None]
    except ParseError as e:
        return ValidationResult.reject("parse_error", f"The SQL could not be parsed: {e}")
    if len(statements) != 1:
        return ValidationResult.reject(
            "multiple_statements", "Only one SQL statement is allowed per question."
        )
    tree = statements[0]

    # 2. Read-only query only (SELECT, optionally WITH, or a set operation of SELECTs).
    if not isinstance(tree, exp.Query):
        kind = type(tree).__name__.upper()
        return ValidationResult.reject(
            "not_select", f"Only read-only SELECT queries are allowed; got a {kind} statement."
        )
    for node in tree.walk():
        if isinstance(node, _DENIED_NODES):
            kind = type(node).__name__.upper()
            return ValidationResult.reject(
                "denied_operation", f"The query contains a {kind} operation, which is not allowed."
            )

    # 3. Denied functions and table functions (session, system, cross-query access).
    for fn in tree.find_all(exp.Func):
        name = _function_name(fn)
        if name in _DENIED_FUNCTIONS or name.startswith(_DENIED_FUNCTION_PREFIXES):
            return ValidationResult.reject(
                "denied_function", f"The function {name} is not allowed."
            )
    if any(True for _ in tree.find_all(exp.TableFromRows)):
        return ValidationResult.reject("denied_function", "Table functions are not allowed.")

    # 4. Every table must be in the role's catalog (CTE names are local and fine).
    ctes = _cte_names(tree)
    used: list[str] = []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            return ValidationResult.reject(
                "unknown_table", f"{table.sql(DIALECT)} is not a table you can query."
            )
        name = table.name.upper()
        if name in ctes and not table.args.get("db"):
            continue
        db = table.args.get("db")
        catalog_part = table.args.get("catalog")
        if db is not None and catalog.schema and db.name.upper() != catalog.schema:
            return ValidationResult.reject(
                "unknown_table", f"{table.sql(DIALECT)} is outside this dataset."
            )
        if (
            catalog_part is not None
            and catalog.database
            and (catalog_part.name.upper() != catalog.database)
        ):
            return ValidationResult.reject(
                "unknown_table", f"{table.sql(DIALECT)} is outside this dataset."
            )
        if name not in catalog.tables:
            return ValidationResult.reject(
                "unknown_table", f"There is no table called {name} in this dataset."
            )
        if name not in used:
            used.append(name)
    if not used:
        return ValidationResult.reject("no_table", "The query does not read from any table.")

    # 5. Every column must resolve against the catalog.
    check = tree.copy()
    for table in check.find_all(exp.Table):
        table.set("db", None)
        table.set("catalog", None)
    try:
        qualify(
            check,
            schema=catalog.as_sqlglot_schema(),
            dialect=DIALECT,
            validate_qualify_columns=True,
        )
    except OptimizeError as e:
        return ValidationResult.reject("unknown_column", f"Unknown column or reference: {e}")

    # 6. Enforce the row limit: add one if absent, cap one that is too large.
    limit_node = tree.args.get("limit")
    limit_added = False
    if limit_node is None:
        tree = tree.limit(max_rows)
        limit, limit_added = max_rows, True
    else:
        value = _literal_int(limit_node.expression if isinstance(limit_node, exp.Limit) else None)
        if value is None:
            return ValidationResult.reject("bad_limit", "LIMIT must be a plain whole number.")
        if value > max_rows:
            tree = tree.limit(max_rows)
            value = max_rows
        limit = value

    return ValidationResult(
        ok=True,
        sql=tree.sql(dialect=DIALECT, pretty=True),
        tables=used,
        limit_added=limit_added,
        limit=limit,
    )
