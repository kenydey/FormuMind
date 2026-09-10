# Embodiment Table Semantics (A+B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On new embodiment extracts only, reject citation/prior-art tables and prefer real formulation tables; fix `origin`/`source_kind` so patents are not mislabeled as literature.

**Architecture:** Harden `table_to_ingredients` / `_column_map` with row/header rejects; score surviving tables instead of densest-wins; wire `_infer_role` and dirty-name confidence caps; tighten `_origin_for_kind` + `_persist_fulltext` patent kind. No re-extract UX, no hot-path OCR, no F4/Slice E.

**Tech Stack:** Python / FastAPI backend; existing `embodiment_drafts.py`, `fulltext_fetcher.py`, `formulation_linker._infer_role`, `patent_ids.normalize_patent_pub`; pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-embodiment-table-semantics-design.md`

## Global Constraints

- New extracts only — do not mutate stored drafts; no「按新规则重提」button.
- Extract path must not download or OCR; only read ingested `full_text` / chunks.
- Empty draft (`ingredients=[]`) is preferred over a wrong citation table.
- `document_fulltext` must be in confirm `_ALLOWED_ORIGINS` or confirm returns 400.
- Frontend `origin` type already allows `string` — no required UI change; optional label polish only if trivial.
- Keep F0–F3 behavior for no-table / flattened prose paths.
- Do not change default `patent_prefer_html` or cloud parse profile in this plan.

---

## File map

| File | Role |
|------|------|
| Modify `backend/app/services/embodiment_drafts.py` | A2/A1/A3/A4 + B1 + optional `text_provenance` warning |
| Modify `backend/app/services/fulltext_fetcher.py` | B2: persist `source_kind=patent` when identifier normalizes |
| Modify `backend/tests/test_embodiment_drafts.py` | T1–T5, T7 regression |
| Modify `backend/tests/test_fulltext_fetcher.py` | T6 persist kind |
| Update `docs/superpowers/specs/2026-09-10-embodiment-table-semantics-design.md` | Status → implemented when done |

---

### Task 1: Hard rejects in table parsing (A2 / T1)

**Files:**
- Modify: `backend/app/services/embodiment_drafts.py` (`_column_map`, `table_to_ingredients`, new helpers near top)
- Test: `backend/tests/test_embodiment_drafts.py`

**Interfaces:**
- Produces: `_PUB_NO_NAME_RE`, `_FORBIDDEN_NAME_HEADERS`, `_is_publication_number(name)`, `_looks_like_year_amount(num, unit_hint)`, stricter `_column_map` / `table_to_ingredients`
- Consumes: existing `_parse_number`, `_NAME_HEADERS`, `_AMOUNT_HEADERS`

- [ ] **Step 1: Write failing tests for citation tables**

Append to `backend/tests/test_embodiment_drafts.py`:

```python
def test_citation_table_rejected():
    md = """
| Patent | Title | Year |
| --- | --- | --- |
| DE102011120870B4 | Prior art coating | 2012 |
| CN102528001A | Magnesium alloy | 2011 |
| US8608869B2 | Surface treatment | 2013 |
"""
    tables = emb.parse_markdown_tables(md)
    assert emb.table_to_ingredients(tables[0]) is None


