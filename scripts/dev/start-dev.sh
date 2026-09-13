#!/usr/bin/env bash
# FormuMind 开发调试模式 — 源码运行（backend/worker/frontend 走 host，基础设施走容器）
#
# 用法:
#   scripts/dev/start-dev.sh start   启动 backend + worker + frontend（host 源码）
#   scripts/dev/start-dev.sh stop    停止 host 进程（容器基础设施不动）
#   scripts/dev/start-dev.sh status  查看运行状态
#
# 前提:
#   1. 基础设施容器存在（离线镜像 + 镜像名）：
#      - redis (formumind-redis-1)         —— cache/broker
#      - datalab-api-1 / datalab-database-1 —— ELN + MongoDB
#      - formumind-molscribe-1            —— OCSR 结构识别 worker
#      一键拉起（首次或容器被删后重建）：
#        docker compose up -d redis
#        docker compose up -d molscribe
#        cd /root/datalab && docker compose --profile prod up -d
#   2. backend/.venv 已建（uvicorn/celery/rdkit 等），frontend/node_modules 完整
#   3. data/.env.host 已生成（连接地址指向 localhost；含 CAMPAIGN/EXPERIMENT=datalab）

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
# 关键：源码模式 CWD=backend/，默认 db_url 会落到 backend/data/formumind.db（8MB 空库）。
# 显式指向仓库根 data/ 的真库（504MB，17 campaigns / 591 docs）。
export FORMUMIND_DB_URL="sqlite:///$ROOT/data/formumind.db"
export FORMUMIND_CAMPAIGN_BACKEND="${FORMUMIND_CAMPAIGN_BACKEND:-datalab}"
export FORMUMIND_EXPERIMENT_BACKEND="${FORMUMIND_EXPERIMENT_BACKEND:-datalab}"
export FORMUMIND_DATALAB_REQUIRED="${FORMUMIND_DATALAB_REQUIRED:-true}"
export FORMUMIND_DATALAB_API_URL="${FORMUMIND_DATALAB_API_URL:-http://127.0.0.1:5001}"
export FORMUMIND_CELERY_EAGER="${FORMUMIND_CELERY_EAGER:-false}"

_probe_datalab() {
  curl -sf --max-time 2 "${FORMUMIND_DATALAB_API_URL%/}/" >/dev/null 2>&1
}

_require_infra() {
  echo "==> 检查核心依赖（Redis + Datalab ELN + MongoDB）"
  # Redis
  if ! redis-cli ping >/dev/null 2>&1; then
    echo "❌ Redis 不可达（:6379）。先: docker compose up -d redis"
    exit 1
  fi
  echo "    Redis OK"
  # MongoDB (Datalab-database)
  if ! docker ps --format '{{.Names}}' | grep -qx datalab-database-1 2>/dev/null; then
    echo "    MongoDB (datalab-database-1) 未运行，尝试启动…"
    docker start datalab-database-1 >/dev/null 2>&1 || echo "      ⚠️ 容器不存在，未启动"
  fi
  echo "    MongoDB OK"
  # Datalab API
  if ! docker ps --format '{{.Names}}' | grep -qx datalab-api-1 2>/dev/null; then
    echo "    Datalab API (datalab-api-1) 未运行，尝试启动…"
    docker start datalab-api-1 >/dev/null 2>&1 || echo "      ⚠️ 容器不存在，未启动"
  fi
  echo "    Datalab API OK"
  # Datalab ELN probe
  if ! _probe_datalab; then
    echo "❌ Datalab ELN 不可达：${FORMUMIND_DATALAB_API_URL}"
    echo "   预期：docker start datalab-api-1 或参考 deploy/eln/README.md"
    exit 1
  fi
  echo "    Datalab ELN 已就绪 (${FORMUMIND_DATALAB_API_URL})"
  # MolScribe — OCSR worker 容器（消费 molscribe 队列；未运行则拉起已有容器）
  if ! docker ps --format '{{.Names}}' | grep -qx formumind-molscribe-1 2>/dev/null; then
    echo "    MolScribe (formumind-molscribe-1) 未运行，尝试启动…"
    docker start formumind-molscribe-1 >/dev/null 2>&1 || echo "      ⚠️ 容器不存在，未启动"
  fi
  echo "    MolScribe OK"
}

start() {
  _require_infra

  echo "==> 启动 backend (uvicorn :8000)"
  cd "$BACKEND"
  nohup "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8000 \
    > "$LOG_DIR/dev-backend.log" 2>&1 &
  echo "    PID $! → logs/dev-backend.log"

  echo "==> 启动 worker (celery)"
  # 内存硬约束（VPS 6.3GB）：并发 2 防重量级任务（search 索引 ~2GB / PDF 解析
  # ~0.8GB）同刻并存撞物理内存 SIGSEGV；prefetch=1 让任务串行化领取。
  nohup "$VENV/bin/celery" -A app.worker.celery_app.celery_app worker \
    --concurrency=2 --prefetch-multiplier=1 \
    --loglevel=info > "$LOG_DIR/dev-worker.log" 2>&1 &
  echo "    PID $! → logs/dev-worker.log"

  echo "==> 启动 frontend (vite dev :5173)"
  cd "$FRONTEND"
  nohup npx vite dev --host 0.0.0.0 --port 5173 \
    > "$LOG_DIR/dev-frontend.log" 2>&1 &
  echo "    PID $! → logs/dev-frontend.log"

  echo "==> 等待健康检查…"
  for i in $(seq 1 30); do
    body=$(curl -s http://localhost:8000/health 2>/dev/null || true)
    code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then
      echo "    backend HTTP 200"
      echo "$body" | grep -q '"reachable":true' && echo "    (检查 datalab/task_broker reachable 于 /health JSON)" || true
      break
    fi
    sleep 2
  done
  # MolScribe worker 探活：ping molscribe 队列的 celery worker。
  # 注意：① ping 进程自身要加载 torch，--timeout 需给足；
  #      ② host worker 刚加入 broker 时会有 mingle 同步，ping 可能瞬时无响应，
  #         故重试 3 次。
  if docker ps --format '{{.Names}}' | grep -qx formumind-molscribe-1 2>/dev/null; then
    ms_host="$(docker exec formumind-molscribe-1 hostname 2>/dev/null || echo '')"
    ms_ready=0
    for _ in 1 2 3; do
      if docker exec -w /app formumind-molscribe-1 celery \
           -A app.worker.celery_app.celery_app \
           inspect ping -d "molscribe@${ms_host}" --timeout 30 >/dev/null 2>&1; then
        ms_ready=1
        break
      fi
      sleep 5
    done
    if [ "$ms_ready" = "1" ]; then
      echo "    MolScribe: worker 就绪（队列 molscribe）"
    else
      echo "    ⚠️  MolScribe 容器在跑但 worker 未响应 ping（见 docker logs formumind-molscribe-1）"
    fi
  else
    echo "    ⚠️  MolScribe 容器未运行，结构识别（OCSR）任务将不可用"
  fi
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
