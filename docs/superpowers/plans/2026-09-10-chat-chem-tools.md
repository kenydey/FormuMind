# Chat Chem Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable OpenAI-compatible native tool calling in FormuMind chat so the model can invoke chemtools, SureChemBL, RDKit structure search, and MolScribe (`recognize_structure(image_ref)`) during SSE streaming.

**Architecture:** New orchestration module `chat_chem_tools.py` owns tool schemas, allowlisted `image_ref` context, execution against existing services, and a streaming tool loop. `api/chat.py` wires it into `/api/chat/stream` (and sync `/api/chat`). Frontend handles new SSE `tool_start` / `tool_result` events. No ChemCrow/JSON-in-prompt; no Anthropic tools in v1.

**Tech Stack:** FastAPI, OpenAI Python SDK (chat.completions tools), existing `chemtools` / `structure_recognize` / `surechembl_*` / `structure_search` / `moljson`, React Zustand chat SSE.

**Spec:** `docs/superpowers/specs/2026-09-10-chat-chem-tools-design.md`

## Global Constraints

- Protocol: native OpenAI-compatible `tools` / `tool_calls` only (not DarkChuang JSON-in-prompt).
- Tool loop enable only when `chemtools_enabled` ∧ `chat_chem_tools_enabled` ∧ provider ∈ `_OPENAI_COMPAT_PROVIDERS`; else silent direct answer.
- `recognize_structure` accepts only allowlisted `image_ref` (sha / attachment id); reject paths, URLs, base64.
- Structured chat (`response_format=structured`) does **not** enter the tool loop in v1.
- SureChemBL tools are read-only; `surechembl_search` default limit ≤ 5.
- MolScribe timeout uses `settings.molscribe_timeout_s`; SSE `tool_start` for it must surface label「结构识别中」.
- Follow existing degrade patterns: return `{ok: false, hint: ...}` never uncaught crash from a single tool.
- Do not add 3D / NMR / Chroma / Anthropic tools in this plan.

---

## File map

| File | Role |
|---|---|
| Create `backend/app/services/chat_chem_tools.py` | Schemas, enable gate, `ChatToolContext`, `execute_tool`, streaming loop helpers |
| Create `backend/tests/test_chat_chem_tools.py` | Unit tests for gate, execute, allowlist |
| Create `backend/tests/test_chat_chem_tools_stream.py` | Mocked loop / SSE event order |
| Modify `backend/app/config.py` | `chat_chem_tools_enabled`, `chat_chem_tools_max_rounds` |
| Modify `backend/app/services/env_flags.py` | Register `chat_chem_tools_enabled` |
| Modify `backend/app/services/llm.py` | Thin helpers: messages+tools create/stream that surface `tool_calls` |
| Modify `backend/app/api/chat.py` | Build context; branch stream/sync into tool loop |
| Modify `frontend/src/api.ts` | `ChatStreamEvent` + `ChatMessage.toolStatus` |
| Modify `frontend/src/store/slices/searchSlice.ts` | Handle tool SSE events |
| Modify `frontend/src/components/ResearchPanel.tsx` | Show「结构识别中」/ tool status while streaming |

---

### Task 1: Config + EnvFlag

**Files:**
- Modify: `backend/app/config.py` (near other `chat_*` fields ~498–508)
- Modify: `backend/app/services/env_flags.py` (chem section after `chemtools_enabled`)
- Test: `backend/tests/test_env_flags.py` (extend registry assertion if present)

**Interfaces:**
- Produces: `Settings.chat_chem_tools_enabled: bool = True`, `Settings.chat_chem_tools_max_rounds: int = 4`; EnvFlag id `chat_chem_tools_enabled`

- [ ] **Step 1: Write failing assertion for new flag id**

In `backend/tests/test_env_flags.py`, add:

```python
def test_chat_chem_tools_flag_registered():
    ids = {f.id for f in env_flags.FLAG_REGISTRY}
    assert "chat_chem_tools_enabled" in ids
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd backend && python -m pytest tests/test_env_flags.py::test_chat_chem_tools_flag_registered -v`  
Expected: FAIL (id not in registry)

- [ ] **Step 3: Add Settings fields**