def test_formulation_table_still_accepted():
    md = """
Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 40 |
| Zinc phosphate | 25 |
| Solvent | 35 |
"""
    tables = emb.parse_markdown_tables(md)
    row = emb.table_to_ingredients(tables[0])
    assert row is not None
    assert [i["name"] for i in row["ingredients"]] == [
        "Epoxy resin",
        "Zinc phosphate",
        "Solvent",
    ]
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd backend && python -m pytest tests/test_embodiment_drafts.py::test_citation_table_rejected tests/test_embodiment_drafts.py::test_formulation_table_still_accepted -v`  
Expected: `test_citation_table_rejected` FAIL (currently parses pub numbers as ingredients)

- [ ] **Step 3: Add helpers + harden `_column_map` / `table_to_ingredients`**

In `embodiment_drafts.py` after `_NUM_RE`:

```python
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
```

Rewrite `_column_map` so:

1. Never set `name_idx` from a header matching `_FORBIDDEN_NAME_HEADERS`.
2. If no header matches `_NAME_HEADERS` / component fuzzy → `name_idx = None` (**do not** fallback to 0).
3. For `amt_idx`, keep amount-header / wt fuzzy matches; only fallback to last column if ≥ half of body cells parse as numbers **and** not year-like under that header.

In `table_to_ingredients`, skip rows where `_is_publication_number(name)` or `_looks_like_year_amount(num, unit_hint)`; if `<2` valid rows return `None`.

- [ ] **Step 4: Run Task 1 tests — expect PASS**

Run: `cd backend && python -m pytest tests/test_embodiment_drafts.py::test_citation_table_rejected tests/test_embodiment_drafts.py::test_formulation_table_still_accepted tests/test_embodiment_drafts.py::test_parse_markdown_table_to_real_wt tests/test_embodiment_drafts.py::test_parts_normalized -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/embodiment_drafts.py backend/tests/test_embodiment_drafts.py
git commit -m "fix(embodiment): reject citation tables as formulation sources"
```

---

### Task 2: Table scoring replace densest-wins (A1 / T2)

**Files:**
- Modify: `backend/app/services/embodiment_drafts.py` (`score_embodiment_table`, `extract_embodiment_draft` sort)
- Test: `backend/tests/test_embodiment_drafts.py`

**Interfaces:**
- Consumes: `table_to_ingredients` output + original table headers/label
- Produces: `score_embodiment_table(table, emb_row) -> float`; extract sorts by `(-score, -n, label)`; drop `score < 0`

- [ ] **Step 1: Write failing dual-table extract test**

```python
def test_extract_prefers_formulation_over_citation_table(stores):
    sources, chunks, _ = stores
    md = (
        "Patent body with enough characters for eligibility. " * 30
        + """
Related patents
| Patent | Title | Year |
| --- | --- | --- |
| DE102011120870B4 | Prior coating | 2012 |
| CN102528001A | Mg alloy | 2011 |
| US8608869B2 | Surface | 2013 |
| EP1234567A1 | Film | 2010 |
| WO2010123456A1 | Bath | 2010 |

Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 55 |
| Zinc phosphate | 15 |
| Talc | 30 |
"""
    )
    sid = _seed_doc(sources, chunks, text=md)
    out = emb.extract_embodiment_draft(source_id=sid)
    assert out["ok"] is True
    names = [i["name"] for i in out["draft"]["formulation"]["ingredients"]]
    assert "Epoxy resin" in names
    assert not any(emb._is_publication_number(n) for n in names)
    assert any("语义" in w or "表语义" in w for w in out["draft"]["warnings"])
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd backend && python -m pytest tests/test_embodiment_drafts.py::test_extract_prefers_formulation_over_citation_table -v`  
Expected: FAIL (densest citation wins or citation still present) until scoring lands; if Task 1 already rejects citation entirely, assert may pass early — still implement scoring + warning text.

- [ ] **Step 3: Implement `score_embodiment_table` and wire extract**

```python
def score_embodiment_table(table: dict[str, Any], emb_row: dict[str, Any]) -> float:
    score = 0.0
    label = str(table.get("label") or emb_row.get("label") or "")
    headers = " ".join(table.get("headers") or [])
    if _EXAMPLE_HEAD.search(label) or re.search(r"配方", label):
        score += 3.0
    name_ok = bool(re.search(r"component|ingredient|原料|组分|成分", headers, re.I))
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
```

In `extract_embodiment_draft`, when collecting:

```python
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
```

Replace densest warning with:

```python
warnings.append(
    f"检测到 {len(embodiments)} 个可用表；默认按表语义分展示，其余可在人审中切换"
)
```

Strip `_score` before returning draft if preferred (or leave — UI ignores unknown keys). Prefer pop:

```python
for e in embodiments:
    e.pop("_score", None)
```

- [ ] **Step 4: Run Task 2 test — expect PASS**

Run: `cd backend && python -m pytest tests/test_embodiment_drafts.py::test_extract_prefers_formulation_over_citation_table -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/embodiment_drafts.py backend/tests/test_embodiment_drafts.py
git commit -m "feat(embodiment): score tables by semantics instead of densest"
```

---

### Task 3: Dirty names + role inference (A3/A4 / T3/T4)

**Files:**
- Modify: `backend/app/services/embodiment_drafts.py` (`table_to_ingredients`, formulation role fallback)
- Test: `backend/tests/test_embodiment_drafts.py`

**Interfaces:**
- Consumes: `app.services.kg.formulation_linker._infer_role`
- Produces: `role` from linker; dirty-name `confidence <= 0.45` + warning

- [ ] **Step 1: Write failing tests**

```python
def test_dirty_name_lowers_confidence_and_warns():
    md = """
Example 2
| Component | wt% |
| --- | --- |
| Water and its preparation method | 40 |
| Epoxy resin | 60 |
"""
    tables = emb.parse_markdown_tables(md)
    row = emb.table_to_ingredients(tables[0])
    dirty = next(i for i in row["ingredients"] if "preparation" in i["name"].lower())
    assert dirty["confidence"] <= 0.45
    assert any("标题污染" in w or "名称可能" in w for w in row["warnings"])


