"""Zero-dependency Text2SQL hybrid routing (Phase 4).

Borrows the *intellectual property* of LangChain's SQL path — the
``SQLITE_PROMPT`` template idea, DDL + 3 sample rows schema rendering
(``SQLDatabase.get_table_info``), and the QUERY_CHECKER error checklist —
as plain text and small functions. Adds the hard guardrails LangChain never
had (SELECT-only validation, server-side LIMIT, statement timeout) and a
self-built intent router.

Deliberately NOT introduced: any ``langchain*`` dependency, the legacy
``AgentExecutor`` multi-round loop, or a second RAG engine. One-shot LLM
SQL generation is enough because FormuMind's table layout is fixed.

Isolation is structural, not textual (round-5): the generated statement never runs on the live database. The rows the
caller may see — one project's, and in multi-user mode the ones the caller owns — are copied into a private in-memory
SQLite database that holds nothing else, and the statement runs only there (see ``open_snapshot``). The earlier guard
looked for ``project_id = '<id>'`` in the SQL text: advice the model could ignore and a regex ``OR 1 = 1`` satisfies.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Iterator, Sequence

from pydantic import BaseModel, Field
from sqlalchemy import Column, MetaData, Table, inspect, text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateTable

from ..db.database import make_engine  # noqa: F401  (re-export for tests/consumers)
from ..db.models import DOEPlanRow, ExperimentRow, FormulationVersion, MeasurementRow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Whitelist: only experiment/formulation tables are queryable.
# ---------------------------------------------------------------------------

ALLOWED_TABLES: tuple[str, ...] = (
    "experiments",
    "measurements",
    "formulation_versions",
    "doe_plans",
)

_TABLE_MODELS = {
    "experiments": ExperimentRow,
    "measurements": MeasurementRow,
    "formulation_versions": FormulationVersion,
    "doe_plans": DOEPlanRow,
}

DEFAULT_TOP_K = 50
SQL_TIMEOUT_S = 10.0
SCHEMA_SAMPLE_ROWS = 3

# DML/DDL and anything that escapes the sandbox. Checked after stripping
# string literals so a label like 'delete me' does not false-positive. ``REPLACE(`` is the string function every
# cleanup query uses, not ``REPLACE INTO``; the engine refuses writes regardless (authorizer, read-only snapshot).
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE(?!\s*\()|ATTACH|DETACH"
    r"|PRAGMA|VACUUM|GRANT|REVOKE|COPY|CALL|EXECUTE)\b",
    re.IGNORECASE,
)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_LIMIT_CLAUSE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)


class Text2SQLError(ValueError):
    """Raised when generated SQL fails a hard guardrail."""


class _GeneratedSQL(BaseModel):
    sql: str = Field(description="Single SQLite SELECT statement")


# ---------------------------------------------------------------------------
# 1. What a statement may see: a private, read-only snapshot of one scope
# ---------------------------------------------------------------------------

DEFAULT_OWNER = "default"  # what ``get_current_owner`` answers in single-user mode: no identity, nothing to restrict

# More rows than this in one scope and the question falls back to the literature path instead of querying a copy that
# would be slow to build; a project is the unit people ask about, so only a deployment that queries "everything" at
# scale meets it.
SNAPSHOT_MAX_ROWS = 250_000
_COPY_CHUNK = 2_000
# SQLite's longest string/blob: the default is 1 GB, so ``hex(zeroblob(500000000))`` would be a one-line way to exhaust
# memory inside the statement timeout. Rows are copied before this applies.
_MAX_VALUE_BYTES = 8_000_000
_SAMPLE_VALUE_CHARS = 200


@dataclass(frozen=True)
class Scope:
    """Whose rows a question may read.

    ``project_id`` empty means every project. ``owner_id`` ``None`` (an internal call with no identity) or ``"default"``
    (single-user mode) means no owner restriction — ``assert_owner``'s rule; any other value, even an empty one, is an
    identity: a row is visible when it has no owner or that identity owns it.
    """

    project_id: str | None = None
    owner_id: str | None = None

    @property
    def project(self) -> str | None:
        return str(self.project_id) if self.project_id else None

    @property
    def owner(self) -> str | None:
        return None if self.owner_id is None or self.owner_id == DEFAULT_OWNER else str(self.owner_id)


def _owner_visible(alias: str) -> str:
    return f"({alias}.owner_id IS NULL OR {alias}.owner_id = '' OR {alias}.owner_id = :owner)"


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _select_for(table: str, columns: Sequence[str], scope: Scope, present: set[str]) -> tuple[str, dict[str, str]]:
    """The query that reads ``table``'s in-scope rows from the live database (identifiers come from the models).

    Scope rules, in one place:

    * ``experiments`` — ``project_id`` and the owner.
    * ``measurements`` — carry neither; they inherit both through their experiment.
    * ``doe_plans`` — the plan's own ``project_id``, else its experiment's, else its campaign's; hidden when either
      parent belongs to someone else. (Plans are usually written with a campaign and no experiment, so reading the
      project only through ``experiments`` would show none of them.)
    * ``formulation_versions`` — ``project_id`` only: the table has no owner column.
    """
    where: list[str] = []
    joins = ""
    alias = {"experiments": "e", "measurements": "m", "formulation_versions": "f", "doe_plans": "p"}[table]
    scoped = scope.project is not None or scope.owner is not None
    if table == "experiments":
        if scope.project:
            where.append("e.project_id = :pid")
        if scope.owner is not None:
            where.append(_owner_visible("e"))
    elif table == "measurements":
        if scoped:
            joins = " JOIN experiments AS e ON e.id = m.experiment_id"
            if scope.project:
                where.append("e.project_id = :pid")
            if scope.owner is not None:
                where.append(_owner_visible("e"))
    elif table == "formulation_versions":
        if scope.project:
            where.append("f.project_id = :pid")
    else:  # doe_plans
        if scoped:
            with_campaigns = "campaigns" in present
            joins = " LEFT JOIN experiments AS e ON e.id = p.experiment_id"
            if with_campaigns:
                joins += " LEFT JOIN campaigns AS c ON c.id = p.campaign_id"
            if scope.project:
                project_of = ["NULLIF(p.project_id, '')", "NULLIF(e.project_id, '')"]
                if with_campaigns:
                    project_of.append("NULLIF(c.project_id, '')")
                where.append(f"COALESCE({', '.join(project_of)}) = :pid")
            if scope.owner is not None:
                where.append(_owner_visible("e"))
                if with_campaigns:
                    where.append(_owner_visible("c"))
    sql = f"SELECT {', '.join(f'{alias}.{_q(c)}' for c in columns)} FROM {_q(table)} AS {alias}{joins}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    params = {"pid": scope.project, "owner": scope.owner}
    return sql, {k: v for k, v in params.items() if v is not None and f":{k}" in sql}


def _bare_ddl(model: Table) -> str:
    """``CREATE TABLE`` with names and types only, for the snapshot's own tables.

    NOT NULL / UNIQUE / foreign keys guard *writes*, and the snapshot is only read — while a legacy row written before a
    column became NOT NULL, or a child whose parent is out of scope, must still be copyable. (The prompt keeps showing
    the model's full DDL, constraints included: that is what tells the model how the tables relate.)
    """
    bare = Table(model.name, MetaData(), *(Column(c.name, c.type.copy()) for c in model.columns))
    return str(CreateTable(bare).compile(dialect=sqlite_dialect.dialect())).strip()


def _adapt(value: Any) -> Any:
    """A value as SQLite stores it, whichever database it was read from (SQLAlchemy's SQLite formats for dates)."""
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


# Functions that reach outside the database (extension loading, file access). Everything else is
# plain SQL arithmetic / string / date work the generated queries legitimately use.
_FORBIDDEN_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})


