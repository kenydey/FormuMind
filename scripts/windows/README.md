# FormuMind on Windows

Native Windows setup (no WSL required). The **installer** is the repo-root
[`install.ps1`](../../install.ps1) / [`install.bat`](../../install.bat), shared with
Linux/macOS through `install.sh`; the scripts in this directory are the **runner**
(`start-dev.ps1` ↔ `scripts/dev/start-dev.sh`). Both exist because a Windows shell
differs from POSIX in four ways that break a literal translation:

| POSIX | Windows equivalent | Why it matters |
|---|---|---|
| `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` | `source` does not exist in PowerShell; a venv built on Linux (`bin/`, no `Scripts/`) cannot be activated on Windows at all. |
| `VAR=value cmd` | `$env:VAR="value"; cmd` | PowerShell has no inline env-prefix syntax. |
| `celery … worker` | `celery … worker --pool=solo` | The default prefork pool cannot run on Windows (daemonic processes cannot have children). |
| `--reload-exclude .venv\*` | `--reload-dir app` | PowerShell expands the bare glob into a file list, so uvicorn receives `.venv\Lib .venv\pyvenv.cfg …` as positional args. |

Neither the installer nor the runner activates the venv: they call
`backend\.venv\Scripts\python.exe` by absolute path, which sidesteps
`Set-ExecutionPolicy` entirely.

## Prerequisites

- **Python 3.12** (recommended) — `winget install --id Python.Python.3.12`
- **Node.js 20+** — for the Vite frontend
- **Docker Desktop** — Redis (:6379), Neo4j (:7687); optional Datalab ELN (:5001)

### Redis, without Docker

The task queue (Celery worker, upload processing, SSE progress) needs a Redis on `:6379`. Any of these works, because the
application speaks RESP2 to Redis unless told otherwise (`FORMUMIND_REDIS_PROTOCOL`, default `2`). redis-py 8's own default
needs Redis ≥ 6.0 and fails on every older one - the 5.0.14.1 port below included - with ``unknown command `HELLO` ``:

