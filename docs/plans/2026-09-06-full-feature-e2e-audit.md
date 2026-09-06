# FormuMind Full-Feature E2E Audit

> Date: 2026-09-06  
> Branch: `cursor/full-feature-e2e-audit-5ace`  
> Scope: end-to-end product path with user-provided `.env` (secrets never committed)  
> Method: API harness + Playwright UI smoke + backend log correlation  
> Policy: discover → root-cause → graded fix plan; **no product code fixes** in this PR

---

## 0. Executive summary

| Verdict | Count |
|---------|------:|
| Feature areas PASS | 6 / 12 |
| Feature areas FAIL / partial | 5 / 12 |
| Feature areas BLOCKED | 1 / 12 |
| Critical (P0) defects | **2** |
| High (P1) defects | 3 |
| Medium (P2) defects | 4 |

**Bottom line:** With the uploaded DeepSeek + search keys, core **sync** paths work (intent, search, KB, chat API, DOE, materials list/upsert, LLM recommend). The closed-loop product path is **broken for interactive async UX** because `FORMUMIND_CELERY_EAGER=true` makes `task.delay()` run the full job inline, and `_dispatch._delay_with_timeout(..., 10s)` returns **false 503** while the task often still finishes later. Separately, the uploaded `FORMUMIND_DATALAB_REQUIRED=true` hard-fails lab/optimize/recommend until Datalab is up or the flag is relaxed.

---

## 1. Environment setup status

| Item | Status | Notes (secrets redacted) |
|------|--------|--------------------------|
| Backend `uvicorn :8000` | UP | `/health` → `ok` after pass-2 config |
| Frontend Vite `:5173` | UP | HTTP 200 |
| Redis `:6379` | Started for audit | Was **down** at first boot; installed `redis-server` and daemonized |
| Celery worker | Optional | Started for diagnosis; not required if eager path is fixed |
| LLM provider | **PASS** | `deepseek` / `deepseek-v4-flash`; `POST /api/settings/test` → `ok: true` (key `...aa4a`) |
| Vision | Configured | provider `deepseek`, model `deepseek-v4-flash-vision-exp` (key redacted) |
| Tavily / SerpAPI / EPO / USPTO / MinerU | Present in env | Search status: patents/literature/internet/tavily/serpapi/epo available |
| ChemCrow / NotebookLM extras | Missing | `library_missing` — non-blocking |
| Neo4j | Configured in env | Not required for primary SQLite KG path in this run |
| Datalab ELN `:5001` | **DOWN** | User env had `FORMUMIND_DATALAB_REQUIRED=true` |
| Auth | Disabled | `auth_required: false` |
| `FORMUMIND_CELERY_EAGER` | `true` | Implies in-process tasks; see P0-1 |
| Pass-2 override (testing only) | Applied | `DATALAB_REQUIRED=false`, `CAMPAIGN_BACKEND=sqlite`, `EXPERIMENT_BACKEND=sqlite` to continue product-path tests when Datalab absent |

Artifacts: `/opt/cursor/artifacts/e2e_api_results.json`, `e2e_api_pass2_results.json`, `e2e_api_pass3_async.json`, `backend_log_fingerprints.txt`, UI PNGs under `/opt/cursor/artifacts/ui/`.

---

## 2. Step-by-step results

Legend: **PASS** / **FAIL** / **BLOCKED** / **SKIPPED**. API and UI noted separately when they diverge.

### 1) Enter research topic

| Path | Result | Evidence |
|------|--------|----------|
| API `POST /api/intent/parse` | **PASS** | HTTP 200; domain `anticorrosion_coating`, objectives parsed |
| UI topic textarea | **PASS** | `/opt/cursor/artifacts/ui/02_topic.png` |

### 2) Search / retrieve materials

| Path | Result | Evidence |
|------|--------|----------|
| API `POST /api/search` (sync) | **PASS** | HTTP 200; evidence returned (patents/literature/internet) |
| API `POST /api/search/stream` | **FAIL** | HTTP **503**; detail contains `dispatch 超时: task.delay > 10.0s` |
| UI「开始检索」 | **PASS** (click/UI) | `/opt/cursor/artifacts/ui/03_search.png`; incremental stream may still 503 under eager |