def _make_authorizer(denied: list[str]) -> Callable[..., int]:
    """SQLite authorizer: no writes, no schema access, no functions that leave the database.

    ``validate_select_only`` is a text filter on what the model wrote; this is the engine itself refusing. The
    snapshot holds only the whitelisted tables, so there is no other table to protect — what remains is the schema
    (``sqlite_master`` and the ``pragma_*`` table-valued functions), writes, and the functions above.
    """

    def authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _source: str | None) -> int:
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            if table.startswith(("sqlite_", "pragma_")):
                denied.append(f"table {arg1}")
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if (arg2 or "").lower() in _FORBIDDEN_FUNCTIONS:
                denied.append(f"function {arg2}")
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        denied.append(f"operation {action}")
        return sqlite3.SQLITE_DENY

    return authorize


@dataclass
class Snapshot:
    """An in-memory copy of one scope's rows, and the only place generated SQL runs."""

    conn: sqlite3.Connection
    scope: Scope
    ddl: dict[str, str] = field(default_factory=dict)  # table -> the model's full CREATE TABLE, as shown to the LLM
    counts: dict[str, int] = field(default_factory=dict)

    def run(self, sql: str, *, max_rows: int = DEFAULT_TOP_K, timeout_s: float = SQL_TIMEOUT_S) -> list[dict]:
        """Execute ``sql`` here, fetching included in the time limit (a streaming query does its work while fetching)."""
        deadline = time.monotonic() + timeout_s
        denied: list[str] = []
        self.conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        self.conn.set_authorizer(_make_authorizer(denied))
        try:
            cursor = self.conn.execute(sql)
            rows = cursor.fetchmany(max_rows)
            cols = [d[0] for d in (cursor.description or [])]
        except sqlite3.DatabaseError as exc:
            # The message differs by SQLite version ("not authorized" / "access to t.c is prohibited"); the
            # callback's own record is the reliable signal.
            if denied:
                raise Text2SQLError(f"query touches something outside the whitelist: {denied[0]}") from exc
            message = str(exc)
            if "interrupted" in message.lower():
                raise Text2SQLError(f"SQL timed out after {timeout_s}s") from exc
            if message.lower().startswith("no such table"):
                raise Text2SQLError(f"table not available to Text2SQL: {message.rsplit(':', 1)[-1].strip()}") from exc
            raise
        finally:
            self.conn.set_authorizer(None)
            self.conn.set_progress_handler(None, 0)
        return [dict(zip(cols, r)) for r in rows]

    def sample(self, table: str, n: int) -> list[dict]:
        cursor = self.conn.execute(f"SELECT * FROM {_q(table)} LIMIT ?", (n,))
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in cursor.fetchall()]


