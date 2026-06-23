#!/usr/bin/env bash
# FormuMind one-click install for Linux/macOS (PEP 668-safe)
set -euo pipefail

cd "$(dirname "$0")/../backend"

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev,llm]"

# chemcrow installed separately to avoid openai==0.27.8 pin conflict (0.3.20)
pip install patent-client paper-qa pubchempy arxiv semanticscholar ddgs || true
pip install "chemcrow>=0.3,!=0.3.20" || echo "⚠️  chemcrow unavailable (openai conflict) — fallback active"

echo "✅ Done. Run: source backend/.venv/bin/activate && uvicorn app.main:app --port 8000"