Log fingerprint:

```text
ERROR app.api._dispatch:submit - celery dispatch timed out for search: task.delay > 10.0s
```

### 3) Knowledge base build

| Path | Result | Evidence |
|------|--------|----------|
| `POST /api/ingest/text` | **PASS** | HTTP 200 |
| `GET /api/kb/stats`, `/api/kb/integrity`, `/api/kb/search` | **PASS** | HTTP 200 |
| `GET /api/research/rag/status` | **PASS** | backend `bm25_faiss` |

### 4) Q&A

| Path | Result | Evidence |
|------|--------|----------|
| API `POST /api/chat` | **PASS** | HTTP 200; non-empty answer with DeepSeek |
| UI middle-column chat | **BLOCKED** | textarea `disabled` placeholder「请先加载资料…」 until session sources loaded (`/opt/cursor/artifacts/ui/04_chat_state.png`) |

### 5) Technical requirements entry

| Path | Result | Evidence |
|------|--------|----------|
| Intent + `/api/templates/anticorrosion_coating` | **PASS** | HTTP 200 |
| UI panel `open-requirements` | **PASS** | `/opt/cursor/artifacts/ui/05_requirements.png` |

### 6) Formula recommendation

| Path | Result | Evidence |
|------|--------|----------|
| `POST /api/formulations/recommend` (with Datalab required) | **FAIL** | HTTP **503** Datalab unreachable |
| Same endpoint after `DATALAB_REQUIRED=false` | **PASS** | HTTP 200; `engine=llm`; `returned_n≥2`; key is **`formulas`** (not `formulations`) |
| `POST /api/research/recommend` async | **FAIL** | HTTP **503** eager dispatch timeout |
| UI recommend panel open | **PASS** | `/opt/cursor/artifacts/ui/06_recommend.png` |

### 7) DOE design

| Path | Result | Evidence |
|------|--------|----------|
| `POST /api/doe?design=lhs` | **PASS** | HTTP 200; plan + runs |
| `GET /api/doe/{id}/export?format=csv` | **PASS** | HTTP 200 |
| `POST /api/doe/active` | **PASS** (pass-2, flat Requirement body) | HTTP 200 |
| UI DOE panel | **PASS** | `/opt/cursor/artifacts/ui/07_doe.png` |

### 8) Lab workbench / experiment ledger

| Path | Result | Evidence |
|------|--------|----------|
| With Datalab required | **FAIL** | workbench create / experiments POST → **503** Datalab |
| After sqlite backends | **PASS** | campaign create + `POST /api/experiments` + list |
| UI lab panel | **PASS** | `/opt/cursor/artifacts/ui/08_lab.png` |

### 9) Optimization / convergence

| Path | Result | Evidence |
|------|--------|----------|
| `POST /api/optimize` | **FAIL** (client) | HTTP **503** at 10.0s |
| Backend reality | Partial | Log shows `Task formumind.optimize[...] succeeded in ~15.5s` **after** client 503 |
| `GET /api/models` | **PASS** after sqlite override; **FAIL** under Datalab required | |
| UI optimize panel | **PASS** (opens) | `/opt/cursor/artifacts/ui/09_optimize.png` |

### 10) Self-driven closed loop

| Path | Result | Evidence |
|------|--------|----------|
| `POST /api/loop/iterate` | **FAIL** (client) | HTTP **503** at 10.0s |
| Backend reality | Partial | `Task formumind.loop[...] succeeded in ~37.6s` after client 503 |
| UI loop panel | **PASS** (opens) | `/opt/cursor/artifacts/ui/10_loop.png` |

### 11) Materials library

| Path | Result | Evidence |
|------|--------|----------|
| `GET /api/materials` | **PASS** | HTTP 200 (hidden from OpenAPI schema) |
| `POST /api/materials` upsert | **PASS** | HTTP 200 |
| `GET /api/materials/supply-risk` | **PASS** | HTTP 200 |
| `POST /api/materials/substitutes` | **FAIL** unless material matches reconstructed genome slot | 400 without `material`/`slot_index`; 404 `配方中不含材料：zinc phosphate` for free-text name |

