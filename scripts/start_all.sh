#!/usr/bin/env bash
# FormuMind 一键启动：Redis + Docker(MongoDB/DataLab) + 后端(celery+uvicorn) + 前端(vite) [+ DECIMER worker]
#
# 用法：
#   bash scripts/start_all.sh                  # 主服务栈（不含 DECIMER，省内存）
#   bash scripts/start_all.sh --with-decimer    # 主服务栈 + DECIMER 离线结构识别 worker
#
# ⚠️ 内存提示：DECIMER worker 峰值 ~3.1GB，主服务栈已用约 4GB（available ~2.4GB），
#    加装 DECIMER 会额外吃 ~0.7-1GB swap，系统变慢。仅当需要离线结构识别时再加装。
#    停止/重启请用 scripts/stop_all.sh。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

WITH_DECIMER=false
for a in "$@"; do
  case "$a" in
    --with-decimer|-d) WITH_DECIMER=true ;;
    --help|-h) sed -n '2,12p' "$0"; exit 0 ;;
  esac
done

echo "==> 1/5 Redis"
if ! redis-cli ping >/dev/null 2>&1; then
  redis-server --daemonize yes --dir /var/lib/redis
  echo "    Redis 已启动"
else
  echo "    Redis 已在运行"
fi

echo "==> 2/5 Docker（MongoDB + DataLab）"
docker start formumind-mongodb datalab-database-1 datalab-api-1 >/dev/null 2>&1 || true
echo "    容器已确保运行"

echo "==> 3/5 后端 celery worker"
CELERY_BIN="$ROOT/backend/.venv/bin/celery"
FORMUMIND_CELERY_EAGER=false nohup "$CELERY_BIN" -A app.worker.celery_app worker --loglevel=info \
  >>"$LOGS/celery.log" 2>&1 &

echo "==> 4/5 后端 uvicorn"
UVICORN_BIN="$ROOT/backend/.venv/bin/uvicorn"
(
  cd "$ROOT/backend"
  FORMUMIND_CELERY_EAGER=false nohup "$UVICORN_BIN" app.main:app --host 127.0.0.1 --port 8000 --reload \
    >>"$LOGS/uvicorn.log" 2>&1 &
)

echo "==> 5/5 前端 vite"
(
  cd "$ROOT/frontend"
  nohup npx vite --force --host 0.0.0.0 --port 5173 >>"$LOGS/vite.log" 2>&1 &
)

if $WITH_DECIMER; then
  echo "==> 6/6 DECIMER worker（独立 venv，离线结构识别）"
  DECIMER_VENV="$ROOT/.venv-decimer"
  if [ ! -x "$DECIMER_VENV/bin/celery" ]; then
    echo "    ⚠️ $DECIMER_VENV 不存在，先跑 bash scripts/setup_decimer_worker.sh 安装"
  else
    (
      cd "$ROOT/backend"
      TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1 TF_CPP_MIN_LOG_LEVEL=2 \
        FORMUMIND_CELERY_EAGER=false \
        nohup "$DECIMER_VENV/bin/celery" -A app.worker.celery_app worker \
        -n decimer@%h --queues=decimer --concurrency=1 --loglevel=info \
        >>"$LOGS/decimer.log" 2>&1 &
    )
    echo "    DECIMER worker 已启动（队列 decimer，并发 1）"
  fi
fi

echo ""
echo "✅ 全部启动完成。日志目录：$LOGS/"
echo "   后端健康检查：curl http://127.0.0.1:8000/health"
echo "   前端入口：http://172.245.79.103:5173"
