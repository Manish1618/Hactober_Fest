"""Query facts (FR-D1): what the executed SQL did, read from the parsed SQL by code, never by the
model. The explanation and the verifier both check against these facts.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.qualify import qualify

from datamind.pipeline.validator import DIALECT, Catalog

_OPS = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.Like: "LIKE",
    exp.ILike: "ILIKE",
}
_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "=": "=", "!=": "!="}


@dataclass
class Filter:
    column: str  # TABLE.COLUMN, or the expression text when it is not a plain column
    op: str
    values: list[str]
    text: str  # the condition as SQL

    def describe(self) -> str:
        col = self.column.split(".")[-1]
        if self.op == "IN":
            return f"{col} is one of {', '.join(self.values)}"
        if self.op == "BETWEEN":
            return f"{col} is between {self.values[0]} and {self.values[1]}"
        if self.op in _OPS.values() and self.values:
            return f"{col} {self.op} {self.values[0]}"
        return self.text


@dataclass
class QueryFacts:
    tables: list[str] = field(default_factory=list)
    joins: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    other_conditions: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    having: list[str] = field(default_factory=list)
    order_by: list[str] = field(default_factory=list)  # "revenue DESC"
    limit: int | None = None
    measures: list[str] = field(default_factory=list)  # aggregate expressions with their alias
    columns_used: list[str] = field(default_factory=list)
    date_range: tuple[str, str] | None = None  # inclusive first and last day
    date_column: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["filters"] = [f.describe() for f in self.filters]
        return d

    def lines(self) -> list[str]:
        """One plain fact per line, as given to the model and shown in the evidence."""
        out = [f"Tables: {', '.join(self.tables)}"]
        out += [f"Join: {j}" for j in self.joins]
        if self.date_range:
            out.append(
                f"Dates: {self.date_column} from {self.date_range[0]} to {self.date_range[1]}"
            )
        out += [f"Filter: {f.describe()}" for f in self.filters if f.column != self.date_column]
        out += [f"Condition: {c}" for c in self.other_conditions]
        out += [f"Measure: {m}" for m in self.measures]
        if self.group_by:
            out.append(f"Grouped by: {', '.join(self.group_by)}")
        out += [f"Having: {h}" for h in self.having]
        if self.order_by:
            out.append(f"Sorted by: {', '.join(self.order_by)}")
        if self.limit is not None:
            out.append(f"Row limit: {self.limit}")
        return out


def _sql(node: exp.Expression) -> str:
    return node.sql(dialect=DIALECT)


def _literal(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Literal):
        return node.this
    if isinstance(node, exp.Cast) and isinstance(node.this, exp.Literal):
        return node.this.this  # DATE '1995-01-01' or CAST('1995-01-01' AS DATE)
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        return "-" + node.this.this
    if isinstance(node, exp.Boolean):
        return "TRUE" if node.this else "FALSE"
    return None


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    node = node.unnest() if isinstance(node, exp.Paren) else node
    if isinstance(node, exp.And):
        return _conjuncts(node.left) + _conjuncts(node.right)
    return [node]


def _column_name(col: exp.Column, aliases: dict[str, str]) -> str:
    table = aliases.get(col.table.upper(), col.table.upper()) if col.table else ""
    return f"{table}.{col.name.upper()}" if table else col.name.upper()


def _parse_date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def extract_facts(
    sql: str,
    catalog: Catalog,
    date_columns: set[str] | None = None,
    limit_added: bool = False,
) -> QueryFacts:
    """Facts from validated SQL. `date_columns` are TABLE.COLUMN names typed DATE in the config.

    A LIMIT the validator added as a safety cap is not a fact about the question, so it is
    left out when `limit_added` is set.
    """
    facts = QueryFacts()
    tree = sqlglot.parse_one(sql, read=DIALECT)
    qualified = tree.copy()
    for t in qualified.find_all(exp.Table):
        t.set("db", None)
        t.set("catalog", None)
    try:
        qualified = qualify(
            qualified,
            schema=catalog.as_sqlglot_schema(),
            dialect=DIALECT,
            quote_identifiers=False,
        )
    except OptimizeError:
        pass  # facts from the unqualified tree are still useful
    ctes = {c.alias_or_name.upper() for c in qualified.find_all(exp.CTE)}
    aliases: dict[str, str] = {}
    for t in qualified.find_all(exp.Table):
        name = t.name.upper()
        aliases[t.alias_or_name.upper()] = name
        if name not in ctes and name not in facts.tables:
            facts.tables.append(name)

    used: list[str] = []
    for col in qualified.find_all(exp.Column):
        name = _column_name(col, aliases)
        if "." in name and name.split(".")[0] in catalog.tables and name not in used:
            used.append(name)
    facts.columns_used = used

    for join in qualified.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None:
            for c in _conjuncts(on):
                if isinstance(c, exp.EQ) and all(
                    isinstance(x, exp.Column) for x in (c.this, c.expression)
                ):
                    facts.joins.append(
                        f"{_column_name(c.this, aliases)} = {_column_name(c.expression, aliases)}"
                    )
                else:
                    facts.other_conditions.append(_sql(c))

    for where in qualified.find_all(exp.Where):
        for c in _conjuncts(where.this):
            _add_condition(c, aliases, facts)

    for select in qualified.find_all(exp.Select):  # includes CTEs and subqueries
        for e in select.expressions:
            if e.find(exp.AggFunc):
                inner = e.this if isinstance(e, exp.Alias) else e
                alias = e.alias if isinstance(e, exp.Alias) else ""
                named = alias and not alias.upper().startswith("_COL_")
                facts.measures.append(_unqualify(inner) + (f" AS {alias.lower()}" if named else ""))
        group = select.args.get("group")
        if group:
            facts.group_by += [_unqualify(g) for g in group.expressions]
        having = select.args.get("having")
        if having:
            facts.having += [_unqualify(h) for h in _conjuncts(having.this)]
    order = tree.args.get("order")
    if order:
        facts.order_by = [
            f"{_unqualify(o.this)} {'DESC' if o.args.get('desc') else 'ASC'}"
            for o in order.expressions
        ]
    limit = tree.args.get("limit")
    if (
        not limit_added
        and isinstance(limit, exp.Limit)
        and isinstance(limit.expression, exp.Literal)
    ):
        facts.limit = int(limit.expression.this)

    _date_range(facts, date_columns or set())
    return facts


def _unqualify(node: exp.Expression) -> str:
    node = node.copy()
    for col in node.find_all(exp.Column):
        col.set("table", None)
    return _sql(node)


def _add_condition(c: exp.Expression, aliases: dict[str, str], facts: QueryFacts) -> None:
    if isinstance(c, exp.EQ) and all(isinstance(x, exp.Column) for x in (c.this, c.expression)):
        facts.joins.append(
            f"{_column_name(c.this, aliases)} = {_column_name(c.expression, aliases)}"
        )
        return
    for kind, op in _OPS.items():
        if isinstance(c, kind):
            left, right = c.this, c.expression
            if not isinstance(left, exp.Column) and isinstance(right, exp.Column):
                left, right, op = right, left, _FLIP.get(op, op)
            value = _literal(right)
            if isinstance(left, exp.Column) and value is not None:
                facts.filters.append(Filter(_column_name(left, aliases), op, [value], _sql(c)))
                return
    if isinstance(c, exp.In) and isinstance(c.this, exp.Column):
        values = [_literal(v) for v in c.expressions]
        if values and all(v is not None for v in values):
            facts.filters.append(Filter(_column_name(c.this, aliases), "IN", values, _sql(c)))
            return
    if isinstance(c, exp.Between) and isinstance(c.this, exp.Column):
        low, high = _literal(c.args["low"]), _literal(c.args["high"])
        if low is not None and high is not None:
            facts.filters.append(
                Filter(_column_name(c.this, aliases), "BETWEEN", [low, high], _sql(c))
            )
            return
    facts.other_conditions.append(_unqualify(c))


def _date_range(facts: QueryFacts, date_columns: set[str]) -> None:
    """Inclusive first and last day from >=, >, <, <=, = and BETWEEN filters on one date column."""
    for f in facts.filters:
        if f.column not in date_columns:
            continue
        start = end = None
        for g in facts.filters:
            if g.column != f.column:
                continue
            days = [_parse_date(v) for v in g.values]
            if any(d is None for d in days):
                continue
            if g.op in (">=", "="):
                start = days[0]
            elif g.op == ">":
                start = days[0] + dt.timedelta(days=1)
            if g.op in ("<=", "="):
                end = days[0]
            elif g.op == "<":
                end = days[0] - dt.timedelta(days=1)
            elif g.op == "BETWEEN":
                start, end = days
        if start or end:
            facts.date_column = f.column
            facts.date_range = (
                start.isoformat() if start else "the start of the data",
                end.isoformat() if end else "the end of the data",
            )
            return
