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
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any, Callable

from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
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
# string literals so a label like 'delete me' does not false-positive.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|ATTACH|DETACH"
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
# 1. Schema rendering (DDL + N sample rows per table)
# ---------------------------------------------------------------------------


# Sample rows are shown to the model, so they obey the same project scope the query must.
# measurements / doe_plans carry no project_id of their own: they inherit it through experiments.
_SCOPED_SAMPLE_SQL = {
    "experiments": "SELECT * FROM experiments WHERE project_id = :pid LIMIT :n",
    "formulation_versions": "SELECT * FROM formulation_versions WHERE project_id = :pid LIMIT :n",
    "measurements": (
        "SELECT m.* FROM measurements AS m JOIN experiments AS e ON e.id = m.experiment_id "
        "WHERE e.project_id = :pid LIMIT :n"
    ),
    "doe_plans": (
        "SELECT p.* FROM doe_plans AS p JOIN experiments AS e ON e.id = p.experiment_id "
        "WHERE e.project_id = :pid LIMIT :n"
    ),
}


def render_schema(
    engine: Engine,
    sample_rows: int = SCHEMA_SAMPLE_ROWS,
    *,
    project_id: str | None = None,
) -> str:
    """Render whitelisted tables as DDL + sample rows for the prompt.

    With ``project_id`` the sample rows come from that project only: they go to the LLM
    (and can be echoed in its answer), so another project's rows must not appear there.
    """
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    blocks: list[str] = []
    for table_name in ALLOWED_TABLES:
        if table_name not in existing:
            continue
        table = _TABLE_MODELS[table_name].__table__
        ddl = str(CreateTable(table).compile(engine)).strip()
        with engine.connect() as conn:
            if project_id:
                result = conn.execute(
                    text(_SCOPED_SAMPLE_SQL[table_name]), {"pid": str(project_id), "n": sample_rows}
                )
            else:
                result = conn.exec_driver_sql(f"SELECT * FROM {table_name} LIMIT {sample_rows}")
            rows = result.mappings().all()
        sample = "\n".join(str(dict(r)) for r in rows) or "(empty)"
        blocks.append(f"{ddl};\n-- sample rows ({table_name}):\n{sample}")
    return "\n\n".join(blocks)


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
    scope_rule = (
        f"7. 必须按 project_id = '{project_id}' 过滤：experiments/formulation_versions "
        "用本表 project_id；measurements/doe_plans 先 JOIN experiments（experiment_id）再用 experiments.project_id 过滤。\n"
        if project_id
        else ""
    )
    system = _SQLITE_SYSTEM.format(
        schema=schema_text, top_k=top_k, project_scope_rule=scope_rule
    )
    return system, f"问题：{question}\nSQL:"


def _default_complete(system: str, user: str) -> str | None:
    """Default LLM call via the platform's structured completion."""
    from .llm import complete_structured

    parsed, err = complete_structured(system, user, _GeneratedSQL, retry=False)
    if err or parsed is None:
        log.warning("text2sql LLM generation failed: %s", err)
        return None
    return parsed.sql


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


_LITERAL_OR_COMMENT = re.compile(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")|--[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(sql: str) -> str:
    """Drop ``-- …`` and ``/* … */`` while leaving string literals (which may contain ``--``) alone."""
    return _LITERAL_OR_COMMENT.sub(lambda m: m.group(1) or " ", sql)


def require_project_scope(sql: str, project_id: str | None) -> str:
    """Deterministic guardrail: every SQL must filter on the given project.

    Prompt rules alone are advisory; the model may drop the filter. When a
    project_id is in scope the generated SQL must contain a literal
    ``project_id = '<id>'`` predicate (on the base table or via a JOIN to
    ``experiments``). Missing scope -> Text2SQLError, and the caller
    fail-opens to the literature path rather than leaking cross-project rows.
    """
    if not project_id:
        return sql
    # Match on the text with comments removed: a predicate that only survives inside
    # ``-- project_id = 'p1'`` filters nothing. (The threat modelled here is the model
    # forgetting the filter — not a hostile query — so string literals are kept as written.)
    pid = re.escape(str(project_id))
    if not re.search(rf"project_id\s*=\s*['\"]{pid}['\"]", _strip_comments(sql)):
        raise Text2SQLError("SQL missing project_id scope filter")
    return sql


def enforce_limit(sql: str, max_rows: int = DEFAULT_TOP_K) -> str:
    """Guarantee a LIMIT no larger than max_rows (server-side, not prompt)."""
    m = _LIMIT_CLAUSE.search(sql)
    if m:
        existing = int(m.group(1))
        if existing > max_rows:
            sql = _LIMIT_CLAUSE.sub(f"LIMIT {max_rows}", sql, count=1)
        return sql
    return sql.rstrip().rstrip(";") + f" LIMIT {max_rows}"


# Functions that reach outside the database (extension loading, file access). Everything else is
# plain SQL arithmetic / string / date work the generated queries legitimately use.
_FORBIDDEN_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})


