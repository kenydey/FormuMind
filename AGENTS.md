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
and Celery runs in eager (in-process) mode by default, so Redis/Postgres/Celery
worker and all heavy AI/physics engines are OPTIONAL — every adapter has a
deterministic fallback. No API keys or credentials are required to run end to end.
Health/engine status: `GET http://localhost:8000/health`. API docs: `/docs`.
The frontend dev server calls the backend at `:8000` (CORS allows `localhost:5173`).

### Build / lint / test

- Backend tests: `cd backend && source .venv/bin/activate && pytest -q` (offline).
- Frontend build + typecheck (this is the lint gate; there is no separate lint
  script): `cd frontend && npm run build` (runs `tsc -b && vite build`).

### Gotchas

- The backend lives in a venv at `backend/.venv`. Activate it before running
  `uvicorn`/`pytest`. Run `uvicorn` with `--reload-exclude .venv` so the reloader
  does not watch installed packages.
- `python3 -m venv` requires the system `python3.12-venv` package (already present
  in the VM snapshot; not part of the update script).
- Enabling optional engines (LLM, RDKit, Summit/Optuna, retrieval, LAMMPS, …) is
  via pyproject extras / Docker images documented in `README.md`; not needed for
  normal development or testing.
