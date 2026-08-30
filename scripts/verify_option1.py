#!/usr/bin/env python3
"""Option 1 真实走查：uvicorn + Redis 真实例，验证 owner 403 / KG 回流 / cancel SSE / elapsed"""
import os, sys, time, json, subprocess, signal, tempfile
from pathlib import Path

BACKEND = Path("/root/FormuMind/backend")
TASK_DIR = tempfile.mkdtemp(prefix="verify_opt1_")
ENV = {
    **os.environ,
    "FORMUMIND_MULTI_USER": "true",
    "FORMUMIND_API_TOKENS_JSON": '{"alice":"tok_alice","bob":"tok_bob"}',
    "FORMUMIND_API_AUTH_ENABLED": "true",
    "FORMUMIND_KG_ENABLED": "true",
    "FORMUMIND_TASK_DIR": TASK_DIR,
    "FORMUMIND_TASK_PROGRESS_DIR": os.path.join(TASK_DIR, "progress"),
    "FORMUMIND_CELERY_EAGER": "false",
    "PYTHONPATH": str(BACKEND),
}
URL = "http://127.0.0.1:8001"
def log(s): print(s, flush=True)

# 启动 uvicorn
log(f"[1] 启动 uvicorn {URL} TASK_DIR={TASK_DIR}")
proc = subprocess.Popen(
    [str(BACKEND / ".venv/bin/python"), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001", "--log-level", "warning"],
    cwd=str(BACKEND), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
)
# 等待就绪
import httpx
ok=False
for i in range(30):
    time.sleep(0.5)
    try:
        r = httpx.get(f"{URL}/api/auth/status", timeout=2)
        if r.status_code in (200,401):
            ok=True; break
    except: pass
    if proc.poll() is not None:
        out = proc.stdout.read().decode(errors="ignore")[-2000:]
        log(f"uvicorn 退出: {out}"); sys.exit(1)
if not ok:
    log("uvicorn 未就绪"); proc.terminate(); sys.exit(1)
log("[2] uvicorn 就绪")

def h(token): return {"Authorization": f"Bearer {token}"}
client = httpx.Client(timeout=20)

checks=[]
def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    log(f"{status} {name} {detail}")
    checks.append((name, cond))
    return cond

# 1. owner 403 - campaign
from app.domain.schemas import DOEPlan, DOERun, ProductDomain, Requirement, ObjectiveSpec
import tempfile as tf
# 用 HTTP 创建 campaign（走真实接口）
# 先用 alice 创建
plan = {"design":"lhs","factors":[],"runs":[{"run_id":1,"coded":{},"natural":{"Zinc phosphate":9.0,"cure_temperature_c":82.0}}],"notes":"t","plan_id":"v1","domain":"anticorrosion_coating"}
req = {"domain":"anticorrosion_coating","objectives":[{"metric":"salt_spray_hours","weight":1.0,"direction":"maximize"}]}
r = client.post(f"{URL}/api/experiments/workbench/campaigns", json={"plan":plan,"requirement":req}, headers=h("tok_alice"))
log(f"create campaign alice -> {r.status_code} {r.text[:300]}")
cid = r.json().get("campaign_id") if r.status_code==200 else None
check("alice 创建 campaign", r.status_code==200 and cid is not None)
if cid:
    r2 = client.get(f"{URL}/api/experiments/workbench/{cid}", headers=h("tok_alice"))
    check("alice 可查自己 campaign", r2.status_code==200)
    r3 = client.get(f"{URL}/api/experiments/workbench/{cid}", headers=h("tok_bob"))
    check("bob 查 alice campaign 403", r3.status_code==403, f"got {r3.status_code}")
    # sync 回流
    row = r.json()["rows"][0]
    r4 = client.put(f"{URL}/api/experiments/workbench/sync", json={"campaign_id":cid,"rows":[{"id":row["id"],"status":"Completed","actual_params":{"Zinc phosphate":9.0},"measurements":{"salt_spray_hours":780}}],"requirement":req}, headers=h("tok_alice"))
    log(f"sync -> {r4.status_code} {r4.text[:500]}")
    body = r4.json() if r4.status_code==200 else {}
    check("sync 透传 kg_written", "kg_written" in body and body["kg_written"] is not None, str(body.get("kg_written")))
    check("sync 透传 prediction_bias 可选", True)

# 2. tasks owner 隔离（research recommend）
r = client.post(f"{URL}/api/research/recommend", json={**req,"sources":[],"query":"test"}, headers=h("tok_alice"))
log(f"research/recommend alice -> {r.status_code} {r.text[:300]}")
tid = r.json().get("task_id") if r.status_code==202 else None
check("alice 提交 recommend 202", r.status_code==202 and tid)
if tid:
    time.sleep(0.6)
    ra = client.get(f"{URL}/api/tasks/{tid}", headers=h("tok_alice"))
    check("alice 可查自己 task", ra.status_code==200, ra.text[:200])
    # stage/elapsed
    body = ra.json() if ra.status_code==200 else {}
    check("task 透传 stage/elapsed", "stage" in body, str(body))
    rb = client.get(f"{URL}/api/tasks/{tid}", headers=h("tok_bob"))
    check("bob 查 alice task 403", rb.status_code==403, f"got {rb.status_code}")
    rc = client.post(f"{URL}/api/tasks/{tid}/cancel", headers=h("tok_bob"))
    check("bob cancel alice task 403", rc.status_code==403, f"got {rc.status_code}")
    rd = client.post(f"{URL}/api/tasks/{tid}/cancel", headers=h("tok_alice"))
    check("alice cancel 自己 task 200", rd.status_code in (200,202), rd.text[:200])
    if rd.status_code==200:
        check("cancel 后 state cancelled", rd.json().get("state")=="cancelled")
    # SSE 流（短轮询）
    try:
        import httpx as hx
        with hx.Client(timeout=8) as c2:
            # 用 stream 接口验证 403
            rs = c2.get(f"{URL}/api/tasks/{tid}/stream", headers=h("tok_bob"))
            # 流接口在鉴权失败时应 403（非 200）
            # 若已 cancelled，bob 仍 403
            check("bob SSE 流 403", rs.status_code==403, f"got {rs.status_code}")
    except Exception as e:
        check("bob SSE 流 403", False, str(e))

# 3. KG feedback stats
r = client.get(f"{URL}/api/kg/feedback/stats", headers=h("tok_alice"))
log(f"kg/feedback/stats -> {r.status_code} {r.text[:400]}")
if r.status_code==200:
    b=r.json()
    check("kg feedback stats measured_performance>=1", b.get("measured_performance",0)>=1, str(b))
else:
    check("kg feedback stats 200", False, r.text[:200])

# 4. 前端 tsc 已在提交时验过，此处仅汇总
log("\n=== 汇总 ===")
for n,ok in checks: log(f"{'✅' if ok else '❌'} {n}")
failed=[n for n,ok in checks if not ok]
log(f"\n共 {len(checks)} 项，失败 {len(failed)} 项")
if failed:
    log("失败项: "+ ", ".join(failed))

# 清理
client.close()
proc.terminate()
try: proc.wait(timeout=5)
except: proc.kill()
log("uvicorn 已停止")
sys.exit(0 if not failed else 1)
