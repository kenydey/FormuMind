"""Review checklist (P1-32): structured checklist from ReviewRun + dispositions.

:func:`build_checklist` loads a persisted ReviewRun
(``data/reviews/runs/{run_id}.json``, see :mod:`reviewer_fix_loop`) and its
session dispositions (``data/reviews/{session_key}.json``), then builds
:class:`ChecklistItem` rows with ``pass`` / ``flagged`` / ``n_a`` verdicts.
The checklist is persisted next to the run file
(``data/reviews/runs/{run_id}.checklist.json``) and can be rendered as a
report appendix via :func:`render_checklist_markdown`, which
``tech_report.assemble_report(checklist=...)`` uses.

Fail-open throughout: missing run → ``None``; missing dispositions →
empty items; nothing here ever raises on I/O.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

Verdict = Literal["pass", "flagged", "n_a"]
VERDICTS = ("pass", "flagged", "n_a")

_VERDICT_LABEL = {"pass": "通过", "flagged": "标记", "n_a": "不适用"}

# Citation markers like [^1] / [^doi:...] inside reviewer notes.
_CITATION_MARK_RE = re.compile(r"\[\^([^\]]+)\]")

# category heuristics applied to the item statement, first match wins.
_CATEGORY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("citation", re.compile(r"引用|DOI|citation|reference|参考文献", re.IGNORECASE)),
    ("numeric", re.compile(r"数值|数字|参数|配比|百分|浓度|含量|%|\bmg\b|\bmol\b", re.IGNORECASE)),
    ("method", re.compile(r"方法|工艺|步骤|流程|method|procedure|protocol", re.IGNORECASE)),
)

_FAIL_STATUSES = {"failure", "warning", "fail", "warn", "flagged"}


@dataclass
class ChecklistItem:
    """One structured review checklist row."""

    id: str
    category: str  # citation | numeric | method | general
    statement: str
    verdict: Verdict
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    reviewer_note: str = ""


def _categorize(statement: str) -> str:
    text = statement or ""
    for category, rx in _CATEGORY_RULES:
        if rx.search(text):
            return category
    return "general"


def _evidence_refs(
    statement: str, evidence_map: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Extract ``[^marker]`` citations from a statement → ``[{source_id, page_no}]``.

    ``evidence_map`` optionally resolves a marker to
    ``{"source_id": ..., "page_no": ...}``; unmapped markers keep the raw
    marker text as ``source_id`` with ``page_no=None``.
    """
    refs: list[dict[str, Any]] = []
    for marker in _CITATION_MARK_RE.findall(statement or ""):
        mapped = (evidence_map or {}).get(marker)
        if isinstance(mapped, dict):
            refs.append(
                {
                    "source_id": mapped.get("source_id", marker),
                    "page_no": mapped.get("page_no"),
                }
            )
        elif isinstance(mapped, (list, tuple)) and mapped:
            refs.append(
                {
                    "source_id": mapped[0],
                    "page_no": mapped[1] if len(mapped) > 1 else None,
                }
            )
        else:
            refs.append({"source_id": marker, "page_no": None})
    # de-dupe, preserve order
    seen: set[tuple[Any, Any]] = set()
    out: list[dict[str, Any]] = []
    for ref in refs:
        key = (ref.get("source_id"), ref.get("page_no"))
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


def _verdict_for_disposition(disp: dict[str, Any]) -> Verdict:
    disposition = str(disp.get("disposition") or "").lower()
    status = str(disp.get("status") or "").lower()
    if disposition == "resolved" or status == "pass":
        return "pass"
    if disposition in {"open", "unaddressed"} or status in _FAIL_STATUSES:
        return "flagged"
    return "n_a"


def _verdict_for_status(status: Any) -> Verdict:
    s = str(status or "").lower()
    if s == "pass":
        return "pass"
    if s in _FAIL_STATUSES:
        return "flagged"
    return "n_a"


def _item_id(run_id: str, statement: str, idx: int) -> str:
    digest = hashlib.sha256(
        f"{run_id}|{idx}|{statement[:120]}".encode("utf-8")
    ).hexdigest()[:10]
    return f"chk-{digest}"


def _checklist_path(run_id: str):
    """``data/reviews/runs/{run_id}.checklist.json`` — same dir as the run."""
    from .reviewer_fix_loop import _run_path

    run_path = _run_path(run_id)
    return run_path.with_name(run_path.stem + ".checklist.json")