def _protected_names(raw_conn: sqlite3.Connection) -> frozenset[str]:
    """Every real table / view in the database that is *not* whitelisted (lower-cased)."""
    rows = raw_conn.execute(
        "SELECT name FROM sqlite_master UNION SELECT name FROM sqlite_temp_master"
    ).fetchall()
    return frozenset(str(r[0]).lower() for r in rows) - frozenset(ALLOWED_TABLES)


def _make_authorizer(protected: frozenset[str], denied: list[str]) -> Callable[..., int]:
    """SQLite authorizer: no writes, no schema access, no reads of tables outside the whitelist.

    ``validate_select_only`` is a text filter on what the model wrote; this is the engine itself
    refusing — ``SELECT * FROM source_documents`` or ``sqlite_master`` fails at prepare time even
    when the text filter lets it through (the whitelist used to exist only in the prompt).

    Reads are checked against the set of *real* tables that are not whitelisted rather than
    "anything not on the list": a CTE or sub-query alias is reported as a read too, and carries
    no data of its own (what it selects from is authorised separately).
    """

    def authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _source: str | None) -> int:
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            if table in protected or table.startswith("sqlite_"):
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


def _run_with_timeout(raw_conn: Any, sql: str, timeout_s: float) -> Any:
    """Execute on a raw sqlite3 connection, aborting past timeout_s."""
    if isinstance(raw_conn, sqlite3.Connection):
        deadline = time.monotonic() + timeout_s
        denied: list[str] = []
        protected = _protected_names(raw_conn)

        def _handler() -> int:
            return 1 if time.monotonic() > deadline else 0

        raw_conn.set_progress_handler(_handler, 1000)
        raw_conn.set_authorizer(_make_authorizer(protected, denied))
        try:
            return raw_conn.execute(sql)
        except sqlite3.DatabaseError as exc:
            # The message differs by SQLite version ("not authorized" / "access to t.c is
            # prohibited"); the callback's own record is the reliable signal.
            if denied:
                raise Text2SQLError(f"query touches something outside the whitelist: {denied[0]}") from exc
            raise
        finally:
            # The connection goes back to the pool: leave no handler or authorizer behind.
            raw_conn.set_authorizer(None)
            raw_conn.set_progress_handler(None, 0)
    log.warning("text2sql: non-sqlite driver, statement timeout not enforced")
    return raw_conn.execute(sql)


def execute_sql(
    engine: Engine,
    sql: str,
    *,
    max_rows: int = DEFAULT_TOP_K,
    timeout_s: float = SQL_TIMEOUT_S,
) -> list[dict]:
    """Validate, clamp LIMIT, execute with timeout, return rows as dicts."""
    cleaned = validate_select_only(sql)
    final_sql = enforce_limit(cleaned, max_rows)
    with engine.connect() as conn:
        raw: Any = conn.connection
        driver = getattr(raw, "driver_connection", raw)
        try:
            cursor = _run_with_timeout(driver, final_sql, timeout_s)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise Text2SQLError(f"SQL timed out after {timeout_s}s") from exc
            raise
        rows = cursor.fetchmany(max_rows)
        cols = [d[0] for d in (cursor.description or [])]
    return [dict(zip(cols, r)) for r in rows]


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
            schema_text = render_schema(engine, project_id=project_id)
            gen = generate_sql(
                question, schema_text, project_id=project_id, complete_fn=complete_fn
            )
            if not gen:
                # Generation failed: honest fallback, not "no matching data".
                raise Text2SQLError("empty SQL generated")
            sql_text = enforce_limit(
                require_project_scope(validate_select_only(gen), project_id),
                max_rows,
            )
            rows = execute_sql(
                engine, sql_text, max_rows=max_rows, timeout_s=timeout_s
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
    data_sources = ["kb_evidence"]
    if sql_text:
        data_sources = ["structured_sql", "kb_evidence"]
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
