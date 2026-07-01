# FormuMind

AI-assisted formulation R&D platform for metal surface treatment. Monorepo with a
Python/FastAPI `backend/` and a Vite/React/TypeScript `frontend/`. See `README.md`
for the product overview, architecture, and full API reference.

## Cursor Cloud specific instructions

### Services

| Service | Dir | Dev command | Port |
|---------|-----|-------------|------|
| Backend API (FastAPI) | `backend/` | `source .venv/bin/activate && uvicorn app.main:app --reload --reload-exclude .venv --port 8000` | 8000 |
| Frontend (Vite/React) | `frontend/` | `npm run dev -- --host` | 5173 |
| Redis (async broker) | — | `sudo service redis-server start` (or `redis-server --daemonize yes`) | 6379 |
| Celery worker | `backend/` | `source .venv/bin/activate && FORMUMIND_REDIS_URL=redis://localhost:6379/0 FORMUMIND_CELERY_EAGER=false celery -A app.worker.celery_app.celery_app worker --loglevel=info` | — |

The backend runs fully offline: SQLite is auto-created at `backend/data/formumind.db`.
A Postgres DB, the Datalab ELN service, and all heavy AI/physics engines are
OPTIONAL — every adapter has a deterministic fallback. `GET /health` reports engine
status; on a clean dev box it shows `database.scheme = sqlite` and
`datalab.required = false` (falls back to the sqlite campaign/experiment store).
No API keys or credentials are required to run end to end. API docs: `/docs`.
The frontend dev server proxies `/api` and `/health` to `:8000` (see
`frontend/vite.config.ts`).

Async jobs (search "开始检索", deep research, recommend, optimize) run through Celery
and stream progress to the UI via SSE (`GET /api/tasks/{id}/stream`). Two ways to run:

- **Full / recommended:** start Redis + the Celery worker (with
  `FORMUMIND_CELERY_EAGER=false` on BOTH the worker and the `uvicorn` backend, and
  `FORMUMIND_REDIS_URL=redis://localhost:6379/0`). This gives real live, per-source
  incremental search results in the left SOURCES pane (`[patents] +N 条`). This is
  the same wiring as the `docker compose` core profile. `redis-server` is already
  installed in the VM snapshot.
- **Offline fallback:** with no Redis and default eager mode, the async endpoints
  still complete and return final results; the UI's recommend/DOE flows work, but
  live incremental search streaming is unreliable in the browser — prefer the Redis
  + worker path when exercising the search UI.

Note: "开始检索" only fills the left SOURCES evidence list; the center RESEARCH pane
populates from "深度研究" (deep research) or the recommend flow, not from plain search.

### Build / lint / test

- Backend tests: `cd backend && source .venv/bin/activate && pytest -q` (offline).
  Expect `369 passed, 7 skipped` (the 7 skips need optional heavy extras).
- Frontend build + typecheck (this is the lint gate; there is no separate lint
  script): `cd frontend && npm run build` (runs `tsc -b && vite build`).
  Both `dev` and `build` run a `predev`/`prebuild` hook (`scripts/check-deps.mjs`)
  that fails fast if `ag-grid-*`/`immer` are missing — just run `npm install`.

### Gotchas

- The backend lives in a venv at `backend/.venv`. Activate it before running
  `uvicorn`/`pytest`. Run `uvicorn` with `--reload-exclude .venv` so the reloader
  does not watch installed packages.
- `python3 -m venv` requires the system `python3.12-venv` package (already present
  in the VM snapshot; not part of the update script).
- Three backend tests (`test_workbench_api.py`, `test_baybe_campaign_objectives.py`)
  import `pandas` via `baybe_engine.py`. `pandas` is only declared under the heavy
  `baybe` extra, so the update script installs it explicitly to keep the suite green
  without pulling the full `baybe`/`torch` stack.
- `arxiv` / `ddgs` are pinned in `backend/requirements.txt` (Docker/prod) for online
  retrieval, but the app imports them lazily and degrades to offline fallbacks, so
  the pyproject-based dev install does not need them.
- Enabling optional engines (LLM, RDKit, Summit/Optuna, BayBE, ColBERT, Postgres,
  LAMMPS, …) is via pyproject extras / Docker images documented in `README.md`;
  not needed for normal development or testing.