In `backend/app/config.py` after `chat_multi_turn_enabled` / chat block:

```python
    # Chat native chem tool-calling (OpenAI-compatible tools → chemtools / SureChemBL / OCSR).
    chat_chem_tools_enabled: bool = True
    chat_chem_tools_max_rounds: int = 4
```

- [ ] **Step 4: Register EnvFlag**

In `env_flags.py` after `chemtools_enabled`:

```python
    EnvFlag("chat_chem_tools_enabled", "聊天化学 Tool Calling",
            "对话中通过供应商原生 tools 自动调用 chemtools / SureChemBL / RDKit 结构检索 / MolScribe。"
            "仅 OpenAI 兼容供应商生效；关闭后聊天静默直答。",
            "chem", "需 chemtools_enabled；MolScribe/SureChemBL 另受各自开关约束"),
```

- [ ] **Step 5: Run test — expect PASS**

Run: `cd backend && python -m pytest tests/test_env_flags.py::test_chat_chem_tools_flag_registered -v`

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/services/env_flags.py backend/tests/test_env_flags.py
git commit -m "feat(config): add chat_chem_tools_enabled flag and max_rounds"
```

---

### Task 2: `chat_chem_tools` core — context, gate, schemas, execute

**Files:**
- Create: `backend/app/services/chat_chem_tools.py`
- Test: `backend/tests/test_chat_chem_tools.py`

**Interfaces:**
- Produces:
  - `@dataclass ChatToolContext`: `allowed_image_refs: set[str]`, `structure: dict | None`, `settings`
  - `def build_tool_context(*, structure: dict | None, attachment_source_ids: list[str], settings) -> ChatToolContext`
  - `def should_enable_tools(provider: str, settings) -> bool`
  - `def openai_tool_schemas(ctx: ChatToolContext) -> list[dict]`
  - `def execute_tool(name: str, args: dict, ctx: ChatToolContext) -> dict`  # always `{ok, ...}` or `{ok:false, hint}`
  - `def summarize_tool_result(name: str, result: dict) -> str`
  - `TOOL_LABELS: dict[str, str]` with `recognize_structure` → `"结构识别中"`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_chat_chem_tools.py`:

```python
from __future__ import annotations

from app.config import Settings
from app.services import chat_chem_tools as cct


def test_should_enable_tools_openai_compat(monkeypatch):
    s = Settings(chemtools_enabled=True, chat_chem_tools_enabled=True)
    assert cct.should_enable_tools("deepseek", s) is True
    assert cct.should_enable_tools("anthropic", s) is False
    s2 = Settings(chemtools_enabled=True, chat_chem_tools_enabled=False)
    assert cct.should_enable_tools("openai", s2) is False


def test_image_ref_allowlist_rejects_path():
    s = Settings()
    ctx = cct.build_tool_context(
        structure={"image_sha": "abc123"},
        attachment_source_ids=[],
        settings=s,
    )
    bad = cct.execute_tool(
        "recognize_structure",
        {"image_ref": "C:/evil/img.png"},
        ctx,
    )
    assert bad["ok"] is False
    assert "hint" in bad


def test_image_ref_allowlist_accepts_sha(monkeypatch):
    s = Settings(ocsr_enabled=True)
    ctx = cct.build_tool_context(
        structure={"image_sha": "deadbeef"},
        attachment_source_ids=[],
        settings=s,
    )

    def fake_recognize(content, **kwargs):
        return {
            "recognized": True,
            "smiles": "CCO",
            "image_sha": "deadbeef",
            "hits": [],
            "warnings": [],
        }

    monkeypatch.setattr(
        cct, "_load_image_bytes_for_ref", lambda ref, ctx: b"fake-png"
    )
    monkeypatch.setattr(
        "app.services.structure_recognize.recognize_structure_image",
        fake_recognize,
    )
    out = cct.execute_tool("recognize_structure", {"image_ref": "deadbeef"}, ctx)
    assert out["ok"] is True
    assert out.get("smiles") == "CCO"


def test_execute_mol_descriptors_degrades(monkeypatch):
    s = Settings(chemtools_enabled=True)
    ctx = cct.build_tool_context(structure=None, attachment_source_ids=[], settings=s)
    monkeypatch.setattr(
        "app.services.chemtools.mol_descriptors",
        lambda smiles: {"mol_wt": 46.0, "logp": -0.3, "tpsa": 20.0, "hbd": 1, "hba": 1, "arom_rings": 0},
    )
    out = cct.execute_tool("mol_descriptors", {"smiles": "CCO"}, ctx)
    assert out["ok"] is True
    assert out["properties"]["mol_wt"] == 46.0
```

