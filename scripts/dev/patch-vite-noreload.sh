#!/usr/bin/env bash
# 幂等 patch: 关闭 vite client「WS 断线重连后自动整页刷新」。
#
# 背景(2026-09-04): FormuMind 前端以 vite dev 直出公网 5173。用户发起问答后
# (deepseek 推理 63-180s), 等待期间切走标签页 → Chrome 冻结/节流后台标签 →
# HMR WebSocket 静默断开 → 切回时 vite client 探测到服务器存活后自动
# location.reload(), 杀掉正在等待的长问答请求 — 表现为"页面自动刷新, 答案丢失"。
#
# 本脚本把 close handler 里的 location.reload() 替换为 console.warn(HMR 恢复
# 但不刷新页面)。npm install 重装 vite 后会覆盖 node_modules, 重跑本脚本即可。
set -euo pipefail
cd "$(dirname "$0")/../../frontend"

TARGET="node_modules/vite/dist/client/client.mjs"
MARK="page reload suppressed by patch"
if [ ! -f "$TARGET" ]; then
  echo "SKIP: $TARGET 不存在(vite 未安装?)" >&2
  exit 0
fi
if grep -q "$MARK" "$TARGET"; then
  echo "OK: 已打过 patch, 跳过"
  exit 0
fi

python3 - "$TARGET" <<'PY'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
old = """    if (hasDocument) {
      console.log(`[vite] server connection lost. Polling for restart...`);
      await waitForSuccessfulPing(protocol, hostAndPath);
      location.reload();
    }"""
new = """    if (hasDocument) {
      console.warn(`[vite] server connection lost. Polling for restart...`);
      await waitForSuccessfulPing(protocol, hostAndPath);
      // Hermes patch: 公网 dev 下后台标签冻结/NAT 空闲会静默断 WS, 自动
      // reload 会杀掉等待中的长问答请求(63-180s)。恢复 HMR 但不刷新页面。
      console.warn(`[vite] connection restored; HMR resumed (page reload suppressed by patch). If code looks stale, refresh manually.`);
      // location.reload();  // original vite behaviour — intentionally disabled
    }"""
assert src.count(old) == 1, f"anchor not unique/found (count={src.count(old)})"
open(p, "w", encoding="utf-8").write(src.replace(old, new))
print("PATCHED:", p)
PY
