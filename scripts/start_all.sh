#!/usr/bin/env bash
# FormuMind 一键启动：Redis + Docker(MongoDB/DataLab) + 后端(celery+uvicorn) + 前端(vite)
#   + MolScribe OCSR worker（离线结构识别）
#
# 用法：
#   bash scripts/start_all.sh    # 全栈 + MolScribe OCSR worker
#
# ⚠️ 内存提示：MolScribe worker 峰值 ~1.9GB（torch-cpu）。
#    停止/重启请用 scripts/stop_all.sh。
#
# 核心依赖（产品必达）：Redis :6379 + Celery worker + Datalab ELN :5001
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

DATALAB_URL="${FORMUMIND_DATALAB_API_URL:-http://127.0.0.1:5001}"
DATALAB_PROBE_TIMEOUT_S="${FORMUMIND_DATALAB_PROBE_TIMEOUT_S:-90}"

# 绝对路径 DB：消除 cwd 依赖（celery/uvicorn 必须连同一个库文件）。
# 相对路径 sqlite:///./data/formumind.db 会因进程 cwd 不同而解析到不同文件，
# 曾导致 loop_history 写入旧库 /root/FormuMind/data/formumind.db 而前端读新库。
export FORMUMIND_DB_URL="${FORMUMIND_DB_URL:-sqlite:///$ROOT/backend/data/formumind.db}"

# Product defaults if unset: ELN required, real worker (not eager).
export FORMUMIND_CAMPAIGN_BACKEND="${FORMUMIND_CAMPAIGN_BACKEND:-datalab}"
export FORMUMIND_EXPERIMENT_BACKEND="${FORMUMIND_EXPERIMENT_BACKEND:-datalab}"
export FORMUMIND_DATALAB_REQUIRED="${FORMUMIND_DATALAB_REQUIRED:-true}"
export FORMUMIND_DATALAB_API_URL="${FORMUMIND_DATALAB_API_URL:-$DATALAB_URL}"
export FORMUMIND_CELERY_EAGER="${FORMUMIND_CELERY_EAGER:-false}"

probe_datalab() {
  local url="$1"
  local base="${url%/}"
  curl -sf --max-time 2 "${base}/" >/dev/null 2>&1
}

echo "==> 1/6 Redis"
if ! redis-cli ping >/dev/null 2>&1; then
  if command -v redis-server >/dev/null 2>&1; then
    redis-server --daemonize yes --dir /var/lib/redis
    echo "    Redis 已启动"
  else
    echo "❌ Redis 未运行且找不到 redis-server。请先安装/启动 Redis（:6379）。"
    exit 1
  fi
else
  echo "    Redis 已在运行"
fi

echo "==> 2/6 Docker（MongoDB + DataLab ELN）"
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ 需要 Docker 以拉起 Datalab ELN。安装 Docker 后参见 deploy/eln/README.md"
  echo "   或: docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d"
  exit 1
fi
docker start formumind-mongodb datalab-database-1 datalab-api-1 >/dev/null 2>&1 || true
# Prefer compose overlay when official stack not already named as above.
if ! docker ps --format '{{.Names}}' | grep -qE 'datalab'; then
  echo "    未检测到 datalab 容器，尝试 compose ELN overlay…"
  docker network create formumind-eln >/dev/null 2>&1 || true
  if [ -f "$ROOT/docker-compose.eln.yml" ]; then
    (cd "$ROOT" && docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d) || true
  fi
fi

echo "    探活 Datalab ${DATALAB_URL}（最多 ${DATALAB_PROBE_TIMEOUT_S}s）…"
ready=0
for _ in $(seq 1 "$DATALAB_PROBE_TIMEOUT_S"); do
  if probe_datalab "$DATALAB_URL"; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != "1" ]; then
  echo "❌ Datalab ELN 不可达：${DATALAB_URL}"
  echo "   FormuMind 将无法完成台账 / 推荐 / workbench / optimize。"
  echo "   启动指引：deploy/eln/README.md"
  echo "   或：docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d"
  exit 1
fi
echo "    Datalab 已就绪"

echo "==> 3/6 后端 celery worker"
CELERY_BIN="$ROOT/backend/.venv/bin/celery"
(
  cd "$ROOT/backend"
  FORMUMIND_CELERY_EAGER=false nohup "$CELERY_BIN" -A app.worker.celery_app worker --loglevel=info \
    >>"$LOGS/celery.log" 2>&1 &
)

echo "==> 4/6 后端 uvicorn"
UVICORN_BIN="$ROOT/backend/.venv/bin/uvicorn"
(
  cd "$ROOT/backend"
  FORMUMIND_CELERY_EAGER=false nohup "$UVICORN_BIN" app.main:app --host 127.0.0.1 --port 8000 --reload \
    >>"$LOGS/uvicorn.log" 2>&1 &
)

echo "==> 5/6 前端 vite"
(
  cd "$ROOT/frontend"
  nohup npx vite --force --host 0.0.0.0 --port 5173 >>"$LOGS/vite.log" 2>&1 &
)

# 6/6 MolScribe OCSR worker（torch-cpu ~1.9GB）
MOLSCRIBE_VENV="$ROOT/.venv-molscribe"
if [ ! -x "$MOLSCRIBE_VENV/bin/celery" ]; then
  echo "==> 6/6 MolScribe OCSR worker — 跳过（$MOLSCRIBE_VENV 不存在，先跑 bash scripts/setup_molscribe_worker.sh 安装）"
else
  echo "==> 6/6 MolScribe OCSR worker"
  (
    cd "$ROOT/backend"
    FORMUMIND_CELERY_EAGER=false \
      nohup "$MOLSCRIBE_VENV/bin/celery" -A app.worker.celery_app worker \
      -n molscribe@%h --queues=molscribe --concurrency=1 --loglevel=info \
      >>"$LOGS/molscribe.log" 2>&1 &
  )
  echo "    MolScribe worker 已启动（队列 molscribe，并发 1）"
fi

echo ""
echo "✅ 全部启动完成。日志目录：$LOGS/"
echo "   全栈清单：Redis + Celery worker + Datalab ELN + API + 前端"
echo "   后端健康检查：curl http://127.0.0.1:8000/health"
echo "   期望 /health：datalab.required=true 且 datalab.reachable=true；task_broker.reachable=true"
echo "   前端入口：http://127.0.0.1:5173"