def _populate(engine: Engine, snap: Snapshot) -> None:
    insp = inspect(engine)
    present = set(insp.get_table_names())
    budget = SNAPSHOT_MAX_ROWS
    with engine.connect() as src:
        for table in ALLOWED_TABLES:
            if table not in present:
                continue
            model = _TABLE_MODELS[table].__table__
            have = {c["name"] for c in insp.get_columns(table)}
            columns = [c.name for c in model.columns if c.name in have]
            snap.conn.execute(_bare_ddl(model))
            snap.ddl[table] = str(CreateTable(model).compile(dialect=sqlite_dialect.dialect())).strip()
            select_sql, params = _select_for(table, columns, snap.scope, present)
            insert = f"INSERT INTO {_q(table)} ({', '.join(_q(c) for c in columns)}) VALUES ({', '.join('?' * len(columns))})"
            result = src.execute(text(select_sql), params)
            copied = 0
            while True:
                chunk = result.fetchmany(_COPY_CHUNK)
                if not chunk:
                    break
                copied += len(chunk)
                budget -= len(chunk)
                if budget < 0:
                    raise Text2SQLError(
                        f"more than {SNAPSHOT_MAX_ROWS} rows in scope: too many to query in one go, choose a project"
                    )
                snap.conn.executemany(insert, [tuple(_adapt(v) for v in row) for row in chunk])
            snap.counts[table] = copied
    snap.conn.commit()