def build_checklist(
    run_id: str,
    *,
    findings: dict[str, Any] | None = None,
    evidence_map: dict[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any] | None:
    """Build a structured review checklist for a persisted ReviewRun.

    - ``run_id``: ReviewRun id (see :mod:`reviewer_fix_loop`).
    - ``findings``: optional review dict ``{"status","notes","suggestion"}``
      (e.g. the ``findings`` from ``run_fix_loop`` meta); when omitted, items
      are derived from dispositions alone and ``reviewer_note`` stays empty.
    - ``evidence_map``: optional ``{marker: {"source_id","page_no"}}`` used to
      resolve ``[^marker]`` citations found in statements.
    - ``persist``: write ``{run_id}.checklist.json`` next to the run file.

    Returns ``None`` when the run does not exist; otherwise
    ``{"run_id","session_key","outcome","generated_at","items","summary"}``.
    Never raises on missing/corrupt data (fail-open).
    """
    try:
        from .reviewer_fix_loop import load_dispositions, load_review_run
    except Exception as exc:  # noqa: BLE001
        logger.warning("review_checklist: reviewer_fix_loop unavailable: %s", exc)
        return None
    try:
        run = load_review_run(run_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("review_checklist: load run %s failed: %s", run_id, exc)
        return None
    if not run:
        return None

    session_key = run.get("session_key") or ""
    try:
        dispositions = load_dispositions(session_key) if session_key else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("review_checklist: load dispositions failed: %s", exc)
        dispositions = {}
    if not isinstance(dispositions, dict):
        dispositions = {}

    items: list[ChecklistItem] = []
    notes = list((findings or {}).get("notes") or [])
    if notes:
        # Rich path: one item per reviewer note; verdict from the matching
        # disposition (keyed by note[:80], same convention as fix-loop),
        # falling back to the overall findings status.
        fallback = _verdict_for_status((findings or {}).get("status"))
        suggestion = str((findings or {}).get("suggestion") or "")
        for i, note in enumerate(notes):
            statement = str(note or "")
            disp = dispositions.get(statement[:80])
            verdict = (
                _verdict_for_disposition(disp)
                if isinstance(disp, dict)
                else fallback
            )
            items.append(
                ChecklistItem(
                    id=_item_id(run_id, statement, i),
                    category=_categorize(statement),
                    statement=statement,
                    verdict=verdict,
                    evidence_refs=_evidence_refs(statement, evidence_map),
                    reviewer_note=suggestion,
                )
            )
    else:
        # Disposition-only path: statement is the disposition key (note text).
        for i, (key, disp) in enumerate(dispositions.items()):
            statement = str(key or "")
            items.append(
                ChecklistItem(
                    id=_item_id(run_id, statement, i),
                    category=_categorize(statement),
                    statement=statement,
                    verdict=_verdict_for_disposition(
                        disp if isinstance(disp, dict) else {}
                    ),
                    evidence_refs=_evidence_refs(statement, evidence_map),
                    reviewer_note="",
                )
            )

    summary: dict[str, int] = {"total": len(items), "pass": 0, "flagged": 0, "n_a": 0}
    for item in items:
        summary[item.verdict] = summary.get(item.verdict, 0) + 1

    payload: dict[str, Any] = {
        "run_id": run_id,
        "session_key": session_key,
        "outcome": run.get("outcome"),
        "generated_at": time.time(),
        "items": [asdict(item) for item in items],
        "summary": summary,
    }
    if persist:
        try:
            path = _checklist_path(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("review_checklist: persist failed: %s", exc)
    return payload


def load_checklist(run_id: str) -> dict[str, Any] | None:
    """Load a previously persisted checklist; ``None`` when absent/corrupt."""
    try:
        path = _checklist_path(run_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        cells = [str(c)[:160] if c is not None else "" for c in (list(row) + [""] * len(headers))[: len(headers)]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _format_refs(refs: list[dict[str, Any]]) -> str:
    parts = []
    for r in refs or []:
        sid = str(r.get("source_id") or "")
        page = r.get("page_no")
        parts.append(f"{sid} p.{page}" if page is not None else sid)
    return "；".join(parts) or "—"


def render_checklist_markdown(checklist: dict[str, Any] | None) -> str:
    """Render the checklist appendix section (Markdown). Empty input → ``""``."""
    if not checklist:
        return ""
    items = checklist.get("items") or []
    summary = checklist.get("summary") or {}
    total = summary.get("total", len(items))
    lines = [
        "## 附录：Review 清单\n",
        f"**结论汇总**：共 {total} 项 —— 通过 {summary.get('pass', 0)} 项 / "
        f"标记 {summary.get('flagged', 0)} 项 / 不适用 {summary.get('n_a', 0)} 项\n",
    ]
    if items:
        rows = [
            [
                i + 1,
                it.get("category", ""),
                _VERDICT_LABEL.get(it.get("verdict"), it.get("verdict", "")),
                (it.get("statement") or "")[:160],
                _format_refs(it.get("evidence_refs") or []),
            ]
            for i, it in enumerate(items)
        ]
        lines.append(_md_table(["#", "类别", "结论", "陈述", "证据引用"], rows) + "\n")
        flagged = [it for it in items if it.get("verdict") == "flagged"]
        if flagged:
            lines.append("### 标记项明细\n")
            for it in flagged:
                lines.append(
                    f"- **[{it.get('category', '')}] {(it.get('statement') or '')[:160]}**"
                    f"（结论：{_VERDICT_LABEL.get('flagged')}）"
                )
                if it.get("reviewer_note"):
                    lines.append(f"  - 审阅备注：{str(it['reviewer_note'])[:200]}")
                refs = _format_refs(it.get("evidence_refs") or [])
                if refs != "—":
                    lines.append(f"  - 证据：{refs}")
    return "\n".join(lines).rstrip() + "\n"
