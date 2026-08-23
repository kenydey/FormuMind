#!/usr/bin/env bash
# 停止 FormuMind 全部服务栈（含 MolScribe OCSR worker）。
#
# 用法：bash scripts/stop_all.sh
# 注意：Redis 默认一并停止（--keep-redis 保留）。
set -euo pipefail

KEEP_REDIS=false
for a in "$@"; do
  case "$a" in
    --keep-redis) KEEP_REDIS=true ;;
  esac
done

echo "==> 停止 celery worker（含 MolScribe OCSR worker）"
pkill -9 -f "celery -A app.worker.celery_app" 2>/dev/null || echo "    无 celery 进程"

echo "==> 停止 uvicorn"
pkill -9 -f "uvicorn app.main" 2>/dev/null || echo "    无 uvicorn 进程"

echo "==> 停止前端 vite"
pkill -9 -f "vite" 2>/dev/null || echo "    无 vite 进程"

if ! $KEEP_REDIS; then
  echo "==> 停止 Redis"
  redis-cli shutdown nosave 2>/dev/null || echo "    Redis 未运行"
else
  echo "==> 保留 Redis（--keep-redis）"
fi

echo "✅ 全部停止"
