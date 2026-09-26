#!/usr/bin/env bash
# P1 #23: archive a deploy-time pip freeze after installing the product stack.
# Does NOT overwrite requirements.txt (hand-maintained). Writes:
#   backend/locks/requirements-freeze-<utc>.txt
#   backend/locks/requirements-freeze-latest.txt  (symlink/copy)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
LOCK_DIR="$BACKEND/locks"
mkdir -p "$LOCK_DIR"

if [ -f "$BACKEND/.venv/bin/activate" ]; then
  # shellcheck source=/dev/null
  source "$BACKEND/.venv/bin/activate"
fi

UTC="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$LOCK_DIR/requirements-freeze-${UTC}.txt"
LATEST="$LOCK_DIR/requirements-freeze-latest.txt"

{
  echo "# FormuMind deploy freeze — generated ${UTC}"
  echo "# Source: pip freeze after requirements.txt + editable extras"
  echo "# Do not treat as the editable install source of truth; see requirements.txt"
  echo "#"
  pip freeze
} >"$OUT"

cp "$OUT" "$LATEST"
echo "Wrote $OUT"
echo "Updated $LATEST"
