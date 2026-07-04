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
echo "   1. cp .env.example .env   # optional keys; intranet dev: FORMUMIND_API_AUTH_ENABLED=false"
echo "   2. Backend:  source backend/.venv/bin/activate && cd backend && uvicorn app.main:app --reload --port 8000"
echo "   3. Frontend: cd frontend && npm run dev   # http://localhost:5173"
echo ""
echo "   More extras (install on demand or via Settings → 依赖管理):"
echo "     pip install -e \".[intel,science,embedding,optimize,bo,baybe,pydoe,color,file_ingest,export,notebooklm,colbert,crag]\""
echo "   Docker: cp .env.example .env && docker compose up"
