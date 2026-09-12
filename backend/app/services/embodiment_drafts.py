"""P3.1 — Embodiment Formulation drafts from ingested fulltext only.

Eligibility gate: SourceDocument with usable parsed body (not abstract-only).
Extract: markdown/HTML tables → real wt%; else honest placeholder.
Confirm: KG + force_pending materials; never production formula pool.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import re
from typing import Any

from ..db.chunk_store import get_chunk_store
from ..db.entity_store import get_entity_store
from ..db.session_utils import commit_session
from ..db.source_store import get_source_store
from .kg.formulation_linker import _infer_role
from .material_promote import propose_material
from .patent_ids import normalize_patent_pub

_MIN_CHARS = 500
_OK_STATUS = frozenset({"ok", "degraded", "fulltext"})
_ALLOWED_ORIGINS = frozenset(
    {
        "patent_fulltext",
        "literature_fulltext",
        "document_fulltext",
        "oa_pdf",
        "surechembl",
        "surechembl+fulltext",
    }
)

_NAME_HEADERS = re.compile(
    r"^(component|ingredient|material|raw\s*material|substance|name|组分|原料|成分|物料)$",
    re.I,
)
_AMOUNT_HEADERS = re.compile(
    r"^(wt%|wt\.?%|mass\s*%|weight\s*%|%|parts?|phr|重量份|质量分数|质量%|份)$",
    re.I,
)
_EXAMPLE_HEAD = re.compile(
    r"(example|embodiment|实施例|实例)\s*([0-9０-９]+|[IVXLC]+)?",
    re.I,
)
_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_PUB_NO_NAME_RE = re.compile(
    r"^(?:CN|US|EP|DE|WO|JP|KR)\s*\d{5,}[A-Z0-9]*\b",
    re.I,
)
_FORBIDDEN_NAME_HEADERS = re.compile(
    r"^(patent|publication|pub\.?\s*no\.?|document|title|编号|公开号|专利号)$",
    re.I,
)
_PRIOR_ART_CONTEXT = re.compile(
    r"(prior\s*art|related\s*art|citation|references?|现有技术|对比文件|对比实施例)",
    re.I,
)
_DIRTY_NAME_RE = re.compile(
    r"(preparation\s+method|and\s+its\s+prepar|及其制备|制备方法)",
    re.I,
)
_AMOUNT_UNIT_OK = re.compile(r"wt|%|份|phr|parts?|mass|质量", re.I)


def _is_publication_number(name: str) -> bool:
    return bool(_PUB_NO_NAME_RE.match((name or "").strip()))


def _looks_like_year_amount(num: float, unit_hint: str) -> bool:
    if _AMOUNT_UNIT_OK.search(unit_hint or ""):
        return False
    return 1900.0 <= float(num) <= 2100.0


def check_eligibility(source_id: str) -> dict[str, Any]:
    """Hard gate: ingested + usable fulltext/chunks only."""
    sid = (source_id or "").strip()
    base = {
        "source_id": sid,
        "eligible": False,
        "reason": "not_ingested",
        "raw_text_chars": 0,
        "extraction_status": None,
        "chunk_count": 0,
        "source_kind": None,
        "has_table_signal": False,
        "title": None,
    }
    if not sid:
        return base

    doc = get_source_store().get(sid)
    if doc is None:
        return base

    chunks = get_chunk_store().get_by_source(sid)
    chunk_count = len(chunks)
    chars = int(doc.raw_text_chars or 0)
    status = (doc.extraction_status or "").strip().lower() or "pending"
    has_body = bool((doc.full_text or "").strip()) or chunk_count > 0
    table_signal = any(_looks_like_table(c.text or "") for c in chunks[:80])

    out = {
        **base,
        "raw_text_chars": chars,
        "extraction_status": status,
        "chunk_count": chunk_count,
        "source_kind": doc.source_kind,
        "has_table_signal": table_signal,
        "title": doc.title or doc.filename,
    }

    if status == "failed":
        out["reason"] = "extract_failed"
        return out
    if status == "pending" and not has_body:
        out["reason"] = "abstract_only"
        return out
    if chars < _MIN_CHARS and not (chunk_count and sum(len(c.text or "") for c in chunks) >= _MIN_CHARS):
        # Allow if chunk text total is large even when raw_text_chars is stale/low.
        chunk_chars = sum(len(c.text or "") for c in chunks)
        if chunk_chars < _MIN_CHARS:
            out["reason"] = "too_short"
            return out
        chars = max(chars, chunk_chars)
        out["raw_text_chars"] = chars
    if not has_body:
        out["reason"] = "no_chunks"
        return out
    if status not in _OK_STATUS and not has_body:
        out["reason"] = "abstract_only"
        return out
    # Usable body wins even if status is odd (e.g. legacy), as long as not failed.
    if status not in _OK_STATUS and chars < _MIN_CHARS and chunk_count == 0:
        out["reason"] = "abstract_only"
        return out

    out["eligible"] = True
    out["reason"] = None
    return out


def check_eligibility_many(source_ids: list[str]) -> dict[str, Any]:
    items = [check_eligibility(s) for s in source_ids]
    return {"items": items}


def _looks_like_table(text: str) -> bool:
    t = text or ""
    if "<table" in t.lower():
        return True
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    pipe_rows = [ln for ln in lines if ln.count("|") >= 2]
    return len(pipe_rows) >= 3


def _parse_number(raw: str) -> float | None:
    m = _NUM_RE.search((raw or "").replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _normalize_header(cell: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(cell or "").strip())


def _split_md_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [_normalize_header(c) for c in s.split("|")]


def _is_md_sep(line: str) -> bool:
    cells = _split_md_row(line)
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells if c)


def parse_markdown_tables(text: str) -> list[dict[str, Any]]:
    """Return list of {headers, rows, label, page_hint} from GFM tables."""
    lines = (text or "").splitlines()
    tables: list[dict[str, Any]] = []
    i = 0
    pending_label = "Example"
    while i < len(lines) - 1:
        line = lines[i].strip()
        em = _EXAMPLE_HEAD.search(line)
        if em and "|" not in line:
            pending_label = line[:80] or "Example"
            i += 1
            continue
        if line.count("|") >= 2 and i + 1 < len(lines) and _is_md_sep(lines[i + 1]):
            headers = _split_md_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().count("|") >= 2:
                if _is_md_sep(lines[i]):
                    i += 1
                    continue
                rows.append(_split_md_row(lines[i]))
                i += 1
            if headers and rows:
                tables.append(
                    {
                        "headers": headers,
                        "rows": rows,
                        "label": pending_label,
                        "page_hint": None,
                    }
                )
                pending_label = f"Example {len(tables) + 1}"
            continue
        i += 1
    return tables


def parse_html_tables(text: str) -> list[dict[str, Any]]:
    """Lightweight HTML <table> parse without external deps."""
    tables: list[dict[str, Any]] = []
    for m in re.finditer(r"<table\b[^>]*>(.*?)</table>", text or "", flags=re.I | re.S):
        block = m.group(1)
        rows_raw = re.findall(r"<tr\b[^>]*>(.*?)</tr>", block, flags=re.I | re.S)
        parsed_rows: list[list[str]] = []
        for rr in rows_raw:
            cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", rr, flags=re.I | re.S)
            cleaned = [
                _normalize_header(re.sub(r"<[^>]+>", "", c))
                for c in cells
            ]
            if cleaned:
                parsed_rows.append(cleaned)
        if len(parsed_rows) < 2:
            continue
        headers = parsed_rows[0]
        body = parsed_rows[1:]
        label = "Example"
        # Look back for example heading in preceding 200 chars
        start = max(0, m.start() - 200)
        head = text[start : m.start()]
        em = list(_EXAMPLE_HEAD.finditer(head))
        if em:
            label = em[-1].group(0)[:80]
        tables.append({"headers": headers, "rows": body, "label": label, "page_hint": None})
    return tables


def _column_map(
    headers: list[str], rows: list[list[str]] | None = None
) -> tuple[int | None, int | None, str]:
    name_idx = None
    amt_idx = None
    unit_hint = ""
    for i, h in enumerate(headers):
        key = h.strip()
        if name_idx is None and not _FORBIDDEN_NAME_HEADERS.match(key):
            if _NAME_HEADERS.match(key):
                name_idx = i
            elif re.search(r"component|ingredient|原料|组分|成分", key, re.I):
                name_idx = i
        if amt_idx is None and _AMOUNT_HEADERS.match(key):
            amt_idx = i
            unit_hint = key
        if amt_idx is None and re.search(r"wt\s*%|重量份|phr|mass\s*%|质量", key, re.I):
            amt_idx = i
            unit_hint = key
    if amt_idx is None and len(headers) > 1 and rows:
        last_idx = len(headers) - 1
        unit_hint_candidate = headers[last_idx]
        numeric = 0
        total = 0
        for row in rows:
            if last_idx >= len(row):
                continue
            total += 1
            num = _parse_number(row[last_idx])
            if num is not None and not _looks_like_year_amount(num, unit_hint_candidate):
                numeric += 1
        if total > 0 and numeric >= total / 2:
            amt_idx = last_idx
            unit_hint = unit_hint_candidate
    return name_idx, amt_idx, unit_hint


def _amounts_to_weight_pct(
    amounts: list[float], unit_hint: str
) -> tuple[list[float | None], str, list[str]]:
    """Normalize raw amounts → weight_pct list + amount_source hint + warnings."""
    warnings: list[str] = []
    u = (unit_hint or "").lower()
    is_parts = bool(re.search(r"parts?|phr|重量份|份", u)) and "wt" not in u and "%" not in u
    is_pct = (not is_parts) and bool(re.search(r"%|wt|质量分数|mass", u) or u.strip() in {"%", "wt%"})

    if not amounts or any(a is None for a in amounts):  # type: ignore[truthy-function]
        pass
    vals = [float(a) for a in amounts]
    if is_parts or (not is_pct and sum(vals) > 105):
        total = sum(vals)
        if total <= 0:
            return [None] * len(vals), "placeholder", ["表内份数总和为 0，无法归一"]
        pcts = [round(100.0 * v / total, 4) for v in vals]
        return pcts, "table", []
    # Treat as wt%
    pcts = [round(v, 4) for v in vals]
    s = sum(pcts)
    if s < 95 or s > 105:
        warnings.append(f"表内 wt% 总和为 {s:.2f}（期望约 100），请人审")
    return pcts, "table", warnings


def table_to_ingredients(table: dict[str, Any]) -> dict[str, Any] | None:
    headers = table.get("headers") or []
    rows = table.get("rows") or []
    name_idx, amt_idx, unit_hint = _column_map(headers, rows)
    if name_idx is None or amt_idx is None:
        return None
    names: list[str] = []
    amounts: list[float] = []
    for row in rows:
        if name_idx >= len(row) or amt_idx >= len(row):
            continue
        name = row[name_idx].strip()
        if not name or _NAME_HEADERS.match(name):
            continue
        if _is_publication_number(name):
            continue
        num = _parse_number(row[amt_idx])
        if num is None or _looks_like_year_amount(num, unit_hint):
            continue
        names.append(name[:200])
        amounts.append(num)
    if len(names) < 2:
        return None
    pcts, amount_source, warnings = _amounts_to_weight_pct(amounts, unit_hint)
    ingredients = []
    extra_warnings: list[str] = []
    for name, pct, raw in zip(names, pcts, amounts):
        role = _infer_role(name)
        conf = 0.85 if amount_source == "table" else 0.4
        if _DIRTY_NAME_RE.search(name):
            conf = min(conf, 0.45)
            msg = "名称可能含标题污染，请核对原件"
            if msg not in extra_warnings:
                extra_warnings.append(msg)
        ingredients.append(
            {
                "name": name,
                "role": role,
                "weight_pct": pct if pct is not None else round(100.0 / len(names), 4),
                "unit_raw": unit_hint,
                "amount_raw": raw,
                "confidence": conf,
                "evidence_span": None,
                "smiles": None,
                "cas_no": None,
            }
        )
    warnings = list(warnings) + extra_warnings
    return {
        "label": table.get("label") or "Example",
        "page_hint": table.get("page_hint"),
        "ingredients": ingredients,
        "amount_source": amount_source if all(p is not None for p in pcts) else "placeholder",
        "warnings": warnings,
    }


def _is_real_example_label(label: str) -> bool:
    """Numbered embodiment heading or 配方 — not parser default bare ``Example``."""
    if re.search(r"配方", label or ""):
        return True
    m = _EXAMPLE_HEAD.search(label or "")
    if not m:
        return False
    return bool(m.group(2))


def score_embodiment_table(table: dict[str, Any], emb_row: dict[str, Any]) -> float:
    score = 0.0
    label = str(table.get("label") or emb_row.get("label") or "")
    headers = " ".join(table.get("headers") or [])
    if _is_real_example_label(label):
        score += 3.0
    name_ok = bool(
        re.search(
            r"component|ingredient|material|substance|原料|组分|成分|物料",
            headers,
            re.I,
        )
    )
    amt_ok = bool(re.search(r"wt\s*%|重量份|phr|%|parts?", headers, re.I))
    if name_ok and amt_ok:
        score += 4.0
    names = [i.get("name") or "" for i in emb_row.get("ingredients") or []]
    if names:
        pub_frac = sum(1 for n in names if _is_publication_number(n)) / len(names)
        if pub_frac >= 0.5:
            score -= 10.0
    blob = f"{label} {headers}"
    if _PRIOR_ART_CONTEXT.search(blob):
        score -= 5.0
    n = len(names)
    score += 0.1 * min(n, 8)
    return score


def _collect_source_text(source_id: str) -> tuple[str, list[dict[str, Any]]]:
    doc = get_source_store().get(source_id)
    chunks = get_chunk_store().get_by_source(source_id)
    parts: list[str] = []
    meta: list[dict[str, Any]] = []
    if doc and doc.full_text:
        parts.append(doc.full_text)
    for c in chunks:
        t = c.text or ""
        if not t.strip():
            continue
        parts.append(t)
        meta.append({"chunk_id": c.id, "page_no": c.page_no, "ord": c.ord})
    return "\n\n".join(parts), meta


def _origin_for_kind(
    source_kind: str | None,
    *,
    surechembl: bool = False,
    doc: Any | None = None,
) -> str:
    if surechembl:
        return "surechembl+fulltext"
    if doc is not None:
        for candidate in (
            getattr(doc, "filename", None),
            getattr(doc, "title", None),
            getattr(doc, "origin_url", None),
        ):
            _office, compact = normalize_patent_pub(candidate)
            if compact:
                return "patent_fulltext"
    kind = (source_kind or "").lower()
    if "patent" in kind:
        return "patent_fulltext"
    if kind in {"oa", "oa_pdf", "openalex"}:
        return "oa_pdf"
    if kind in {"literature", "arxiv", "paper", "scholar"}:
        return "literature_fulltext"
    if kind in {"web", "local", "upload", "pasted", "image", "api"}:
        return "document_fulltext"
    return "document_fulltext"


def _placeholder_from_names(names: list[str], *, limit: int = 8) -> list[dict[str, Any]]:
    picked = []
    seen: set[str] = set()
    for n in names:
        key = n.casefold()
        if not n or key in seen:
            continue
        seen.add(key)
        picked.append(n)
        if len(picked) >= limit:
            break
    if not picked:
        return []
    share = round(100.0 / len(picked), 4)
    return [
        {
            "name": n,
            "role": _infer_role(n),
            "weight_pct": share,
            "unit_raw": None,
            "amount_raw": None,
            "confidence": 0.3,
            "evidence_span": None,
            "smiles": None,
            "cas_no": None,
        }
        for n in picked
    ]


_FLAT_ROW_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\s\-/\.]{1,60}?)"
    r"\s+(?P<num>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>wt\s*%|weight\s*%|重量份|质量份|份|%|g|kg)?\s*$",
    re.I,
)
_FLAT_BOILERPLATE = re.compile(
    r"^(the|a|an|this|typical|translated|description|example|claim|"
    r"coating|coatings|primer|composition|compositions|may|include|includes|"
    r"present|invention|according|wherein)\b",
    re.I,
)
_EMBODIMENT_HEADING = re.compile(
    r"(?i)(example\s*\d+|embodiment\s*\d+|实施例\s*[0-9一二三四五六七八九十]+|配方\s*[0-9]+)"
)


def parse_flattened_amount_rows(text: str) -> dict[str, Any] | None:
    """Recover ``Name 40`` / ``环氧树脂 30重量份`` lines after HTML table strip (F3).

    Requires ≥2 consecutive high-precision rows near an embodiment heading
    (or anywhere if ≥3 rows). Rejects English boilerplate sentence fragments.
    """
    lines = (text or "").splitlines()
    best: list[dict[str, Any]] = []
    best_label = "Flattened rows"
    current: list[dict[str, Any]] = []
    label = "Flattened rows"

    def flush() -> None:
        nonlocal best, current
        if len(current) >= 2 and len(current) > len(best):
            best = list(current)
        current = []

    for ln in lines:
        raw = ln.strip()
        if not raw:
            flush()
            continue
        if _EMBODIMENT_HEADING.search(raw) and len(raw) < 80:
            flush()
            label = raw[:80]
            continue
        m = _FLAT_ROW_RE.match(raw)
        if not m:
            flush()
            continue
        name = re.sub(r"\s+", " ", m.group("name")).strip(" -./")
        if len(name) < 2 or _FLAT_BOILERPLATE.match(name):
            flush()
            continue
        # Reject sentence-like names (too many stopwords / verbs)
        if re.search(r"\b(may|include|includes|comprising|wherein|according)\b", name, re.I):
            flush()
            continue
        if sum(ch.isalpha() for ch in name) < 2:
            flush()
            continue
        num = float(m.group("num"))
        unit = (m.group("unit") or "").strip() or None
        current.append(
            {
                "name": name,
                "role": _infer_role(name),
                "amount_raw": num,
                "unit_raw": unit,
                "evidence_span": raw[:200],
            }
        )
        best_label = label
    flush()

    if len(best) < 2:
        return None
    amounts = [r["amount_raw"] for r in best]
    unit_hint = next((r["unit_raw"] for r in best if r.get("unit_raw")), None)
    pcts, amount_source, warnings = _amounts_to_weight_pct(amounts, unit_hint)
    if not all(p is not None for p in pcts):
        return None
    # Flattened recovery is not a true GFM table — label honestly.
    if amount_source == "table":
        amount_source = "prose"
    ingredients = []
    extra_warnings: list[str] = []
    for row, pct in zip(best, pcts):
        name = row["name"]
        conf = 0.55
        if _DIRTY_NAME_RE.search(name):
            conf = min(conf, 0.45)
            msg = "名称可能含标题污染，请核对原件"
            if msg not in extra_warnings:
                extra_warnings.append(msg)
        ingredients.append(
            {
                "name": name,
                "role": row.get("role") or _infer_role(name),
                "weight_pct": pct,
                "unit_raw": row.get("unit_raw"),
                "amount_raw": row.get("amount_raw"),
                "confidence": conf,
                "evidence_span": row.get("evidence_span"),
                "smiles": None,
                "cas_no": None,
            }
        )
    warnings = list(warnings) + extra_warnings + ["比重来自剥扁文本行恢复（非原生表格），请核对原件"]
    return {
        "label": best_label,
        "page_hint": None,
        "ingredients": ingredients,
        "amount_source": amount_source,
        "warnings": warnings,
    }


def extract_embodiment_draft(
    *,
    source_id: str,
    domain: str = "anticorrosion_coating",
    surechembl_hint: bool = False,
) -> dict[str, Any]:
    """Build review-only draft from ingested fulltext. No DB writes."""
    elig = check_eligibility(source_id)
    if not elig.get("eligible"):
        return {"ok": False, "reason": elig.get("reason") or "ineligible", "draft": None, "eligibility": elig}

    text, _chunk_meta = _collect_source_text(source_id)
    md_tables = parse_markdown_tables(text)
    html_tables = parse_html_tables(text)
    scored: list[tuple[float, dict[str, Any]]] = []
    for t in md_tables + html_tables:
        emb_row = table_to_ingredients(t)
        if not emb_row:
            continue
        sc = score_embodiment_table(t, emb_row)
        if sc < 0:
            continue
        emb_row["_score"] = sc
        scored.append((sc, emb_row))
    scored.sort(key=lambda x: (-x[0], -len(x[1]["ingredients"]), x[1].get("label") or ""))
    embodiments = [e for _, e in scored]
    for e in embodiments:
        e.pop("_score", None)

    amount_source = "placeholder"
    warnings = [
        "人审草稿：确认后仅进入原料 pending + KG，不会写入生产配方池",
    ]
    if embodiments:
        primary = embodiments[0]
        amount_source = primary.get("amount_source") or "table"
        warnings.extend(primary.get("warnings") or [])
        if len(embodiments) > 1:
            warnings.append(
                f"检测到 {len(embodiments)} 个可用表；默认按表语义分展示，其余可在人审中切换"
            )
        ingredients = primary["ingredients"]
        if amount_source == "table":
            warnings.insert(0, "比重来自已解析全文表格（已归一为 wt%），请核对原件")
    else:
        # F3: try flattened name+number rows before empty placeholder (P3.1b).
        flat = parse_flattened_amount_rows(text)
        if flat and flat.get("ingredients"):
            embodiments = [flat]
            ingredients = flat["ingredients"]
            amount_source = flat.get("amount_source") or "prose"
            warnings.extend(flat.get("warnings") or [])
        else:
            # F0: never invent Title-Case sentence fragments as ingredients.
            ingredients = []
            warnings.insert(
                0,
                "未识别到可用配方表或可恢复配比行；请改用含表格的 PDF 再入库全文，或人工填写组分",
            )
            embodiments = [
                {
                    "label": "No formulation table",
                    "page_hint": None,
                    "ingredients": [],
                    "amount_source": "placeholder",
                    "warnings": list(warnings),
                }
            ]
            amount_source = "placeholder"

    if amount_source == "table":
        text_provenance = "markdown_table" if md_tables else "html_table"
    elif amount_source == "prose":
        text_provenance = "prose"
    else:
        text_provenance = "none"

    doc = get_source_store().get(source_id)
    origin = _origin_for_kind(doc.source_kind if doc else None, surechembl=surechembl_hint, doc=doc)
    title = (doc.title if doc else None) or elig.get("title") or source_id
    identifier = (doc.origin_url if doc else None) or source_id

    draft = {
        "status": "draft",
        "needs_review": True,
        "origin": origin,
        "source_id": source_id,
        "doc_id": identifier,
        "title": title,
        "assignee": None,
        "pub_date": None,
        "url": doc.origin_url if doc else None,
        "url_alt": None,
        "amount_source": amount_source,
        "text_provenance": text_provenance,
        "embodiments": embodiments,
        "formulation": {
            "name": f"全文草稿 · {(title or source_id)[:80]}",
            "domain": domain,
            "ingredients": [
                {
                    "name": ing["name"],
                    "role": ing.get("role") or "unknown",
                    "weight_pct": ing["weight_pct"],
                    "smiles": ing.get("smiles"),
                    "cas_no": ing.get("cas_no"),
                }
                for ing in ingredients
            ],
            "predicted": {},
            "warnings": warnings,
            "source": origin,
        },
        "ingredients_detail": ingredients,
        "chemistry_count": len(ingredients),
    }
    return {"ok": True, "draft": draft, "eligibility": elig}


# ── KG helpers (Slice D) ──────────────────────────────────────────────


# normalize_patent_pub imported from patent_ids (shared with kb ingest / classify)


def patent_entity_id_from_pub(raw: str) -> str | None:
    office, compact = normalize_patent_pub(raw)
    if not office or not compact:
        return None
    return f"patent:{office.lower()}:{compact}"[:64]


def paper_entity_id(doc: Any) -> str | None:
    """Build paper:* id from origin_url / title heuristics."""
    url = (getattr(doc, "origin_url", None) or "").strip()
    if not url:
        return None
    doi = None
    m = re.search(r"10\.\d{4,9}/[^\s]+", url, re.I)
    if m:
        doi = m.group(0).rstrip(".)")
        return f"paper:doi:{doi}"[:64]
    if "arxiv.org" in url.lower():
        m2 = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", url)
        if m2:
            return f"paper:arxiv:{m2.group(1)}"[:64]
    if "openalex.org" in url.lower():
        m3 = re.search(r"(W\d+)", url)
        if m3:
            return f"paper:openalex:{m3.group(1)}"[:64]
    return None


def formulation_draft_entity_id(source_id: str, slug: str = "embodiment") -> str:
    digest = hashlib.sha1(f"{source_id}:{slug}".encode()).hexdigest()[:12]
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug)[:16] or "embodiment"
    return f"form:fulltext:{digest}:{safe}"[:64]


def chem_entity_id_from_name(name: str) -> str:
    digest = hashlib.sha1(name.strip().casefold().encode()).hexdigest()[:16]
    return f"chem:draft:{digest}"[:64]


def confirm_embodiment_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """Human confirm: KG + pending materials only."""
    if not isinstance(draft, dict):
        return {"ok": False, "reason": "invalid_draft"}
    origin = str(draft.get("origin") or "")
    if origin not in _ALLOWED_ORIGINS or not draft.get("needs_review"):
        return {"ok": False, "reason": "draft_not_reviewable"}

    source_id = str(draft.get("source_id") or "").strip()
    if origin != "surechembl" and not source_id:
        return {"ok": False, "reason": "missing_source_id"}

    if source_id:
        elig = check_eligibility(source_id)
        if not elig.get("eligible"):
            return {"ok": False, "reason": elig.get("reason") or "ineligible"}

    details = list(draft.get("ingredients_detail") or [])
    if not details:
        form = draft.get("formulation") or {}
        details = list(form.get("ingredients") or [])
    if not details and draft.get("embodiments"):
        details = list((draft["embodiments"][0] or {}).get("ingredients") or [])

    # Drop empty / whitespace-only names
    details = [r for r in details if str((r or {}).get("name") or "").strip()]
    if not details:
        return {"ok": False, "reason": "no_usable_ingredients"}

    amount_source = str(draft.get("amount_source") or "placeholder")
    placeholder = amount_source == "placeholder"

    # Pending materials
    pending: list[dict[str, Any]] = []
    for row in details:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        try:
            result = propose_material(
                name,
                {
                    "smiles": row.get("smiles") or None,
                    "cas_no": row.get("cas_no") or None,
                    "role": row.get("role") or "unknown",
                },
                source=origin if len(origin) <= 32 else "fulltext_draft",
                source_ref=f"embodiment draft {source_id or draft.get('doc_id')}",
                force_pending=True,
            )
            pending.append(result)
        except Exception as exc:
            pending.append({"action": "error", "name": name, "reason": str(exc)})

    store = get_entity_store()
    form_eid = formulation_draft_entity_id(source_id or str(draft.get("doc_id") or "unknown"))
    doc = get_source_store().get(source_id) if source_id else None
    doc_entity_id = None
    aliases = [
        f"origin:{origin}",
        "status:pending_review",
        f"amount_source:{amount_source}",
    ]
    if source_id:
        aliases.append(f"source:{source_id}")

    # Resolve document entity (patent or paper)
    if doc is not None:
        kind = (doc.source_kind or "").lower()
        if "patent" in kind or origin in {"patent_fulltext", "surechembl+fulltext"}:
            doc_entity_id = patent_entity_id_from_pub(doc.origin_url or doc.title or "")
            if not doc_entity_id:
                digest = hashlib.sha1(source_id.encode()).hexdigest()[:12]
                doc_entity_id = f"patent:kb:{digest}"[:64]
        else:
            doc_entity_id = paper_entity_id(doc)
            if not doc_entity_id:
                digest = hashlib.sha1(source_id.encode()).hexdigest()[:12]
                doc_entity_id = f"paper:kb:{digest}"[:64]

    links = 0
    with commit_session(store._session_factory) as session:
        if doc_entity_id and doc is not None:
            store.upsert_entity(
                session,
                id=doc_entity_id,
                kind="patent" if doc_entity_id.startswith("patent:") else "document",
                canonical_name=(doc.title or doc.filename or doc_entity_id)[:512],
                composition_status="resolved",
                aliases=[source_id, doc.origin_url or ""][:5],
            )
            # same_as via aliases toward SureChEMBL scpn when pub matches
            _office, compact = normalize_patent_pub(doc.origin_url or "")
            if compact:
                from . import surechembl_kg as sch_kg

                scpn_guess = None
                m = re.match(r"^([A-Z]{2})(\d+)([A-Z]\d*)?$", compact)
                if m:
                    scpn = f"{m.group(1)}-{m.group(2)}"
                    if m.group(3):
                        scpn = f"{scpn}-{m.group(3)}"
                    scpn_guess = sch_kg.patent_entity_id(scpn)
                patent_aliases = [source_id, compact]
                if scpn_guess:
                    patent_aliases.append(f"same_as:{scpn_guess}")
                    store.upsert_entity(
                        session,
                        id=scpn_guess,
                        kind="patent",
                        canonical_name=(doc.title or compact)[:512],
                        composition_status="resolved",
                        aliases=[f"same_as:{doc_entity_id}", compact],
                    )
                store.upsert_entity(
                    session,
                    id=doc_entity_id,
                    kind="patent",
                    canonical_name=(doc.title or compact)[:512],
                    composition_status="resolved",
                    aliases=patent_aliases,
                )

        store.upsert_entity(
            session,
            id=form_eid,
            kind="formulation",
            canonical_name=str((draft.get("formulation") or {}).get("name") or form_eid)[:512],
            composition_status="unknown",
            aliases=aliases,
        )

        if doc_entity_id:
            if store.merge_structural_link(
                session,
                src_entity_id=form_eid,
                dst_entity_id=doc_entity_id,
                link_type="appears_in",
                confidence=0.85,
                evidence_ref={
                    "source_id": source_id or None,
                    "chunk_id": None,
                    "sentence": "human-confirmed fulltext embodiment draft",
                    "confidence": 0.85,
                    "extraction_method": "fulltext_human",
                },
                metadata={
                    "origin": origin,
                    "status": "pending_review",
                    "amount_source": amount_source,
                    "source_id": source_id,
                },
                extraction_method="fulltext_human",
            ):
                links += 1

        for row in details:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            chem_id = chem_entity_id_from_name(name)
            store.upsert_entity(
                session,
                id=chem_id,
                kind="chemical",
                canonical_name=name[:512],
                smiles=(str(row["smiles"]).strip() if row.get("smiles") else None),
                composition_status="unknown",
                aliases=[],
            )
            if store.merge_structural_link(
                session,
                src_entity_id=form_eid,
                dst_entity_id=chem_id,
                link_type="has_ingredient",
                confidence=float(row.get("confidence") or 0.7),
                evidence_ref={
                    "source_id": source_id or None,
                    "chunk_id": None,
                    "sentence": name,
                    "confidence": float(row.get("confidence") or 0.7),
                    "extraction_method": "fulltext_human",
                },
                metadata={
                    "weight_pct": row.get("weight_pct"),
                    "placeholder_amount": placeholder,
                    "amount_source": amount_source,
                    "origin": origin,
                    "source_id": source_id,
                    "unit_raw": row.get("unit_raw"),
                },
                extraction_method="fulltext_human",
            ):
                links += 1
            if doc_entity_id and store.merge_structural_link(
                session,
                src_entity_id=chem_id,
                dst_entity_id=doc_entity_id,
                link_type="appears_in",
                confidence=0.7,
                evidence_ref={
                    "source_id": source_id or None,
                    "chunk_id": None,
                    "sentence": name,
                    "confidence": 0.7,
                    "extraction_method": "fulltext_human",
                },
                metadata={"origin": origin, "source_id": source_id},
                extraction_method="fulltext_human",
            ):
                links += 1

    return {
        "ok": True,
        "source_id": source_id,
        "doc_id": draft.get("doc_id"),
        "document_entity_id": doc_entity_id,
        "pending_materials": pending,
        "formulation_entity_id": form_eid,
        "links_added": links,
        "promoted_to_pool": False,
        "amount_source": amount_source,
        "note": "草稿已确认：原料进入 pending；配方实体标记 pending_review；未写入生产配方池",
    }