@contextmanager
def open_snapshot(engine: Engine, scope: Scope | None = None) -> Iterator[Snapshot]:
    """Copy ``scope``'s rows from the live database into a private in-memory SQLite and yield it; closed on exit.

    Whatever SQL runs on the snapshot, it cannot read a row that was not copied: other projects, other owners and every
    table outside the whitelist simply are not there. It also cannot reach the live database — no connection to it
    exists inside the snapshot, and ``ATTACH`` is switched off (``SQLITE_LIMIT_ATTACHED = 0``) besides being refused by
    the authorizer and the keyword filter.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = OFF")  # the copy is partial by design: parents may be out of scope
        snap = Snapshot(conn=conn, scope=scope or Scope())
        _populate(engine, snap)
        log.debug("text2sql snapshot (project=%s, owner=%s): %s", snap.scope.project, snap.scope.owner, snap.counts)
        conn.execute("PRAGMA query_only = ON")
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_VALUE_BYTES)
        yield snap
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema rendering (DDL + N sample rows per table)
# ---------------------------------------------------------------------------


def _shorten(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _SAMPLE_VALUE_CHARS:
        return value[:_SAMPLE_VALUE_CHARS] + "…"
    return value


def _render_schema(snap: Snapshot, sample_rows: int) -> str:
    blocks: list[str] = []
    for table_name in ALLOWED_TABLES:
        ddl = snap.ddl.get(table_name)
        if ddl is None:
            continue
        rows = snap.sample(table_name, sample_rows)
        sample = "\n".join(str({k: _shorten(v) for k, v in r.items()}) for r in rows) or "(empty)"
        blocks.append(f"{ddl};\n-- sample rows ({table_name}):\n{sample}")
    return "\n\n".join(blocks)


def render_schema(
    engine: Engine,
    sample_rows: int = SCHEMA_SAMPLE_ROWS,
    *,
    project_id: str | None = None,
    owner_id: str | None = None,
    snapshot: Snapshot | None = None,
) -> str:
    """Render whitelisted tables as DDL + sample rows for the prompt.

    The sample rows come from the same snapshot the query will run on, so they cannot show what the query could not
    read: they go to the LLM, and can be echoed in its answer. Long values (a DOE plan's parameters, a formulation's
    snapshot) are cut so one row cannot fill the prompt.
    """
    if snapshot is not None:
        return _render_schema(snapshot, sample_rows)
    with open_snapshot(engine, Scope(project_id, owner_id)) as snap:
        return _render_schema(snap, sample_rows)


# ---------------------------------------------------------------------------
# 2. SQLite-specific prompt (borrows SQLITE_PROMPT ideas: LIMIT + date('now'))
# ---------------------------------------------------------------------------

_SQLITE_SYSTEM = (
    "你是 SQLite SQL 生成器。只输出一条 SQL 语句，不要解释、不要 markdown 代码块、不要注释。\n"
    "数据库是 SQLite。可用表结构如下：\n{schema}\n\n"
    "规则：\n"
    "1. 只允许 SELECT 查询，禁止任何增删改、DDL 或 PRAGMA。\n"
    "2. 如果问题问\"前 N\"，用 LIMIT N；否则默认 LIMIT {top_k}。\n"
    "3. 相对日期（如\"上个月\"）用 SQLite 日期函数表达，例如 date('now', '-1 month')。\n"
    "4. 表名和列名必须来自上面的表结构，不要编造不存在的列。\n"
    "5. 需要跨表时用 JOIN，measurements.experiment_id 关联 experiments.id。\n"
    "6. 输出纯 SQL 文本，以分号结尾或不带分号均可。\n"
    "{project_scope_rule}"
)


def build_sqlite_prompt(
    question: str, schema_text: str, top_k: int = DEFAULT_TOP_K,
    project_id: str | None = None,
) -> tuple[str, str]:
    """Return (system, user) prompt pair for one-shot SQL generation."""
    # Isolation is the snapshot's job, not the prompt's: the database the statement runs on holds only this project's
    # rows. Telling the model so also stops it from adding joins to experiments just to filter measurements by project.
    scope_rule = (
        "7. 数据库里只有当前项目（及当前用户可见）的数据：不需要、也不要再按 project_id 或 owner_id 过滤。\n"
        if project_id
        else ""
    )
    system = _SQLITE_SYSTEM.format(
        schema=schema_text, top_k=top_k, project_scope_rule=scope_rule
    )
    return system, f"问题：{question}\nSQL:"


# One question must not wait on a slow provider for minutes: the route is consulted for every chat question that
# looks structured, and the literature answer is the fallback. (Chat caps its main LLM call at 45 s the same way.)
SQL_GENERATION_DEADLINE_S = 20.0


def _default_complete(system: str, user: str) -> str | None:
    """Default LLM call via the platform's structured completion, with a wall-clock deadline."""
    from .llm import _call_with_deadline, complete_structured

    def _generate() -> str | None:
        parsed, err = complete_structured(system, user, _GeneratedSQL, retry=False)
        if err or parsed is None:
            log.warning("text2sql LLM generation failed: %s", err)
            return None
        return parsed.sql

    sql = _call_with_deadline(_generate, SQL_GENERATION_DEADLINE_S)
    if sql is None:
        log.info("text2sql: no SQL within %.0fs (or generation failed) — using the literature path", SQL_GENERATION_DEADLINE_S)
    return sql