### 12) Monitor backend running logs

| Path | Result | Evidence |
|------|--------|----------|
| Log capture | **PASS** | `/tmp/cursor/logs/uvicorn-e2e-pass3.log` + `/opt/cursor/artifacts/backend_log_fingerprints.txt` |
| Notable | | Eager dispatch timeouts; Datalab connection refused; optional deps missing (`baybe`, `botorch`, `optuna`, `patent_client`, `colour`) |

---

## 3. Root-cause analysis

### RCA-A — P0: Eager Celery + 10s dispatch timeout → false 503

**Symptom:** `POST /api/search/stream`, `/api/optimize`, `/api/loop/iterate`, `/api/research/recommend` return:

```text
503 … 任务队列（Redis）当前不可达…（dispatch 超时: task.delay > 10.0s）
```

even when Redis is up and `celery_eager=true`.

**Mechanism:**

1. `backend/app/worker/celery_app.py` sets `task_always_eager=settings.celery_eager`.
2. Under eager mode, `task.delay(payload)` **executes the full task body in-process** and only returns when done.
3. `backend/app/api/_dispatch.py` `_delay_with_timeout` waits only **10s** on `task.delay` in a thread pool; longer jobs raise `_DispatchTimeout` → HTTP 503 with a **misleading Redis message**.
4. Empirically: optimize body ~9–16s; loop ~37s; search often >10s with live providers. Logs still show `Task … succeeded` after the client already got 503.

**Repro (local):** `_delay_with_timeout(run_optimize_task, payload, timeout_s=10)` → timeout; same with `timeout_s=120` → OK.

**Why Redis message is wrong:** exception text always prefixes `BROKER_DOWN_DETAIL` even when the failure is eager sync runtime, not broker reachability (`broker_reachable()` already returns True when eager).

### RCA-B — P0/P1: `FORMUMIND_DATALAB_REQUIRED=true` without Datalab

**Symptom:** `/health` degraded; recommend / experiments / workbench / models / optimize fail with:

```text
Datalab ELN 不可达（http://localhost:5001）… Connection refused … Datalab 为必需
```

**Code:** `backend/app/db/store.py`, `campaign_store.py`, `main.py` health — when `datalab_required` or backends force datalab, missing service is hard-fail.

**Uploaded env** enabled the hard dependency; this Cloud Agent host has no Datalab compose stack. Pass-2 override to sqlite unblocked ledger/recommend for continued testing.

### RCA-C — P1: Async UX depends on either fixed eager path OR Redis+worker

Without fixing RCA-A, operators must run Redis + Celery worker **and** set `CELERY_EAGER=false`. Comment in `celery_app.py` still says the opposite of the code (doc debt).

### RCA-D — P2: Materials substitute API contract

`SubstituteRequest` requires `formulation` or `requirement` plus `slot_index` **or** a `material` string that exactly matches a reconstructed genome slot name. Free-text “zinc phosphate” → 404. Frontend materials panel for catalog CRUD is still largely missing (see prior gap plan `2026-09-05-backend-ui-gap-fullfill.md` A2).

### RCA-E — P2: UI chat gate

Middle column chat is intentionally disabled until sources are loaded into the session. Sync search works via API, but stream search 503 (RCA-A) makes the UI path flaky for “search → ask”.

### RCA-F — Harness false negative (not a product bug)

Recommend response field is `formulas` / `returned_n`. Asserting `formulations` incorrectly marked PASS as FAIL in pass-1.

---

## 4. Graded fix plan

### P0 — ship first

#### P0-1 Fix eager dispatch semantics

**Files:** `backend/app/api/_dispatch.py` (primary); optionally `backend/app/worker/celery_app.py`, `backend/app/worker/tasks.py`

**Options (pick one, prefer A):**

- **A.** If `celery_eager`: do **not** call blocking `task.delay` under a 10s cap. Submit via background thread / `TaskManager` and return `202` + `task_id` immediately (eager execution continues async to the HTTP request).
- **B.** If keeping sync eager `delay()`, remove/raise the timeout dramatically and change the HTTP contract to wait for completion (breaks SSE/async UX — not recommended).
- **C.** Document that `CELERY_EAGER=true` is test-only and refuse eager in production settings validation.

