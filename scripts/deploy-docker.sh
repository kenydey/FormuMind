#!/usr/bin/env bash
# FormuMind production Docker deployment
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Copy .env.example to .env and configure API keys first."
  exit 1
fi

# Ensure Docker daemon (vfs fallback for nested VM environments)
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null && ! pgrep -x dockerd >/dev/null; then
    echo "Starting dockerd (vfs storage driver)..."
    sudo dockerd --storage-driver=vfs >/tmp/dockerd.log 2>&1 &
    sleep 8
  fi
fi

COMPOSE=(docker compose -f docker-compose.yml)
# Use host-network overlay when bridge networking is restricted
if [[ "${FORMUMIND_USE_HOST_NETWORK:-auto}" == "true" ]] || \
   [[ "${FORMUMIND_USE_HOST_NETWORK:-auto}" == "auto" && "$(docker network ls -q | wc -l)" -gt 0 ]]; then
  COMPOSE+=(-f docker-compose.host.yml)
fi

echo "Building images..."
"${COMPOSE[@]}" build

echo "Starting stack..."
"${COMPOSE[@]}" up -d

echo "Waiting for health..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

python3 scripts/smoke_test.py
echo "Deployment complete: http://localhost:5173  (API http://localhost:8000)"
