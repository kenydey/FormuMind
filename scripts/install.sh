#!/usr/bin/env bash
# FormuMind one-click install for Linux/macOS (PEP 668-safe).
# Installs backend venv + core extras; optional frontend npm deps.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Backend: Python virtualenv + editable install"
cd "$ROOT/backend"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -e ".[dev,llm]"

# Lightweight online retrieval (arxiv/ddgs also ship in requirements.txt for Docker).
pip install arxiv semanticscholar ddgs || true
# chemcrow installed separately — versions <0.3.7 pin openai==0.27.8 (conflicts with openai>=1.30).
pip install "chemcrow>=0.3.7" || echo "⚠️  chemcrow skipped — ChemCrow path uses offline fallback"

# Apply third-party library patches (e.g. pymupdf4llm RapidOCR attribute fix).
# Idempotent — safe to run on every install.
if [ -f "$ROOT/backend/.venv/bin/python" ]; then
  "$ROOT/backend/.venv/bin/python" "$ROOT/backend/scripts/apply_patches.py" || \
    echo "⚠️  patch application failed — see backend/scripts/reference/rapidocr-attribute-fix.md"
fi

echo ""
echo "==> Frontend (optional — skip if you only run the API)"
cd "$ROOT/frontend"
if command -v npm >/dev/null 2>&1; then
  npm install
else
  echo "⚠️  npm not found — install Node.js 20+ then: cd frontend && npm install"
fi

echo ""
echo "✅ Done."
echo "   Core stack (required for product): Redis + Celery worker + Datalab ELN :5001"
echo "   1. cp .env.example .env"
echo "      # intranet: FORMUMIND_API_AUTH_ENABLED=false"
echo "      # ELN defaults already set: CAMPAIGN/EXPERIMENT=datalab, DATALAB_REQUIRED=true"
echo "   2. Start infra (Docker required for ELN):"
echo "        docker compose up -d redis"
echo "        docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d"
echo "      # or follow deploy/eln/README.md — do NOT skip ELN / use sqlite as product mode"
echo "   3. Backend + worker:"
echo "        source backend/.venv/bin/activate"
echo "        cd backend && FORMUMIND_CELERY_EAGER=false uvicorn app.main:app --reload --port 8000"
echo "        # other terminal: celery -A app.worker.celery_app worker --loglevel=info"
echo "   4. Frontend: cd frontend && npm run dev   # http://localhost:5173"
echo "   5. Verify: curl -s localhost:8000/health  # datalab.reachable=true, task_broker.reachable=true"
echo ""
echo "   Or one-shot: bash scripts/start_all.sh"
echo "   More extras (optional engines only — native DOE/optimizer works without them):"
echo "     pip install -e \".[intel,science,embedding,optimize,bo,baybe,pydoe,color,file_ingest,export,notebooklm,colbert,crag]\""
echo "   Docker full stack: cp .env.example .env && docker compose -f docker-compose.yml -f docker-compose.eln.yml up"
