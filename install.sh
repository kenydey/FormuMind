#!/usr/bin/env bash
#
# FormuMind 一键安装脚本 (macOS / Linux)
#
# 一条命令完成：环境检查 → 后端/前端依赖安装 → .env 配置 → 数据库迁移。
# 依赖安装复用 scripts/install.sh（与 docs/QUICKSTART.md 文档一致）。
#
# 用法:
#   ./install.sh                 # 交互式：安装完成后询问是否启动服务
#   ./install.sh --start         # 安装完成后直接启动全部服务
#   ./install.sh -y --start      # 全自动（CI / 无人值守）
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ASSUME_YES=0
DO_START=0

usage() {
  sed -n '3,12p' "$0"
}

for arg in "$@"; do
  case "$arg" in
    -y|--yes)   ASSUME_YES=1 ;;
    --start)    DO_START=1 ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "未知参数: $arg"; usage; exit 1 ;;
  esac
done

# 版本比较：ver_ge "3.12.3" "3.10" → 0 表示 >=
ver_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

OS="linux"
[ "$(uname -s)" = "Darwin" ] && OS="macos"

hint_python() {
  echo "  请先安装 Python 3.10+："
  if [ "$OS" = "macos" ]; then
    echo "    brew install python@3.12"
  elif command -v apt >/dev/null 2>&1; then
    echo "    sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  elif command -v dnf >/dev/null 2>&1; then
    echo "    sudo dnf install -y python3 python3-pip"
  elif command -v pacman >/dev/null 2>&1; then
    echo "    sudo pacman -S python python-pip"
  else
    echo "    参见 https://www.python.org/downloads/"
  fi
}

hint_node() {
  echo "  请先安装 Node.js 20+（仅前端需要；只跑 API 可跳过）："
  if [ "$OS" = "macos" ]; then
    echo "    brew install node@24"
  else
    echo "    参见 https://nodejs.org/ （推荐 LTS，或用 nvm 安装）"
  fi
}

echo "==> [1/5] 环境检查"

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3"
  hint_python
  exit 1
fi
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
if ! ver_ge "$PY_VER" "3.10"; then
  echo "❌ Python 版本过低: $PY_VER（需要 >= 3.10）"
  hint_python
  exit 1
fi
echo "    Python $PY_VER ✓"

if command -v node >/dev/null 2>&1; then
  NODE_VER="$(node --version | sed 's/^v//')"
  if ver_ge "$NODE_VER" "20"; then
    echo "    Node.js v$NODE_VER ✓"
  else
    echo "⚠️  Node.js v$NODE_VER 过低（需要 >= 20），前端安装可能失败"
    hint_node
  fi
  command -v npm >/dev/null 2>&1 || { echo "❌ 找到 node 但未找到 npm"; hint_node; exit 1; }
else
  echo "⚠️  未找到 node — 将跳过前端安装（只跑 API 不受影响）"
  hint_node
fi

command -v git >/dev/null 2>&1 || echo "⚠️  未找到 git（不影响安装，推送代码时需要）"

echo ""
echo "==> [2/5] 安装后端/前端依赖"
bash "$ROOT/scripts/install.sh"

echo ""
echo "==> [3/5] 配置 .env"
if [ -f "$ROOT/.env" ]; then
  echo "    .env 已存在，跳过"
else
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "    已从 .env.example 生成 .env"
  echo "    内网/免登录：FORMUMIND_API_AUTH_ENABLED=false"
  echo "    LLM Key 可选（不填则用规则引擎离线运行）"
fi

echo ""
echo "==> [4/5] 数据库迁移"
cd "$ROOT/backend"
"$ROOT/backend/.venv/bin/python" -m alembic upgrade head
echo "    数据库已就绪"

echo ""
echo "==> [5/5] 完成 ✅"
echo ""
echo "  后端 API:    cd backend && source .venv/bin/activate && uvicorn app.main:app --port 8000"
echo "  Celery worker: cd backend && celery -A app.worker.celery_app worker --loglevel=info"
echo "  前端:        cd frontend && npm run dev    # http://localhost:5173"
echo "  健康检查:    curl -s localhost:8000/health"
echo ""
echo "  产品级运行需要 Redis + Datalab ELN（:5001）："
echo "    docker compose up -d redis"
echo "    docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d"
echo "  详见 docs/QUICKSTART.md"

SHOULD_START=0
if [ "$DO_START" = "1" ]; then
  SHOULD_START=1
elif [ "$ASSUME_YES" = "0" ] && [ -t 0 ]; then
  read -rp "是否现在一键启动全部服务？[y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] && SHOULD_START=1
fi

if [ "$SHOULD_START" = "1" ]; then
  echo ""
  bash "$ROOT/scripts/start_all.sh"
else
  echo ""
  echo "  随时一键启动：bash scripts/start_all.sh"
fi
