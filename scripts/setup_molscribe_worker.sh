#!/usr/bin/env bash
# MolScribe 离线 OCSR worker：独立 venv + 独立 Celery 队列（无 GPU 默认后端）。
#
# 用法：bash scripts/setup_molscribe_worker.sh
#
# 三个必踩坑已固化（2026-08 POC 实测）：
#   1. 必须 git clone 源码装（PyPI 包锁 torch<2.0 会失败）
#   2. 必须 torch==2.3.0+cpu（新版本用 AVX2 指令，无 AVX2 老 CPU 上 SIGILL 非法指令）
#   3. torchvision 必须 +cpu 版，且 -e . 会覆盖成 CUDA 版，需重装；torchtext 不能漏
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOLSCRIBE_VENV="${MOLSCRIBE_VENV:-$ROOT/.venv-molscribe}"
QUEUE="${FORMUMIND_MOLSCRIBE_QUEUE:-molscribe}"

echo "==> MolScribe worker（队列=$QUEUE）"
echo "==> 独立 venv: $MOLSCRIBE_VENV"

# 1. 创建独立 venv（与主 backend .venv 隔离：MolScribe 锁 numpy<2.0，主 venv 是 numpy 2.4.6）
if [ ! -d "$MOLSCRIBE_VENV" ]; then
  python3 -m venv "$MOLSCRIBE_VENV"
  "$MOLSCRIBE_VENV/bin/pip" install -q -U pip
  echo "==> 装 torch==2.3.0+cpu + torchvision==0.18.0+cpu（CPU 源，避免 AVX2 SIGILL / CUDA 版 torchvision）"
  "$MOLSCRIBE_VENV/bin/pip" install -q "torch==2.3.0+cpu" "torchvision==0.18.0+cpu" \
    --index-url https://download.pytorch.org/whl/cpu
fi

# 2. 装 MolScribe（源码装）
if [ ! -d "$ROOT/MolScribe" ]; then
  echo "==> git clone MolScribe"
  git clone --depth 1 https://github.com/thomas0809/MolScribe.git "$ROOT/MolScribe"
fi
echo "==> pip install -e MolScribe"
(cd "$ROOT/MolScribe" && "$MOLSCRIBE_VENV/bin/pip" install -q -e .)
# -e . 会把 torchvision 拉成 CUDA 版，强制重装 +cpu 版
"$MOLSCRIBE_VENV/bin/pip" install -q "torchvision==0.18.0+cpu" --index-url https://download.pytorch.org/whl/cpu
# onmt-py（MolScribe 的 transformer decoder 依赖）需要 torchtext；huggingface_hub 下载模型
"$MOLSCRIBE_VENV/bin/pip" install -q "torchtext==0.5.0" huggingface_hub

# 3. 验证 import
"$MOLSCRIBE_VENV/bin/python" -c "import molscribe, torch; print('molscribe OK | torch', torch.__version__)"

# 4. 启动独立 Celery worker（只消费 molscribe 队列，并发=1 串行，避免内存叠加）
echo "==> 启动 celery worker（-Q $QUEUE -c 1）"
cd "$ROOT/backend"
exec "$MOLSCRIBE_VENV/bin/celery" -A app.worker.celery_app worker \
  -n molscribe@%h --queues="$QUEUE" --concurrency=1 --loglevel=info