- [ ] **Step 2: Run tests — expect FAIL (module missing)**

Run: `cd backend && python -m pytest tests/test_chat_chem_tools.py -v`  
Expected: FAIL import error

- [ ] **Step 3: Implement `chat_chem_tools.py` (minimal complete)**

Implement the module with:

1. `should_enable_tools`: import `_OPENAI_COMPAT_PROVIDERS` from `app.services.llm`; require both flags.
2. `build_tool_context`: collect refs from `structure.get("image_sha")` if non-empty str; plus each `attachment_source_ids` entry stripped; store as `frozenset`.
3. `openai_tool_schemas(ctx)`: return OpenAI function-tool dicts for all tools in the spec (chemtools set + validate_smiles + substructure_search + scaffold_substitutes + surechembl_* + recognize_structure). If `ctx.allowed_image_refs` empty, **omit** `recognize_structure` from the list.
4. `execute_tool`: dispatch table; wrap every call in try/except → `{ok:False, hint:str(exc)[:200]}`.
5. For `recognize_structure`: if `image_ref not in ctx.allowed_image_refs` → reject; else `_load_image_bytes_for_ref` then `recognize_structure.recognize_structure_image`.
6. `_load_image_bytes_for_ref`: resolve sha via structure_recognize shared cache/dir helpers if present; if attachment id, look up project/source blob path used elsewhere — if unresolved return None and execute returns ok false. Prefer reusing cache keyed by sha from `structure_recognize._cache_get` / shared image dir written at upload time. Document in code: if bytes cannot be loaded, hint「无法读取结构图附件」.
7. SureChemBL / structure_search / moljson / chemtools wrappers truncate lists to small top_k (default 5–10).
8. `summarize_tool_result` + `TOOL_LABELS`.

Keep the file focused; do not import DarkChuang code.

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd backend && python -m pytest tests/test_chat_chem_tools.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/chat_chem_tools.py backend/tests/test_chat_chem_tools.py
git commit -m "feat(chat): add chat_chem_tools schemas, allowlist, and execute dispatch"
```

---

### Task 3: LLM thin helpers for tools + stream aggregation

**Files:**
- Modify: `backend/app/services/llm.py`
- Test: `backend/tests/test_chat_chem_tools_stream.py` (partial — mock OpenAI client)

**Interfaces:**
- Consumes: OpenAI SDK `chat.completions.create`
- Produces:
  - `def iter_chat_with_tools_stream(*, messages, tools, api_key, model, base_url, max_tokens, on_text_delta, disable_thinking=False) -> dict`  
    Returns `{"kind": "message", "content": str}` or `{"kind": "tool_calls", "tool_calls": [{"id","name","arguments": dict}], "content": str}`
  - Aggregation must merge streamed `delta.tool_calls` chunks (index-based) into full name + arguments JSON string, then `json.loads`.

- [ ] **Step 1: Write failing unit test for tool_call aggregation helper**

If aggregation is a pure function `_merge_tool_call_deltas(acc, delta_tool_calls) -> acc`, test it in `test_chat_chem_tools_stream.py`:

```python
from app.services.llm import _merge_tool_call_deltas

def test_merge_tool_call_deltas():
    acc: dict = {}
    _merge_tool_call_deltas(acc, [{"index": 0, "id": "c1", "function": {"name": "mol_descriptors", "arguments": ""}}])
    _merge_tool_call_deltas(acc, [{"index": 0, "function": {"arguments": "{\"smiles\":"}}])
    _merge_tool_call_deltas(acc, [{"index": 0, "function": {"arguments": "\"CCO\"}"}}])
    calls = list(acc.values())
    assert calls[0]["name"] == "mol_descriptors"
    assert calls[0]["arguments"] == '{"smiles":"CCO"}'