def test_role_inferred_not_always_additive():
    md = """
| Component | wt% |
| --- | --- |
| Epoxy resin | 50 |
| Solvent naphtha | 50 |
"""
    tables = emb.parse_markdown_tables(md)
    row = emb.table_to_ingredients(tables[0])
    roles = {i["name"]: i["role"] for i in row["ingredients"]}
    assert roles["Epoxy resin"] == "resin"
    assert roles["Solvent naphtha"] == "solvent"
    assert "additive" not in roles.values() or roles.get("Epoxy resin") != "additive"
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd backend && python -m pytest tests/test_embodiment_drafts.py::test_dirty_name_lowers_confidence_and_warns tests/test_embodiment_drafts.py::test_role_inferred_not_always_additive -v`  
Expected: FAIL (role still additive; no dirty warning)

- [ ] **Step 3: Wire role + dirty-name in `table_to_ingredients`**

Inside the ingredient loop:

```python
    from .kg.formulation_linker import _infer_role

    ...
        role = _infer_role(name)
        conf = 0.85 if amount_source == "table" else 0.4
        extra_warnings: list[str] = []
        if _DIRTY_NAME_RE.search(name):
            conf = min(conf, 0.45)
            extra_warnings.append("名称可能含标题污染，请核对原件")
        ingredients.append({..., "role": role, "confidence": conf, ...})
    warnings = list(warnings) + extra_warnings  # merge unique
```

Also change draft builder fallback from `ing.get("role") or "additive"` to `ing.get("role") or "unknown"` (two places if F3/`_placeholder_from_names` still hardcode additive — update F3 path similarly for consistency; placeholders may keep low-confidence `unknown`).

For F3 `parse_flattened_amount_rows`, set `role = _infer_role(name)` the same way.

- [ ] **Step 4: Run Task 3 tests + prior table tests — expect PASS**

Run: `cd backend && python -m pytest tests/test_embodiment_drafts.py -k "dirty_name or role_inferred or citation or formulation_table or prefers_formulation or parse_markdown or parts_normalized" -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/embodiment_drafts.py backend/tests/test_embodiment_drafts.py
git commit -m "feat(embodiment): infer roles and flag dirty ingredient names"
```

---

### Task 4: Origin + persist source_kind (B1/B2 / T5/T6)

**Files:**
- Modify: `backend/app/services/embodiment_drafts.py` (`_ALLOWED_ORIGINS`, `_origin_for_kind`, call site)
- Modify: `backend/app/services/fulltext_fetcher.py` (`_persist_fulltext`)
- Test: `backend/tests/test_embodiment_drafts.py`, `backend/tests/test_fulltext_fetcher.py`

**Interfaces:**
- Produces: origin `document_fulltext`; `_origin_for_kind(..., doc=None)`; persist overrides kind to `patent` when pub normalizes

- [ ] **Step 1: Write failing origin + persist tests**

```python
# test_embodiment_drafts.py
def test_origin_web_is_document_fulltext_not_literature():
    assert emb._origin_for_kind("web") == "document_fulltext"
    assert emb._origin_for_kind("local") == "document_fulltext"
    assert emb._origin_for_kind("literature") == "literature_fulltext"


def test_origin_from_patent_filename(stores):
    sources, chunks, _ = stores
    text = ("Eligible body. " * 40) + """
Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 70 |
| Solvent | 30 |
"""
    sid = sources.create(
        filename="CN120693379A.pdf",
        title="一种水性分散体防腐保护涂层组合物",
        source_kind="web",
        full_text=text,
        content_hash=f"h-origin-{hash(text) & 0xFFFFFFFF:x}",
        extraction_status="ok",
        origin_url="https://patents.google.com/patent/CN120693379A",
    )
    with chunks._session_factory() as session:
        chunks.replace_for_source_in(session, sid, [{"text": text, "heading_path": "", "page_no": 1, "meta": {}}])
        session.commit()
    out = emb.extract_embodiment_draft(source_id=sid)
    assert out["draft"]["origin"] == "patent_fulltext"
```

```python
# test_fulltext_fetcher.py
def test_persist_forces_patent_kind_for_pub_id(monkeypatch, tmp_path):
    # Use existing stores fixture pattern from file, or monkeypatch store.create
    created = {}

    class FakeStore:
        def find_by_hash(self, h):
            return None
        def create(self, **kw):
            created.update(kw)
            return "sid-1"

    monkeypatch.setattr("app.db.source_store.get_source_store", lambda: FakeStore())
    monkeypatch.setattr("app.services.kb_index.index_source", lambda *a, **k: None)
    ev = Evidence(
        source="internet",
        identifier="CN104789083B",
        title="demo",
        snippet="x",
        relevance=0.9,
        url="https://patents.google.com/patent/CN104789083B",
    )
    ff._persist_fulltext("full text body " * 40, ev, "web")
    assert created["source_kind"] == "patent"
