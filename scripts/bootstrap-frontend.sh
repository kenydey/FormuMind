#!/usr/bin/env bash
# Install locked frontend deps (required after git pull — includes ag-grid for LabWorkbench).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
echo "[FormuMind] frontend dependencies installed."