```

(Adjust import path if helper lives in `chat_chem_tools` instead — prefer `llm.py` next to stream code.)

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement merge helper + `iter_chat_with_tools_stream`**

Reuse patterns from `_openai_compatible_stream` / `_openai_compatible_request` (timeout, `disable_thinking` for deepseek). When finish_reason is `tool_calls` or accumulated tool_calls non-empty, return tool_calls kind; else message kind. Call `on_text_delta` only for final-answer text deltas when no tool_calls in that completion (or when content arrives without tools).

Also add non-stream sibling `complete_chat_with_tools(...)` used by sync `/api/chat` and by loop rounds if needed.

- [ ] **Step 4: Run tests PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llm.py backend/tests/test_chat_chem_tools_stream.py
git commit -m "feat(llm): add OpenAI-compatible chat helpers that surface tool_calls"
```

---

### Task 4: Tool loop + wire `/api/chat/stream` and `/api/chat`

**Files:**
- Modify: `backend/app/services/chat_chem_tools.py` (add `run_tool_loop_events`)
- Modify: `backend/app/api/chat.py`
- Test: `backend/tests/test_chat_chem_tools_stream.py`

**Interfaces:**
- Produces: generator/iterator yielding dict events:
  - `{"type":"phase","phase":"tools"|"answering"}`
  - `{"type":"tool_start","name":...,"args":...,"label":...}`
  - `{"type":"tool_result","name":...,"ok":...,"summary":...}`
  - `{"type":"token","delta":...}`
  - `{"type":"loop_done","answer": str, "tools_used": list[str]}`
  - `{"type":"error","message":...}`
- Consumes: Task 2 execute/schemas; Task 3 stream helper; `Settings.chat_chem_tools_max_rounds`

- [ ] **Step 1: Write integration-style test with mocks**

```python
def test_tool_loop_emits_start_result_then_tokens(monkeypatch):
    from app.services import chat_chem_tools as cct
    from app.config import Settings

    s = Settings(chemtools_enabled=True, chat_chem_tools_enabled=True, chat_chem_tools_max_rounds=4)
    ctx = cct.build_tool_context(structure=None, attachment_source_ids=[], settings=s)

    rounds = {"n": 0}

    def fake_complete(**kwargs):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return {
                "kind": "tool_calls",
                "content": "",
                "tool_calls": [
                    {"id": "1", "name": "mol_descriptors", "arguments": {"smiles": "CCO"}}
                ],
            }
        return {"kind": "message", "content": "乙醇 LogP 约 -0.3。"}

    monkeypatch.setattr(cct, "complete_chat_with_tools", fake_complete)
    monkeypatch.setattr(
        cct,
        "execute_tool",
        lambda name, args, ctx: {"ok": True, "properties": {"logp": -0.3}},
    )

    events = list(
        cct.run_tool_loop_events(
            messages=[{"role": "user", "content": "乙醇 logp?"}],
            ctx=ctx,
            api_key="x",
            model="m",
            base_url=None,
            max_tokens=512,
            settings=s,
        )
    )
    types = [e["type"] for e in events]
    assert "tool_start" in types
    assert "tool_result" in types
    assert any(e["type"] == "loop_done" for e in events)
```

(If final tokens are streamed, also assert token events; for sync complete helper, `loop_done.answer` is enough.)

- [ ] **Step 2: Implement `run_tool_loop_events`**

Algorithm:
1. Yield `phase=tools`.
2. For round in `1..max_rounds`: call complete/stream helper with current messages + schemas.
3. On tool_calls: for each call yield tool_start (label from TOOL_LABELS), execute_tool, yield tool_result, append assistant tool_calls message + tool role messages (OpenAI format).
4. On message: yield phase answering; if streaming deltas already forwarded as token events, still yield loop_done with full answer; else yield tokens by chunking or single token then loop_done.
5. On tools API 4xx: yield nothing fatal from loop — caller falls back to existing direct stream (document in chat.py).

- [ ] **Step 3: Wire `chat_stream` in `api/chat.py`**

After retrieval meta, when markdown path and `should_enable_tools(provider, settings)`:

```python
from ..services.chat_chem_tools import (
    build_tool_context,
    should_enable_tools,
    run_tool_loop_events,
    openai_tool_schemas,
)

# build messages from plan["prompt"] / history — prefer converting _chat_prompt
# into system+user messages inside chat_chem_tools.build_messages(...)
ctx = build_tool_context(
    structure=req.structure,
    attachment_source_ids=req.attachment_source_ids,
    settings=settings,
)
if should_enable_tools(provider, settings) and openai_tool_schemas(ctx) is not None:
    tools_used: list[str] = []
    answer_acc = ""
    try:
        for ev in run_tool_loop_events(...):  # may run in to_thread + queue like today
            if ev["type"] == "token":
                answer_acc += ev["delta"]
                yield _sse(ev)
            elif ev["type"] in ("phase", "tool_start", "tool_result"):
                yield _sse(ev)
            elif ev["type"] == "loop_done":
                answer_acc = ev["answer"] or answer_acc
                tools_used = ev.get("tools_used") or []
            elif ev["type"] == "error":
                # fall through to legacy stream OR yield error
                ...
        # then existing claims/done packaging using answer_acc
    except Exception as tools_exc:
        logger.warning("chem tools loop failed, fallback direct stream: %s", tools_exc)
        # existing answering stream path
else:
    # existing answering stream path
```

Preserve existing claims phase after answer when enabled.

For sync `/api/chat`: same gate; run loop without SSE; put answer into ChatResponse; optional `tools_used` ignored by schema unless you extend ChatResponse (spec says optional on done only — sync can omit).

- [ ] **Step 4: Run tests**

`cd backend && python -m pytest tests/test_chat_chem_tools.py tests/test_chat_chem_tools_stream.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/chat_chem_tools.py backend/app/api/chat.py backend/tests/test_chat_chem_tools_stream.py
git commit -m "feat(chat): wire native chem tool loop into chat and chat/stream"
```

---

### Task 5: Frontend SSE + UI status

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/store/slices/searchSlice.ts`
- Modify: `frontend/src/components/ResearchPanel.tsx`

**Interfaces:**
- Extends `ChatStreamEvent` with tool events and `phase: "tools"`
- Extends `ChatMessage` with `toolStatus?: string | null`
- `sendChat` updates `toolStatus` on tool_start/result; clears on answering/done

- [ ] **Step 1: Update types in `api.ts`**

```typescript
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Evidence[];
  kbChunksUsed?: number;
  streaming?: boolean;
  phase?: string;
  /** Live tool status while streaming (e.g. 结构识别中). */
  toolStatus?: string | null;
}

export type ChatStreamEvent =
  | { type: "phase"; phase: "retrieval" | "tools" | "answering" | "claims" }
  | { type: "meta"; kb_used: number; rewritten_query?: string | null; source_count?: number }
  | { type: "tool_start"; name: string; args: Record<string, unknown>; label?: string }
  | { type: "tool_result"; name: string; ok: boolean; summary: string }
  | { type: "token"; delta: string }
  | {
      type: "done";
      answer: string;
      citations?: Evidence[];
      kb_chunks_used?: number;
      clarification?: unknown;
      rewritten_query?: string | null;
      sourced_claims?: unknown;
      structured?: unknown;
      tools_used?: string[];
    }
  | { type: "error"; message: string };
