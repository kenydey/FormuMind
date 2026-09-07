#!/usr/bin/env bash
# Apply third-party patches after pip install (see backend/scripts/apply_patches.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/backend/.venv/bin/python" "$ROOT/backend/scripts/apply_patches.py"