**Also:** stop attaching `BROKER_DOWN_DETAIL` to non-broker failures; distinguish `DispatchTimeout` vs broker down.

**Acceptance:**

- With Redis **down** and `CELERY_EAGER=true`, `POST /api/optimize` and `/api/loop/iterate` return **202** within &lt;1s and task reaches `completed`.
- With Redis **up** and eager false + worker, same endpoints return 202 and complete.
- Error text for true broker outages still mentions Redis; eager timeouts never claim “Redis 不可达”.
- Unit test: mock long eager task (&gt;10s) still accepts within 1s.

#### P0-2 Safe defaults when Datalab absent

**Files:** `backend/app/config.py`, `backend/app/main.py`, `.env.example`, docs quickstart

**Plan:**

- Keep `datalab_required` default `false`.
- When `campaign_backend/experiment_backend=auto` and Datalab unreachable, fall back to sqlite **unless** `datalab_required=true`.
- Startup / Settings UI warning when required+unreachable (already partially in health).
- Quickstart: explicit “ELN mode” vs “local sqlite mode” checklist.

**Acceptance:** Fresh clone with only LLM keys: recommend + experiments + workbench succeed without Datalab. Enabling `DATALAB_REQUIRED=true` without service yields clear health=`degraded` and actionable 503.

### P1

#### P1-1 Cloud/local bootstrap: Redis

Ensure `scripts/install.sh` / compose / AGENTS cloud notes start Redis when async features are used. Document that eager mode is not a substitute until P0-1 ships.

#### P1-2 Misleading broker error copy

Split error strings in `_dispatch.submit` for timeout vs unreachable vs task exception (loop/optimize currently can nest Datalab errors inside Redis wording).

#### P1-3 UI search→chat continuity

When `/search/stream` fails, surface the 503 detail in the left-rail error banner (not only disabled chat). Optionally fall back to sync `/api/search` automatically.

### P2

#### P2-1 Materials substitutes UX/API

Accept fuzzy name match or return candidate slot names on 404. Wire materials management panel (gap plan A2).

#### P2-2 OpenAPI visibility

Publish `GET /api/materials` in schema (`include_in_schema=True`).

#### P2-3 Fix inverted comment in `celery_app.py`

Comment currently implies eager=False runs in-process; reverse it.

#### P2-4 Optional science extras

Install `baybe`/`optuna`/`botorch` only if product claims those engines; otherwise keep native DOE/optimizer fallbacks (already mostly working).

---

## 5. Suggested implementation order

1. **P0-1** eager/dispatch timeout (unblocks search stream, optimize, loop, async recommend).  
2. **P0-2** Datalab required defaults/docs (unblocks lab ledger on laptop/cloud without ELN).  
3. **P1-2/P1-3** error UX.  
4. **P2** materials/OpenAPI polish.

---

## 6. Artifact index

| Artifact | Path |
|----------|------|
| API pass-1 results | `/opt/cursor/artifacts/e2e_api_results.json` |
| API pass-2 (sqlite/datalab override) | `/opt/cursor/artifacts/e2e_api_pass2_results.json` |
| API pass-3 async after Redis | `/opt/cursor/artifacts/e2e_api_pass3_async.json` |
| UI step results | `/opt/cursor/artifacts/e2e_ui_results.json` |
| Log fingerprints | `/opt/cursor/artifacts/backend_log_fingerprints.txt` |
| UI screenshots | `/opt/cursor/artifacts/ui/01_home.png` … `11_settings.png` |

Secrets: none committed. Env keys shown only as last-4 where needed (`...aa4a` for DeepSeek).

---

## 7. Services left running

- Backend uvicorn `:8000`  
- Frontend Vite `:5173`  
- Redis `:6379`  
- Optional Celery worker (diagnostic)

Pass-2 `.env` overrides (`DATALAB_REQUIRED=false`, sqlite backends) remain on the VM for follow-up testing; **do not commit `.env`**.