```

- [ ] **Step 2: Handle events in `searchSlice.sendChat`**

```typescript
} else if (ev.type === "tool_start") {
  set((draft) => {
    const m = last(draft);
    if (m?.role === "assistant" && m.streaming) {
      m.phase = "tools";
      m.toolStatus = ev.label || ev.name;
    }
  });
} else if (ev.type === "tool_result") {
  set((draft) => {
    const m = last(draft);
    if (m?.role === "assistant" && m.streaming) {
      m.toolStatus = ev.ok ? null : `工具失败: ${ev.name}`;
    }
  });
}
```

On `phase === "answering"` or `done`, clear `toolStatus`.

- [ ] **Step 3: ResearchPanel streaming UI**

Extend the streaming status block:

```tsx
{m.phase === "tools" ? (
  <span className="text-accent2 text-xs ml-1">
    ⏳ {m.toolStatus || "调用化学工具…"}
  </span>
) : m.phase === "retrieval" ? (
  ...
```

Ensure `recognize_structure` shows「结构识别中」via backend `label`.

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`  
Expected: 0 errors related to these changes

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/store/slices/searchSlice.ts frontend/src/components/ResearchPanel.tsx
git commit -m "feat(frontend): show chem tool SSE status in chat stream"
```

---

### Task 6: Attachment ref plumbing + image bytes loader hardening

**Files:**
- Modify: `backend/app/services/chat_chem_tools.py` (`_load_image_bytes_for_ref`)
- Modify: `frontend/src/store/slices/searchSlice.ts` (ensure `structure` passed includes `image_sha` — already on `StructureRecognitionResult`)
- Test: extend `test_chat_chem_tools.py`

**Interfaces:**
- `_load_image_bytes_for_ref(ref, ctx) -> bytes | None` must resolve:
  1. sha equal to `structure["image_sha"]` → read from OCSR/shared upload path used by `structure_recognize` (same file written before Celery dispatch), or from redis/file cache if bytes still available
  2. else treat ref as attachment source id only if in allowlist — load via existing ingest/source file API/store

- [ ] **Step 1: Failing test when loader returns None for unknown sha**

```python
def test_recognize_unknown_sha_hint():
    s = Settings(ocsr_enabled=True)
    ctx = cct.build_tool_context(
        structure={"image_sha": "deadbeef"},
        attachment_source_ids=[],
        settings=s,
    )
    # allowlisted but bytes missing
    out = cct.execute_tool("recognize_structure", {"image_ref": "deadbeef"}, ctx)
    assert out["ok"] is False
```

(May already pass if Step Task2 stub; harden real loader.)

- [ ] **Step 2: Implement real path resolution** using `structure_recognize._shared_dir` and naming convention from recognize pipeline (read that file for exact filename pattern `{sha}.png` etc.).

- [ ] **Step 3: Manual checklist note in commit body** — with molscribe worker up, upload structure image in UI, ask「请识别附图结构并给出官能团」.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/chat_chem_tools.py backend/tests/test_chat_chem_tools.py
git commit -m "fix(chat): resolve recognize_structure image_ref bytes from allowlisted sha"
```

---

### Task 7: Regression + availability surfacing

**Files:**
- Modify: `backend/app/api/chemistry.py` or `chemtools.availability()` — optional nested key `chat_tools` describing enablement (provider-agnostic capability list is enough; provider check is runtime)
- Test: `backend/tests/test_chemtools.py` or small addition
- Run full related suite

- [ ] **Step 1: Extend `/api/chemical/tools` JSON** with:

```python
"chat_tool_calling": {
    "flag_enabled": bool(settings.chat_chem_tools_enabled),
    "chemtools_enabled": bool(settings.chemtools_enabled),
    "note": "实际启用还取决于当前 LLM 是否为 OpenAI 兼容供应商",
}
```

- [ ] **Step 2: Run suites**

```bash
cd backend && python -m pytest tests/test_chat_chem_tools.py tests/test_chat_chem_tools_stream.py tests/test_env_flags.py tests/test_chemtools.py tests/test_chat_compat.py -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/chemtools.py backend/app/api/chemistry.py backend/tests
git commit -m "feat(chem): expose chat tool-calling status on chemical tools availability"
```

---

## Spec coverage self-check

| Spec requirement | Task |
|---|---|
| Native tool calling, not JSON prompt | 3, 4 |
| chemtools tool set + safety_flags/chemical_profile | 2 |
| SureChemBL lookup/similar/search | 2 |
| RDKit validate/substructure/scaffold | 2 |
| MolScribe image_ref allowlist + long timeout + label | 2, 5, 6 |
| SSE tool_start/result + token stream | 4, 5 |
| OpenAI-compat only; silent fallback | 2 gate, 4 wire |
| Flag + max_rounds | 1 |
| Structured chat skips tools | 4 |
| Settings visibility | 7 |

## Placeholder scan

No TBD/TODO steps; commands and primary code shapes included. Implementers must still match exact OpenAI message shapes for `tool_calls` to the SDK version in use — verify against installed `openai` package during Task 3.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-10-chat-chem-tools.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute in this session with executing-plans checkpoints  

Which approach?