```

Adapt `Evidence` import / FakeStore to match existing `test_persist_fulltext_retries_on_db_locked` style in the same file.

- [ ] **Step 2: Run — expect FAIL**

Run: `cd backend && python -m pytest tests/test_embodiment_drafts.py::test_origin_web_is_document_fulltext_not_literature tests/test_embodiment_drafts.py::test_origin_from_patent_filename tests/test_fulltext_fetcher.py::test_persist_forces_patent_kind_for_pub_id -v`  
Expected: FAIL

- [ ] **Step 3: Implement B1/B2**

`_ALLOWED_ORIGINS` add `"document_fulltext"`.

```python
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
```

Call site:

```python
origin = _origin_for_kind(doc.source_kind if doc else None, surechembl=surechembl_hint, doc=doc)
```

In `_persist_fulltext`:

```python
    persist_kind = kind
    _office, compact = normalize_patent_pub(ev.identifier)
    if not compact:
        _office, compact = normalize_patent_pub(getattr(ev, "url", None) or "")
    if compact:
        persist_kind = "patent"
    ...
                source_kind=persist_kind,
```

(`normalize_patent_pub` already imported in that function via local import — reuse / hoist consistently.)

- [ ] **Step 4: Run origin/persist tests — expect PASS**

Run: `cd backend && python -m pytest tests/test_embodiment_drafts.py::test_origin_web_is_document_fulltext_not_literature tests/test_embodiment_drafts.py::test_origin_from_patent_filename tests/test_embodiment_drafts.py::test_extract_and_confirm tests/test_fulltext_fetcher.py::test_persist_forces_patent_kind_for_pub_id -v`  
Expected: PASS (`confirm` still accepts patent + new document origin)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/embodiment_drafts.py backend/app/services/fulltext_fetcher.py backend/tests/test_embodiment_drafts.py backend/tests/test_fulltext_fetcher.py
git commit -m "fix(embodiment): label patent origin and persist source_kind correctly"
```

---

### Task 5: Light provenance warning + full regression (B3 / T7)

**Files:**
- Modify: `backend/app/services/embodiment_drafts.py` (`extract_embodiment_draft` warnings)
- Test: full `test_embodiment_drafts.py` + related fulltext / pdf downloader embodiment tests

**Interfaces:**
- Produces: optional draft key `text_provenance` ∈ `{markdown_table, html_table, prose, none}` and/or a single warning line

- [ ] **Step 1: Add provenance hint when amount_source is table/prose**

```python
    if amount_source == "table":
        text_provenance = "markdown_table" if md_tables else "html_table"
    elif amount_source == "prose":
        text_provenance = "prose"
    else:
        text_provenance = "none"
    draft["text_provenance"] = text_provenance
```

No new failing test required beyond asserting key present in one existing extract test:

```python
    assert out["draft"].get("text_provenance") in {"markdown_table", "html_table", "prose", "none"}
```

Add that assert to `test_extract_and_confirm`.

- [ ] **Step 2: Run full related suites**

Run:

```bash
cd backend && python -m pytest tests/test_embodiment_drafts.py tests/test_fulltext_fetcher.py tests/test_pdf_downloader.py -q --tb=line
```

Expected: all PASS

- [ ] **Step 3: Mark spec status implemented**

In `docs/superpowers/specs/2026-09-10-embodiment-table-semantics-design.md` header: `状态：已实现（仅新提取）`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/embodiment_drafts.py backend/tests/test_embodiment_drafts.py docs/superpowers/specs/2026-09-10-embodiment-table-semantics-design.md
git commit -m "chore(embodiment): add text_provenance and close A+B spec"
```

---

## Self-review (plan vs spec)

| Spec item | Task |
|-----------|------|
| A2 hard rejects | Task 1 |
| A1 scoring / no densest | Task 2 |
| A3 dirty names | Task 3 |
| A4 roles | Task 3 |
| B1 origin + `document_fulltext` | Task 4 |
| B2 persist patent kind | Task 4 |
| B3 provenance | Task 5 |
| New extract only / no OCR / no F4 | Global constraints |
| T1–T7 | Tasks 1–5 |

No TBD placeholders; types/names consistent (`document_fulltext`, `_is_publication_number`, `score_embodiment_table`).