| Redis | Version | Notes |
|---|---|---|
| [Memurai](https://www.memurai.com/) | 7.x | The supported native choice (it has a free Developer edition). |
| [tporadowski/redis](https://github.com/tporadowski/redis/releases) | 5.0.14.1 | An unofficial port that runs as a plain process; `scripts/windows/smoke-stack.ps1` and the `windows-stack` CI job run the whole stack against it. |
| Redis in WSL2 | 6.x / 7.x | `sudo apt install redis-server`; reachable on `localhost:6379` from Windows. |
| Docker Desktop | 7.x | `docker compose up -d redis` (what `start-dev.ps1` does unless `-NoInfra`). |

### Datalab (the ELN)

Datalab is a Linux-container product: it needs Docker Desktop **with Linux containers**, and there is no native Windows build.
Without it `start-dev.ps1` says `Datalab ELN unreachable ... starting in soft-degrade mode (local sqlite ledger)` and carries on:
DOE and the workbench stay usable, only ELN-backed campaigns need it.

Python version matters: ColBERT (`ragatouille` → `voyager`) has Windows wheels
only up to **CPython 3.12**. On 3.13/3.14 the installer skips ColBERT and warns,
because `pip install -e ".[a,b,c]"` resolves every extra in a single
transaction — one impossible dependency fails the whole list and nothing gets
installed. RAG then falls back to BM25 / TF-IDF / sentence-transformers.

## Install

```powershell
cd C:\dev\FormuMind
.\install.bat                        # double-click works too
# or: powershell -ExecutionPolicy Bypass -File install.ps1
```

Options (forwarded by `install.bat` too):

```powershell
-Minimal         # core only: requirements.txt + .[dev,llm]
-Full            # also the torch tier (bo/heavy, + colbert on <= 3.12), CPU wheels
-SkipFrontend    # backend only
```

Steps: `[1/5]` environment check (Python / Node / git) → `[2/5]` venv + backend deps
+ patches → `[3/5]` `npm install` → `[4/5]` `.env` from `.env.example` → `[5/5]`
`alembic upgrade head`. A `.venv` that is not a Windows venv, or one on a different
interpreter, is renamed to `.venv.bak-<timestamp>` and rebuilt (never deleted).

**The window at the end.** A double-click opens a console that closes together with the script, which would take the
next-steps text (or the error) with it, so `install.bat` waits for a key - for a double-click only; a terminal, a script
and CI are never held up. It tells them apart by the command line `cmd.exe` was started with (`%cmdcmdline%`), which was
measured on a Windows runner (the `windows-stack` CI job prints it on every run; `backend/tests/test_install_bat_launch.py`
runs the batch file for each row, including the real shell association via `Start-Process`):

| started as | `%cmdcmdline%` | waits for a key |
|---|---|---|
| double-click; `Start-Process install.bat` with **no** arguments | `cmd.exe /c ""<dir>\install.bat" "` | yes |
| `Start-Process install.bat -ArgumentList -Minimal`; `& .\install.bat -Minimal` or `& .\install.bat` in PowerShell | `cmd.exe /c ""<dir>\install.bat" -Minimal"` | no |
| `cmd /c "<dir>\install.bat" -Minimal`, `call install.bat` from a script | ends in an argument, or in the path's own quote | no |
| typed at a prompt | `cmd.exe /q` (the prompt's own command line) | no |

Only the first row ends in quote, space, quote - the file association is `"%1" %*` and `%*` is empty - and that is the test.
Consequence: a script that starts the installer with `Start-Process` and **no arguments** is indistinguishable from a
double-click and will wait; give it any switch (`-Minimal`, `-Full`, `-SkipFrontend`). What has not been seen is a real
desktop double-click on a real Windows 10/11 machine; the shell association is the call Explorer makes, on a Windows Server 2025 runner.

Install tiers, in order:

1. **core** — `requirements.txt`, then `.[dev,llm]`. API + Celery worker boot.
2. **extras (no torch)** — `science,optimize,intel,file_ingest,report_export,parse_pro,export,embedding,pydoe,baybe,color,crag,notebooklm,dev,postgres`
3. **torch** (`-Full`) — `bo`, `heavy`, plus `colbert` when ≤ 3.12, with
   `--extra-index-url https://download.pytorch.org/whl/cpu` (the default wheel
   bundles CUDA and is ~2.5 GB).

Installing in tiers is deliberate: one impossible dependency cannot take the
rest down with it.

Two extras are intentionally left out; install them by hand if you want them:

```powershell
backend\.venv\Scripts\python.exe -m pip install -e ".[observability]"   # langfuse tracing
backend\.venv\Scripts\python.exe -m pip install -e ".[intel-colbert]"   # the two above, combined
```

## Start / stop / status

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 start
powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 status
powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 stop
# or: scripts\windows\start.cmd | status.cmd | stop.cmd
```

`start` does, in order:

1. Brings up Docker infra (`docker compose up -d redis kg`) and waits for
   Redis; skipped with `-NoInfra`.
2. Probes the Datalab ELN at `FORMUMIND_DATALAB_API_URL` (default
   `http://127.0.0.1:5001`). Reachable → `datalab` backends + `DATALAB_REQUIRED=true`.
   Unreachable → `auto` + `false`, i.e. the local sqlite ledger, with a warning
   (`-RequireEln` makes it fatal instead).
3. Runs `alembic upgrade head` so a stale schema cannot bite later
   (`schema drift: 缺失列 document_chunks.bbox …`).
4. Starts API (`uvicorn app.main:app --reload --reload-dir app`), Celery worker
   (`--pool=solo`) and Vite, each into `logs\*.log` with a PID file under
   `logs\pids\`.
5. Health-checks `/health` and the Vite root before reporting success.

Environment defaults set by `start`: an absolute
`FORMUMIND_DB_URL=sqlite:///<repo>/data/formumind.db` (a relative sqlite URL
resolves against each process's CWD, so uvicorn and Celery would otherwise open
different files), `FORMUMIND_CELERY_EAGER=false`, `FORMUMIND_COLBERT_INDEX_DIR`,
and `FORMUMIND_ENV_FILE` pointing at the repo `.env` when present.

## URLs

| Service | URL |
|---|---|
| API | http://127.0.0.1:8000 |
| API docs | http://127.0.0.1:8000/docs |
| Health | http://127.0.0.1:8000/health |
| Frontend | http://127.0.0.1:5173 |
| Neo4j browser | http://127.0.0.1:7474 |
| Datalab ELN (if started) | http://127.0.0.1:5001 |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `无法将"source"项识别为 cmdlet` | POSIX syntax. Use the scripts here, or `.\.venv\Scripts\Activate.ps1`. |
| `无法将"FORMUMIND_CELERY_EAGER=false"项识别…` | POSIX inline env prefix. Use `$env:FORMUMIND_CELERY_EAGER="false"` first, or put the key in `backend\.env`. |
| `Expected comma between extra names` | A typo/space in the extras list, e.g. `notebookl m`. Extras are comma-separated with no spaces. |
| `ResolutionImpossible … voyager` | ColBERT on CPython ≥ 3.13. Run the default install (it skips that tier), or rebuild with 3.12. |
| Installer aborts at 「创建虚拟环境」 | A `.venv` from another OS/interpreter was present. The installer renames it to `.venv.bak-<stamp>` and rebuilds; a leftover `.venv.bak-*` can be deleted. |
| `Got unexpected extra arguments (.venv\Lib …)` | PowerShell expanded `--reload-exclude .venv\*`. Use `--reload-dir app` (what `start-dev.ps1` does). |
| `celery` exits immediately | Missing `--pool=solo` on Windows. |
| `No matching distribution found for torch` | Use the CPU index: `pip install torch --extra-index-url https://download.pytorch.org/whl/cpu`. |
| Frontend reports missing dependencies | `cd frontend; npm install --legacy-peer-deps`. |
| `/health` shows `status: degraded` | Expected without LLM/ELN credentials. Check `database.ok`, `task_broker.reachable`, `datalab.reachable`. |
| Redis unreachable and Docker absent | Install Memurai, a plain-process Redis (tporadowski), or run Redis inside WSL2 - see *Redis, without Docker* above. Celery and SSE task progress need it. |
| `celery.err.log`: ``unknown command `HELLO` `` / every upload answers 503 | `FORMUMIND_REDIS_PROTOCOL=3` against a Redis older than 6.0. Remove it (the default, `2`, works with every Redis). |

## Notes

- `backend\.env` is read relative to the working directory, so start the API
  from `backend\`. Keys keep the `FORMUMIND_` prefix (`FORMUMIND_CELERY_EAGER=false`,
  not `CELERY_EAGER=false`).
- `parse_pro` installs `pymupdf4llm`/`PyMuPDF`, which are AGPL-3.0 (or a paid
  Artifex licence). Calls are confined to `app/services/pdf_local.py`.
- `pymupdf4llm` is pinned to `1.28.0`; do not upgrade it ad hoc.
- Docker stack for the full product (ELN-mandatory):
  `docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d`.
