#!/usr/bin/env bash
# FormuMind 一键启动：Redis + Docker(MongoDB/DataLab) + 后端(celery+uvicorn) + 前端(vite)
#   + MolScribe OCSR worker（离线结构识别）
#
# 用法：
#   bash scripts/start_all.sh    # 全栈 + MolScribe OCSR worker
#
# ⚠️ 内存提示：MolScribe worker 峰值 ~1.9GB（torch-cpu）。
#    停止/重启请用 scripts/stop_all.sh。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

echo "==> 1/6 Redis"
if ! redis-cli ping >/dev/null 2>&1; then
  redis-server --daemonize yes --dir /var/lib/redis
  echo "    Redis 已启动"
else
  echo "    Redis 已在运行"
fi

echo "==> 2/6 Docker（MongoDB + DataLab）"
docker start formumind-mongodb datalab-database-1 datalab-api-1 >/dev/null 2>&1 || true
echo "    容器已确保运行"

echo "==> 3/6 后端 celery worker"
CELERY_BIN="$ROOT/backend/.venv/bin/celery"
FORMUMIND_CELERY_EAGER=false nohup "$CELERY_BIN" -A app.worker.celery_app worker --loglevel=info \
  >>"$LOGS/celery.log" 2>&1 &

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
echo "   后端健康检查：curl http://127.0.0.1:8000/health"
echo "   前端入口：http://172.245.79.103:5173"
