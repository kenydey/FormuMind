#!/usr/bin/env bash
# Wait for a FormuMind task to reach a terminal state, then print a summary.
#
# The backend can be unreachable mid-run (memory pressure on a 4G host makes
# uvicorn unschedulable while the worker keeps ingesting), so a failed status
# read is NOT a reason to give up — it just means we sleep and retry. Only a
# *terminal* state or a hard deadline ends the wait.
set -uo pipefail

TASK_ID="${1:?task id required}"
API="http://localhost:8000/api/tasks/${TASK_ID}"
DEADLINE=$(( $(date +%s) + 18000 ))   # 5 h — matches celery_hard_time_limit_s
CONSEC_FAILS=0
MAX_CONSEC_FAILS=18                  # 18 * 30s = 9 min of total silence

while :; do
  BODY="$(curl -s -m 8 "$API" 2>/dev/null)"
  STATE="$(printf '%s' "$BODY" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("state",""))' 2>/dev/null)"
  if [ -z "$STATE" ]; then
    CONSEC_FAILS=$((CONSEC_FAILS + 1))
    if [ "$CONSEC_FAILS" -ge "$MAX_CONSEC_FAILS" ]; then
      echo "watcher: 连续 ${MAX_CONSEC_FAILS} 次读不到任务状态（后端长时间不可达），退出"
      exit 4
    fi
    echo "watcher: 后端暂不可达（第 ${CONSEC_FAILS} 次），30s 后重试"
    sleep 30
    continue
  fi
  CONSEC_FAILS=0

  case "$STATE" in
    completed|failed|cancelled|"")
      if [ -z "$STATE" ]; then
        echo "watcher: 任务状态为空，退出"
        exit 2
      fi
      echo "=== task ${TASK_ID} 结束: ${STATE} ==="
      printf '%s' "$BODY" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print("progress=%.2f  elapsed=%.1f min" % (d.get("progress", 0), d.get("elapsed_ms", 0) / 60000))
print("message:", d.get("message"))
r = d.get("result")
if isinstance(r, dict):
    print("indexed=%s skipped=%s failed=%s" % (r.get("indexed"), r.get("skipped"), r.get("failed")))
    docs = r.get("docs") or []
    print("docs=%d" % len(docs))
    for x in docs:
        if x.get("status") != "indexed":
            print("  非indexed:", x.get("status"), "|", x.get("identifier"), "|", x.get("error"))
'
      exit 0
      ;;
  esac
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then
    echo "watcher: 超过 5 小时仍未结束（state=$STATE）"
    exit 3
  fi
  sleep 30
done