#!/usr/bin/env bash
# DECIMER 离线 OCSR worker：独立 venv + 独立 Celery 队列（与主 backend torch 环境隔离）。
# 用法：
#   FORMUMIND_DECIMER_MODE=cpu bash scripts/setup_decimer_worker.sh      # 当前 VPS（无 GPU）
#   FORMUMIND_DECIMER_MODE=gpu bash scripts/setup_decimer_worker.sh      # 预留（需 GPU + poppler）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DECIMER_VENV="${DECIMER_VENV:-$ROOT/.venv-decimer}"
MODE="${FORMUMIND_DECIMER_MODE:-cpu}"
QUEUE="${FORMUMIND_DECIMER_QUEUE:-decimer}"

echo "==> DECIMER worker（模式=$MODE，队列=$QUEUE）"
echo "==> 独立 venv: $DECIMER_VENV"

# 1. 创建独立 venv（与主 backend .venv 完全隔离，避免 tensorflow 与 torch 冲突）
if [ ! -d "$DECIMER_VENV" ]; then
  python3 -m venv "$DECIMER_VENV"
fi
# shellcheck disable=SC1091
source "$DECIMER_VENV/bin/activate"
pip install -U pip setuptools wheel

# 2. 按模式装依赖
cd "$ROOT/backend"
if [ "$MODE" = "gpu" ]; then
  echo "==> GPU 模式：tensorflow 完整版 + decimer-segmentation（预留）"
  pip install -e ".[decimer-gpu]"
  apt-get install -y poppler-utils || echo "⚠️  poppler-utils 安装失败（segmentation 内部 PDF→图需要）"
else
  echo "==> CPU 模式：decimer + tensorflow-cpu（纯识别，无 segmentation）"
  pip install -e ".[decimer-cpu]"
  # ⚠️ decimer 声明依赖 tensorflow（完整版，含 CUDA 库），必须换成 cpu 版：
  #    实测峰值 4.0GiB → 3.14GiB，否则吃 swap。POC 已实测。
  pip uninstall -y tensorflow || true
  pip install "tensorflow-cpu>=2.12,<2.21"
fi

# 3. TF 线程限流（CPU 模式必须 =1，防吃满 4 核饿死 API worker）
export TF_NUM_INTRAOP_THREADS="${TF_NUM_INTRAOP_THREADS:-1}"
export TF_NUM_INTEROP_THREADS="${TF_NUM_INTEROP_THREADS:-1}"
export TF_CPP_MIN_LOG_LEVEL="${TF_CPP_MIN_LOG_LEVEL:-2}"

# 4. 启动独立 Celery worker（只消费 decimer 队列，并发=1 串行，避免内存叠加）
echo "==> 启动 celery worker（-Q $QUEUE -c 1）"
exec celery -A app.worker.celery_app worker \
  --queues="$QUEUE" --concurrency=1 --loglevel=info
