#!/usr/bin/env python3
import os, sys, time, subprocess, tempfile
from pathlib import Path
BACKEND=Path("/root/FormuMind/backend")
TASK_DIR=tempfile.mkdtemp(prefix="verify_next_")
ENV={**os.environ,"FORMUMIND_MULTI_USER":"true","FORMUMIND_API_TOKENS_JSON":'{"alice":"tok_alice","bob":"tok_bob"}',"FORMUMIND_API_AUTH_ENABLED":"true","FORMUMIND_KG_ENABLED":"true","FORMUMIND_TASK_DIR":TASK_DIR,"FORMUMIND_TASK_PROGRESS_DIR":os.path.join(TASK_DIR,"progress"),"FORMUMIND_CELERY_EAGER":"false","PYTHONPATH":str(BACKEND)}
URL="http://127.0.0.1:8003"
def log(s): print(s,flush=True)
proc=subprocess.Popen([str(BACKEND/".venv/bin/python"),"-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8003","--log-level","warning"],cwd=str(BACKEND),env=ENV,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
import httpx
ok=False
for i in range(30):
    time.sleep(0.5)
    try:
        r=httpx.get(f"{URL}/api/auth/status",timeout=2)
        if r.status_code in (200,401): ok=True; break
    except: pass
    if proc.poll() is not None: print(proc.stdout.read().decode()[-2000:]); sys.exit(1)
if not ok: print("not ready"); proc.terminate(); sys.exit(1)
log("[ready]")
def h(t): return {"Authorization": f"Bearer {t}"}
client=httpx.Client(timeout=20)
checks=[]
def ck(n,c,d=""): log(f"{'✅' if c else '❌'} {n} {d}"); checks.append((n,c))
# bias-trend need campaign with history; create campaign then seed via direct DB
plan={"design":"lhs","factors":[],"runs":[{"run_id":1,"coded":{},"natural":{"Zinc phosphate":9.0}}],"notes":"t","plan_id":"b1","domain":"anticorrosion_coating"}
req={"domain":"anticorrosion_coating","objectives":[{"metric":"salt_spray_hours","weight":1.0,"direction":"maximize"}]}
r=client.post(f"{URL}/api/experiments/workbench/campaigns",json={"plan":plan,"requirement":req},headers=h("tok_alice"))
cid=r.json().get("campaign_id") if r.status_code==200 else None
ck("create campaign", r.status_code==200 and cid)
if cid:
    # seed loop_history via DB factory (use same DB file as server: need to find DB url)
    # Instead use a trick: directly via API not exposed, so use internal DB via python import with same ENV
    # We'll spawn a python snippet that imports with same ENV to write
    import json, textwrap
    code=textwrap.dedent(f"""
import os
os.environ['FORMUMIND_TASK_DIR']="{TASK_DIR}"
from app.db.database import make_engine, make_session_factory, Base
from app.db.campaign_store import SqliteCampaignStore
from app.config import get_settings
# Replicate server's DB: default sqlite file is at backend's default (not TASK_DIR), need to locate
# Use the same factory as server would: default_session_factory reads DB_URL env (empty -> sqlite default)
from app.db.database import default_session_factory
factory=default_session_factory()
from app.db.models import Campaign
with factory() as s:
    camp=s.get(Campaign, {cid})
    camp.loop_history=[
        {{"type":"prediction_bias","at":"2026-08-28T00:00:00Z","bias":{{"n_rows":2,"by_metric":{{"salt_spray_hours":{{"n":2,"mean_error":5,"rmse":10,"mae":8,"max_abs":12}}}}}}}},
        {{"type":"prediction_bias","at":"2026-08-28T01:00:00Z","bias":{{"n_rows":3,"by_metric":{{"salt_spray_hours":{{"n":3,"mean_error":-2,"rmse":60,"mae":50,"max_abs":70}}}}}}}},
    ]
    s.commit()
print("seeded")
""")
    # Run with same ENV via subprocess python
    import subprocess as sp
    env2={**ENV, "PYTHONPATH": str(BACKEND)}
    res=sp.run([str(BACKEND/".venv/bin/python"), "-c", code], cwd=str(BACKEND), env=env2, capture_output=True, text=True, timeout=10)
    log("seed db: "+res.stdout[:500]+res.stderr[:500])
    r2=client.get(f"{URL}/api/experiments/workbench/{cid}/bias-trend?threshold_rmse=50",headers=h("tok_alice"))
    ck("bias-trend 200", r2.status_code==200, r2.text[:500])
    if r2.status_code==200:
        b=r2.json()
        ck("bias-trend 2 entries", len(b["trend"])==2, str(b))
        ck("bias-trend alert 1", len(b["alerts"])==1 and "60" in b["alerts"][0], str(b["alerts"]))
        # bob 403
        ck("bob bias-trend 403", client.get(f"{URL}/api/experiments/workbench/{cid}/bias-trend",headers=h("tok_bob")).status_code==403)
    else:
        ck("bias-trend 2 entries", False)
        ck("bias-trend alert", False)
        ck("bob bias-trend 403", False)
# rag prewarm
r=client.get(f"{URL}/api/research/rag/status",headers=h("tok_alice"))
ck("rag/status prewarm", r.status_code==200 and "prewarm" in r.json(), r.text[:300])
r=client.get(f"{URL}/api/kg/feedback/report",headers=h("tok_alice"))
ck("report alert none or string", r.status_code==200 and "alert" in r.json(), r.text[:300])
# tsc
import subprocess as sp
res=sp.run(["npx","tsc","--noEmit"],cwd="/root/FormuMind/frontend",capture_output=True,text=True,timeout=30)
ck("tsc PASS", res.returncode==0, (res.stdout+res.stderr)[:300])
log("\n=== 汇总 ===")
for n,ok in checks: log(f"{'✅' if ok else '❌'} {n}")
failed=[n for n,ok in checks if not ok]
log(f"共 {len(checks)} 失败 {len(failed)}")
if failed: log("失败: "+", ".join(failed))
client.close(); proc.terminate()
try: proc.wait(timeout=5)
except: proc.kill()
sys.exit(0 if not failed else 1)