def generate_sql(
    question: str,
    schema_text: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    project_id: str | None = None,
    complete_fn: Callable[[str, str], str | None] | None = None,
) -> str | None:
    """One-shot SQL generation. Returns cleaned SQL text or None on failure."""
    system, user = build_sqlite_prompt(
        question, schema_text, top_k=top_k, project_id=project_id
    )
    raw = (complete_fn or _default_complete)(system, user)
    if not raw:
        return None
    sql = raw.strip()
    # Strip markdown fences if the model wrapped the answer anyway.
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip()


# ---------------------------------------------------------------------------
# 3. Hard guardrails (what LangChain's prompt-only "protection" never had)
# ---------------------------------------------------------------------------


def validate_select_only(sql: str) -> str:
    """Reject anything that is not a single SELECT/WITH statement."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise Text2SQLError("empty SQL")
    first = cleaned.split(None, 1)[0].upper() if cleaned.split() else ""
    if first not in ("SELECT", "WITH"):
        raise Text2SQLError(f"only SELECT/WITH allowed, got: {first or '<empty>'}")
    # Multi-statement smuggling.
    if ";" in cleaned:
        raise Text2SQLError("multiple statements not allowed")
    # Strip string literals before the keyword scan.
    code_only = _STRING_LITERAL.sub("''", cleaned)
    hit = _FORBIDDEN.search(code_only)
    if hit:
        raise Text2SQLError(f"forbidden keyword: {hit.group(1).upper()}")
    return cleaned


def enforce_limit(sql: str, max_rows: int = DEFAULT_TOP_K) -> str:
    """Guarantee a LIMIT no larger than max_rows (server-side, not prompt)."""
    m = _LIMIT_CLAUSE.search(sql)
    if m:
        existing = int(m.group(1))
        if existing > max_rows:
            sql = _LIMIT_CLAUSE.sub(f"LIMIT {max_rows}", sql, count=1)
        return sql
    return sql.rstrip().rstrip(";") + f" LIMIT {max_rows}"


def execute_sql(
    engine: Engine,
    sql: str,
    *,
    max_rows: int = DEFAULT_TOP_K,
    timeout_s: float = SQL_TIMEOUT_S,
    scope: Scope | None = None,
    snapshot: Snapshot | None = None,
) -> list[dict]:
    """Validate, clamp LIMIT, run on a snapshot of ``scope`` (or the one passed in) with a timeout, return rows as dicts.

    Without a scope the snapshot is the whole whitelist — the single-user, no-project case. The statement never
    touches ``engine`` itself.
    """
    cleaned = validate_select_only(sql)
    final_sql = enforce_limit(cleaned, max_rows)
    if snapshot is not None:
        return snapshot.run(final_sql, max_rows=max_rows, timeout_s=timeout_s)
    with open_snapshot(engine, scope) as snap:
        return snap.run(final_sql, max_rows=max_rows, timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Lightweight self-check (QUERY_CHECKER's 8 error classes, distilled)
# ---------------------------------------------------------------------------


def self_check(sql: str, question: str) -> list[str]:
    """Return warning strings; empty means the checklist is clean.

    Advisory only — never blocks execution (hard guardrails already ran).
    """
    warnings: list[str] = []
    upper = sql.upper()
    if "SELECT *" in upper:
        warnings.append("SELECT * used; prefer explicit columns")
    if "WHERE" not in upper and "JOIN" in upper:
        warnings.append("JOIN without WHERE may explode row count")
    if any(w in question for w in ("上个月", "最近", "今年", "去年")) and "DATE(" not in upper:
        warnings.append("question mentions relative time but SQL has no date() filter")
    if "LIMIT" not in upper:
        warnings.append("no LIMIT clause (will be clamped server-side)")
    if upper.count("SELECT") > 2 and "WITH" not in upper:
        warnings.append("nested SELECTs without CTE; consider WITH for readability")
    return warnings


# ---------------------------------------------------------------------------
# 4. Hybrid routing: structured (Text2SQL) vs unstructured (ColBERT RAG)
# ---------------------------------------------------------------------------

_STRUCTURED_SIGNALS = (
    "查询", "统计", "大于", "小于", "平均", "最高", "最低", "总数",
    "上个月", "最近", "今年", "去年", "多少", "列表", "排序", "对比",
    ">", "<", "≥", "≤", "top",
)
_DOMAIN_SIGNALS = (
    "实验", "配方", "测量", "耐蚀", "腐蚀", "盐雾", "DOE", "除油",
    "性能", "指标", "批次", "版本",
)


_LITERATURE_SIGNALS = (
    "方法", "原理", "为什么", "如何", "机制", "文献", "研究", "综述",
    "介绍", "讲解", "概念", "机理",
)


def classify_intent(question: str) -> dict:
    """Rule-based intent classification. Returns {route, reason}.

    route is "structured" (Text2SQL over experiment/formulation tables),
    "hybrid" (both structured data and literature matter), or
    "unstructured" (literature retrieval over the knowledge base).
    """
    q = question or ""
    # P1-k: "…的方法"/"如何…"结尾是文献问法（问方法不是问数据），强制 unstructured，
    # 避免"查询耐蚀性提升的方法"被误判为 hybrid
    if q.rstrip("？?。！! ").endswith("的方法") or q.strip().startswith("如何"):
        return {"route": "unstructured", "reason": "以方法/如何问法结尾，文献性问题"}
    has_struct = any(s in q for s in _STRUCTURED_SIGNALS)
    has_domain = any(s in q for s in _DOMAIN_SIGNALS)
    has_lit = any(s in q for s in _LITERATURE_SIGNALS)
    if has_struct and has_domain:
        if has_lit:
            return {
                "route": "hybrid",
                "reason": "兼具结构化查询信号与文献性问法，走融合",
            }
        return {
            "route": "structured",
            "reason": "含结构化查询信号（数值比较/统计/时间范围）且涉及实验数据域",
        }
    if has_struct and not has_domain:
        # e.g. "查询耐蚀性提升的方法" — structured verb but no data-domain noun.
        return {"route": "unstructured", "reason": "结构化信号弱（无实验数据域），走文献检索"}
    return {"route": "unstructured", "reason": "描述性问题，走非结构化检索"}


def fuse_context(
    question: str,
    sql_text: str | None,
    rows: list[dict],
    evidence: list[Any],
    route: str = "structured",
) -> str:
    """Merge deterministic SQL rows + descriptive literature evidence.

    P3-3 attribution rule: SQL numbers are marked as precise experiment-DB
    values and never take literature footnotes; the two halves are visually
    separated so the answer model can attribute correctly.

    Returns "" when there is nothing to fuse (e.g. the unstructured route
    with no evidence text — the caller renders literature from its own
    sources path instead).
    """
    parts = [f"问题：{question}"]
    if route == "hybrid":
        parts.append(
            "回答要求：下文同时包含实验数据库的确定性数据与文献证据，"
            "引用时必须明确区分两者。"
        )
    if sql_text:
        parts.append(f"结构化查询 SQL：{sql_text}")
    if rows:
        lines = ["确定性数据结果（来自实验数据库，精确值）："]
        for i, row in enumerate(rows, 1):
            kv = "；".join(f"{k}={v}" for k, v in row.items())
            lines.append(f"  {i}. {kv}")
        parts.append("\n".join(lines))
    elif route in ("structured", "hybrid"):
        parts.append("结构化查询：无匹配数据。")
    if evidence:
        lines = ["相关文献证据（描述性）："]
        for i, ev in enumerate(evidence, 1):
            snippet = getattr(ev, "text", None) or getattr(ev, "snippet", None) or str(ev)
            title = getattr(ev, "title", None) or getattr(ev, "doc_title", None) or ""
            lines.append(f"  [{i}] {title}: {str(snippet)[:300]}")
        parts.append("\n".join(lines))
    if len(parts) == 1:
        return ""
    return "\n\n".join(parts)


def hybrid_answer(
    question: str,
    engine: Engine | None = None,
    *,
    project_id: str | None = None,
    owner_id: str | None = None,
    complete_fn: Callable[[str, str], str | None] | None = None,
    retrieve_fn: Callable[..., list[Any]] | None = None,
    evidence: list[Any] | None = None,
    include_evidence_text: bool = True,
    settings=None,
    max_rows: int = DEFAULT_TOP_K,
    timeout_s: float = SQL_TIMEOUT_S,
) -> dict:
    """Route the question, gather both sides, return a fused context.

    C-3 production entry for the chat chain (replaces the SQL-only
    ``structured_data_block``): one routing decision drives the SQL half
    and the literature half instead of two independent links.

    ``project_id`` and ``owner_id`` scope the rows the SQL can read (``owner_id`` is the caller's identity in
    multi-user mode; ``None`` or ``"default"`` restricts nothing).

    ``evidence`` accepts pre-retrieved literature — the chat chain already
    ran its KB retrieval, so it passes ``sources`` here and literature is
    never retrieved twice. ``include_evidence_text=False`` renders only the
    SQL section (the caller's own sources path carries the literature text).

    Structured path is fail-open: if SQL generation or execution fails, the
    route becomes ``"fallback"`` and the answer still carries the literature
    evidence instead of an error. Never raises.
    """
    from ..config import get_settings

    settings = settings or get_settings()
    if not getattr(settings, "text2sql_routing_enabled", True):
        ev = evidence if evidence is not None else []
        return {
            "route": "disabled",
            "route_reason": "text2sql routing disabled",
            "sql": None,
            "rows": [],
            "row_count": 0,
            "evidence_count": len(ev),
            "fused_context": "",
            "data_sources": ["kb_evidence"],
        }
    decision = classify_intent(question)
    route = decision["route"]
    sql_text: str | None = None
    rows: list[dict] = []
    if route in ("structured", "hybrid"):
        try:
            if engine is None:
                from ..db.database import default_engine

                engine = default_engine()
            # The statement runs on a copy that holds only what this caller may read, so a query that forgot (or
            # lied about) the project filter still cannot see another project's rows.
            with open_snapshot(engine, Scope(project_id, owner_id)) as snap:
                schema_text = render_schema(engine, snapshot=snap)
                gen = generate_sql(
                    question, schema_text, project_id=project_id, complete_fn=complete_fn
                )
                if not gen:
                    # Generation failed: honest fallback, not "no matching data".
                    raise Text2SQLError("empty SQL generated")
                sql_text = enforce_limit(validate_select_only(gen), max_rows)
                rows = execute_sql(
                    engine, sql_text, max_rows=max_rows, timeout_s=timeout_s, snapshot=snap
                )
        except Exception as exc:  # fail-open: fall back to evidence only
            log.warning("text2sql structured path failed, falling back: %s", exc)
            route = "fallback"
            sql_text = None
            rows = []
    ev: list[Any] = []
    if evidence is not None:
        ev = evidence
    else:
        try:
            retrieve = retrieve_fn
            if retrieve is None:
                from .kb_index import retrieve_evidence as _retrieve

                retrieve = _retrieve
            ev = retrieve(question, k=6, project_id=project_id) or []
        except Exception as exc:
            log.warning("text2sql evidence retrieval failed: %s", exc)
    # P1-b: data_sources 如实反映 —— 无证据时不谎报 kb_evidence
    data_sources = []
    if sql_text:
        data_sources.append("structured_sql")
    if ev:
        data_sources.append("kb_evidence")
    return {
        "route": route,
        "route_reason": decision["reason"],
        "sql": sql_text,
        "rows": rows,
        "row_count": len(rows),
        "evidence_count": len(ev),
        "fused_context": fuse_context(
            question,
            sql_text,
            rows,
            ev if include_evidence_text else [],
            route=route,
        ),
        "data_sources": data_sources,
    }
