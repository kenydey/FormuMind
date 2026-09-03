#!/usr/bin/env python3
"""冻结验证：v2+v3+v4 扩展（prewarm + report + tsc）"""
import os, sys, time, subprocess, tempfile, json
from pathlib import Path
BACKEND = Path("/root/FormuMind/backend")
TASK_DIR = tempfile.mkdtemp(prefix="verify_freeze_")
ENV = {**os.environ,
    "FORMUMIND_MULTI_USER":"true",
    "FORMUMIND_API_TOKENS_JSON":'{"alice":"tok_alice","bob":"tok_bob"}',
    "FORMUMIND_API_AUTH_ENABLED":"true",
    "FORMUMIND_KG_ENABLED":"true",
    "FORMUMIND_TASK_DIR":TASK_DIR,
    "FORMUMIND_TASK_PROGRESS_DIR": os.path.join(TASK_DIR,"progress"),
    "FORMUMIND_CELERY_EAGER":"false",
    "PYTHONPATH": str(BACKEND),
}
URL="http://127.0.0.1:8002"
def log(s): print(s,flush=True)
proc=subprocess.Popen([str(BACKEND/".venv/bin/python"),"-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8002","--log-level","warning"],cwd=str(BACKEND),env=ENV,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
import httpx
ok=False
for i in range(30):
    time.sleep(0.5)
    try:
        r=httpx.get(f"{URL}/api/auth/status",timeout=2)
        if r.status_code in (200,401): ok=True; break
    except: pass
    if proc.poll() is not None:
        print(proc.stdout.read().decode()[-2000:]); sys.exit(1)
if not ok: print("uvicorn not ready"); proc.terminate(); sys.exit(1)
log("[ready] uvicorn")
def h(t): return {"Authorization": f"Bearer {t}"}
client=httpx.Client(timeout=20)
checks=[]
def ck(n,c,d=""): log(f"{'✅' if c else '❌'} {n} {d}"); checks.append((n,c)); return c

# v2/v3 已验 14 项简版：campaign + task owner + kg stats（复用关键）
from app.domain.schemas import DOEPlan
plan={"design":"lhs","factors":[],"runs":[{"run_id":1,"coded":{},"natural":{"Zinc phosphate":9.0,"cure_temperature_c":82.0}}],"notes":"t","plan_id":"v1","domain":"anticorrosion_coating"}
req={"domain":"anticorrosion_coating","objectives":[{"metric":"salt_spray_hours","weight":1.0,"direction":"maximize"}]}
r=client.post(f"{URL}/api/experiments/workbench/campaigns",json={"plan":plan,"requirement":req},headers=h("tok_alice"))
cid=r.json().get("campaign_id") if r.status_code==200 else None
ck("alice campaign", r.status_code==200 and cid)
if cid:
    ck("bob campaign 403", client.get(f"{URL}/api/experiments/workbench/{cid}",headers=h("tok_bob")).status_code==403)
    row=r.json()["rows"][0]
    r2=client.put(f"{URL}/api/experiments/workbench/sync",json={"campaign_id":cid,"rows":[{"id":row["id"],"status":"Completed","actual_params":{"Zinc phosphate":9.0},"measurements":{"salt_spray_hours":780}}],"requirement":req},headers=h("tok_alice"))
    ck("kg_written", r2.json().get("kg_written")==1, str(r2.json().get("kg_written")))

# v4 D prewarm
r=client.get(f"{URL}/api/research/rag/status",headers=h("tok_alice"))
ck("rag/status 200 + prewarm", r.status_code==200 and "prewarm" in r.json(), r.text[:300])
if r.status_code==200:
    pw=r.json()["prewarm"]
    ck("prewarm status in idle/warming/ready/failed", pw["status"] in ("idle","warming","ready","failed"), str(pw))
r=client.post(f"{URL}/api/research/rag/prewarm?background=true",headers=h("tok_alice"))
ck("POST prewarm 200", r.status_code==200, r.text[:200])
r=client.post(f"{URL}/api/research/rag/prewarm?background=false",headers=h("tok_alice"))
ck("POST prewarm sync 200", r.status_code==200 and r.json()["status"] in ("ready","failed","warming"), r.text[:200])

# v4 E report
r=client.get(f"{URL}/api/kg/feedback/report",headers=h("tok_alice"))
ck("GET report 200", r.status_code==200, r.text[:400])
if r.status_code==200:
    b=r.json()
    ck("report measured_performance>=1 (有回流后无告警)", b["measured_performance"]>=1 and b["alert"] is None, str(b))
    ck("report by_campaign contains", "measured:campaign_" in str(b["by_campaign"]), str(b["by_campaign"]))

# tasks owner + cancel + SSE 已在之前验证，此处抽检
r=client.post(f"{URL}/api/research/recommend",json={**req,"sources":[],"query":"test"},headers=h("tok_alice"))
tid=r.json().get("task_id") if r.status_code==202 else None
ck("recommend task owner alice", tid and client.get(f"{URL}/api/tasks/{tid}",headers=h("tok_alice")).json().get("owner_id")=="alice")
if tid:
    ck("bob task 403", client.get(f"{URL}/api/tasks/{tid}",headers=h("tok_bob")).status_code==403)

log("\n=== 汇总 ===")
for n,ok in checks: log(f"{'✅' if ok else '❌'} {n}")
failed=[n for n,ok in checks if not ok]
log(f"共 {len(checks)} 项 失败 {len(failed)}")
if failed: log("失败: "+", ".join(failed))
# tsc
import subprocess as sp
res=sp.run(["npx","tsc","--noEmit"],cwd="/root/FormuMind/frontend",capture_output=True,text=True,timeout=30)
ck("tsc PASS", res.returncode==0, (res.stdout+res.stderr)[:500])
client.close(); proc.terminate()
try: proc.wait(timeout=5)
except: proc.kill()
sys.exit(0 if not [n for n,ok in checks if not ok] else 1)
