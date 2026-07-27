#!/usr/bin/env bash
# ── ci_golden_gate.sh ─────────────────────────────────────────────────────────
# Task 2.7 CI gate: golden eval regression gate for RAG quality.
#
# Steps:
#   1. alembic upgrade head → fresh DB (migration integrity check)
#   2. Ingest 2-3 sample documents (ingest pipeline validation)
#   3. pytest tests/test_golden_eval.py (retrieval + citation eval)
#
# Exit code ≠ 0 → CI blocked.
#
# Usage:  bash scripts/ci_golden_gate.sh
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BACKEND_DIR"

# ── environment ───────────────────────────────────────────────────────────────
export FORMUMIND_ENVIRONMENT="test"
export FORMUMIND_API_AUTH_ENABLED="false"
export FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP="1"
export FORMUMIND_KB_INGEST_AUTO="false"
export FORMUMIND_KB_V2_ENABLED="true"

# Point alembic at a temporary SQLite DB so we never touch the dev/prod DB.
GOLDEN_DB="$BACKEND_DIR/.golden_eval.db"
export FORMUMIND_DB_URL="sqlite:///$GOLDEN_DB"

# Activate the project venv
if [ -f "$BACKEND_DIR/.venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source "$BACKEND_DIR/.venv/bin/activate"
fi

echo "=== [1/3] alembic upgrade head (fresh DB) ==="
rm -f "$GOLDEN_DB"
cd "$BACKEND_DIR"
python3 -m alembic upgrade head
echo "OK: migrations applied cleanly"

echo ""
echo "=== [2/3] ingest sample documents ==="
python3 -c "
import os, json
from fastapi.testclient import TestClient
from app.main import app

docs = [
    {
        'title': '环氧树脂防腐机理',
        'text': '环氧树脂通过固化形成致密交联网络，有效阻隔腐蚀介质渗透。胺类固化剂在室温下即可引发交联反应。',
    },
    {
        'title': '磷酸锌防锈颜料',
        'text': '磷酸锌是一种无毒防锈颜料，水解生成磷酸根离子与金属基材形成钝化保护膜，显著提升盐雾耐受时间。',
    },
    {
        'title': '防腐蚀涂料工艺参数',
        'text': '固化温度80-120°C，涂膜厚度25-35微米，表面需除油脱脂至Sa2.5级。盐雾试验按GB/T 1771执行。',
    },
]

client = TestClient(app)
for doc in docs:
    resp = client.post('/api/kb/ingest', json=doc)
    assert resp.status_code == 200, f'ingest failed: {resp.status_code} {resp.json()}'
    data = resp.json()
    print(f'  ingested {data[\"source_id\"][:8]}… ({data[\"chunk_count\"]} chunks) — {doc[\"title\"]}')

# Quick smoke: hybrid_search returns results
resp = client.post('/api/kb/hybrid-search', json={'query': '环氧树脂防腐', 'top_k': 3})
assert resp.status_code == 200
results = resp.json()
assert len(results) > 0, 'hybrid-search returned no results after ingest'
print(f'  smoke check: hybrid_search → {len(results)} results')

print('OK: sample documents ingested')
"
echo "OK"

echo ""
echo "=== [3/3] pytest tests/test_golden_eval.py ==="
cd "$BACKEND_DIR"
python3 -m pytest tests/test_golden_eval.py -q --tb=short --timeout=240 2>&1 || {
    echo ""
    echo "FAIL: golden eval gate did not pass — CI blocked."
    exit 1
}

echo ""
echo "PASS: golden eval gate — all checks green."
