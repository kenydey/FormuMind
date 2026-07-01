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

The backend runs fully offline: SQLite is auto-created at `backend/data/formumind.db`
and Celery runs in eager (in-process) mode by default. Redis, a Postgres DB, the
Celery worker, the Datalab ELN service, and all heavy AI/physics engines are
OPTIONAL — every adapter has a deterministic fallback. `GET /health` reports engine
status; on a clean dev box it shows `database.scheme = sqlite` and
`datalab.required = false` (falls back to the sqlite campaign/experiment store).
No API keys or credentials are required to run end to end. API docs: `/docs`.
The frontend dev server calls the backend at `:8000` (CORS allows `localhost:5173`).

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
