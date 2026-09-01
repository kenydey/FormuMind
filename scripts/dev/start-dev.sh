#!/usr/bin/env bash
# FormuMind 开发调试模式 — 源码运行（backend/worker/frontend 走 host，基础设施走容器）
#
# 用法:
#   scripts/dev/start-dev.sh start   启动 backend + worker + frontend（host 源码）
#   scripts/dev/start-dev.sh stop    停止 host 进程（容器基础设施不动）
#   scripts/dev/start-dev.sh status  查看运行状态
#
# 前提:
#   1. redis/neo4j/molscribe/datalab 容器在跑（docker compose up -d redis kg molscribe datalab）
#   2. backend/.venv 已建（uvicorn/celery/rdkit 等），frontend/node_modules 完整
#   3. data/.env.host 已生成（连接地址指向 localhost）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
ENV_FILE="$ROOT/data/.env.host"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

export FORMUMIND_ENV_FILE="$ENV_FILE"

# 开发模式固定值（容器内是 /app/data，host 是源码路径）
export FORMUMIND_COLBERT_INDEX_DIR="$ROOT/data/colbert_index"

start() {
  echo "==> 启动 backend (uvicorn :8000)"
  cd "$BACKEND"
  nohup "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8000 \
    > "$LOG_DIR/dev-backend.log" 2>&1 &
  echo "    PID $! → logs/dev-backend.log"

  echo "==> 启动 worker (celery)"
  nohup "$VENV/bin/celery" -A app.worker.celery_app.celery_app worker \
    --loglevel=info > "$LOG_DIR/dev-worker.log" 2>&1 &
  echo "    PID $! → logs/dev-worker.log"

  echo "==> 启动 frontend (vite dev :5173)"
  cd "$FRONTEND"
  nohup npx vite dev --host 0.0.0.0 --port 5173 \
    > "$LOG_DIR/dev-frontend.log" 2>&1 &
  echo "    PID $! → logs/dev-frontend.log"

  echo "==> 等待健康检查…"
  for i in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then echo "    backend healthy (200)"; break; fi
    sleep 2
  done
  curl -s -o /dev/null -w "    frontend: %{http_code}\n" http://localhost:5173/ 2>/dev/null || true
  echo "==> 完成。停止: scripts/dev/start-dev.sh stop"
}

stop() {
  echo "==> 停止 host 进程（容器不受影响）"
  pkill -f "app.main:app" 2>/dev/null && echo "    backend 已停" || echo "    backend 未运行"
  pkill -f "app.worker.celery_app" 2>/dev/null && echo "    worker 已停" || echo "    worker 未运行"
  pkill -f "vite dev" 2>/dev/null && echo "    frontend 已停" || echo "    frontend 未运行"
  echo "==> 完成"
}

status() {
  echo "==> host 源码进程:"
  pgrep -af "app.main:app" | head -1 | sed 's/^/    backend: /' || echo "    backend: 未运行"
  pgrep -af "app.worker.celery_app" | head -1 | sed 's/^/    worker: /' || echo "    worker: 未运行"
  pgrep -af "vite dev" | head -1 | sed 's/^/    frontend: /' || echo "    frontend: 未运行"
  echo "==> 容器基础设施:"
  docker ps --format '    {{.Names}}: {{.Status}}' | grep -E "redis|kg|molscribe|datalab|freellmapi" || echo "    (无容器运行)"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "用法: $0 {start|stop|status}"; exit 1 ;;
esac
