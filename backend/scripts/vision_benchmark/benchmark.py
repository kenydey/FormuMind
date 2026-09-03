"""Benchmark vision LLMs on chemical-structure → SMILES accuracy.

Runs every test image through each provider and reports, per provider:
  * exact-match rate   (canonical SMILES == ground truth)
  * RDKit-parseable rate
  * mean Tanimoto      (Morgan fingerprint, how close the answer is)
  * formula-match rate

Providers are OpenAI-compatible and configured via environment:

    QWEN_API_KEY=... GLM_API_KEY=... python benchmark.py

Output is a per-image table plus a summary line per provider, and a final
JSON blob the caller can diff between runs.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time

from rdkit import Chem, RDLogger
from rdkit.Chem import DataStructs, rdMolDescriptors

RDLogger.DisableLog("rdApp.*")

# Background runs buffer stdout in blocks — force line buffering so progress
# is visible in real time and per-image results land in results.json.
sys.stdout.reconfigure(line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))

PROMPT = (
    "这是一个化学结构图。请只输出该分子的 SMILES 字符串本身，"
    "不要任何解释、不要 markdown 代码块、不要空格或换行。"
    "如果无法识别，只输出一个空字符串。"
)

TOKEN_PLAN_BASE = (
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
)

# (label, base_url, model, key_env_var) — 阿里云百炼 Token Plan 套餐，一个 key 通吃。
# 三个 qwen 型号按「强→弱/贵→便宜」排列；glm-5.2 虽在套餐内，但该端点下不接受
# image_url（prompt_tokens=22，图片被静默丢弃），故不纳入化学结构视觉评测。
PROVIDERS = {
    "qwen3.8-max": ("Qwen3.8-Max", TOKEN_PLAN_BASE, "qwen3.8-max", "TOKEN_PLAN_API_KEY"),
    "qwen3.7-plus": ("Qwen3.7-Plus", TOKEN_PLAN_BASE, "qwen3.7-plus", "TOKEN_PLAN_API_KEY"),
    "qwen3.6-flash": ("Qwen3.6-Flash", TOKEN_PLAN_BASE, "qwen3.6-flash", "TOKEN_PLAN_API_KEY"),
}

_SMILES_RE = re.compile(r"[A-Za-z0-9@+\-\[\]()=#/\\%.:]+")


def canonical(smi: str) -> str | None:
    try:
        m = Chem.MolFromSmiles(smi)
        return Chem.MolToSmiles(m) if m is not None else None
    except Exception:
        return None


def extract_smiles(raw: str) -> str | None:
    """Pull a usable SMILES out of whatever the model actually returned."""
    t = (raw or "").strip()
    t = re.sub(r"^```(?:smiles|SMILES)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    if not t:
        return None
    if canonical(t):
        return canonical(t)
    # Fall back to the longest RDKit-parseable token.
    best = None
    for tok in _SMILES_RE.findall(t):
        if len(tok) < 2:
            continue
        c = canonical(tok)
        if c and (best is None or len(c) > len(best)):
            best = c
    return best


def tanimoto(a: str, b: str) -> float:
    ma, mb = Chem.MolFromSmiles(a), Chem.MolFromSmiles(b)
    if ma is None or mb is None:
        return 0.0
    fa = rdMolDescriptors.GetMorganFingerprintAsBitVect(ma, 2, 2048)
    fb = rdMolDescriptors.GetMorganFingerprintAsBitVect(mb, 2, 2048)
    return DataStructs.TanimotoSimilarity(fa, fb)


def formula(smi: str) -> str | None:
    m = Chem.MolFromSmiles(smi)
    return rdMolDescriptors.CalcMolFormula(m) if m is not None else None


def call_vision(client, model: str, image_path: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    )
    return resp.choices[0].message.content or ""


def run_provider(client, model: str, gt: list[dict], label: str) -> dict:
    n = len(gt)
    exact = parseable = formula_ok = 0
    tsum = 0.0
    rows = []
    for rec in gt:
        raw = call_vision(client, model, rec["image"])
        cand = extract_smiles(raw)
        ok_parse = cand is not None
        ok_exact = ok_parse and cand == rec["smiles"]
        t = tanimoto(rec["smiles"], cand) if ok_parse else 0.0
        f_ok = ok_parse and formula(cand) == rec["formula"]
        exact += ok_exact
        parseable += ok_parse
        formula_ok += f_ok
        tsum += t
        rows.append({
            "name": rec["name"], "gt": rec["smiles"], "cand": cand or "",
            "exact": ok_exact, "parseable": ok_parse, "tanimoto": round(t, 3),
            "formula_ok": f_ok,
        })
    print(f"\n{'=' * 90}\n{label} ({model})\n{'=' * 90}")
    print(f"  {'name':<18} | {'GT':<36} | {'预测':<36} | 匹配 | Tanimoto | 式一致")
    for r in rows:
        mark = "✓" if r["exact"] else ("·" if r["parseable"] else "✗")
        print(f"  {r['name']:<18} | {r['gt']:<36} | {(r['cand'] or '—'):<36} "
              f"|  {mark}  |  {r['tanimoto']:.2f}   | {'✓' if r['formula_ok'] else '✗'}")
    stats = {
        "provider": label, "model": model, "n": n,
        "exact": exact, "exact_rate": round(exact / n, 3),
        "parseable": parseable, "parseable_rate": round(parseable / n, 3),
        "formula_ok": formula_ok, "formula_rate": round(formula_ok / n, 3),
        "mean_tanimoto": round(tsum / n, 3),
        "per_image": rows,
    }
    print(f"  → 完全匹配 {exact}/{n} ({exact/n*100:.0f}%) | "
          f"RDKit可解析 {parseable}/{n} ({parseable/n*100:.0f}%) | "
          f"分子式一致 {formula_ok}/{n} ({formula_ok/n*100:.0f}%) | "
          f"平均Tanimoto {tsum/n:.2f}")
    return stats


def main() -> None:
    from openai import OpenAI  # noqa: WPS433

    with open(os.path.join(HERE, "ground_truth.json"), encoding="utf-8") as f:
        gt = json.load(f)
    if not gt:
        raise SystemExit("ground_truth.json 为空，先跑 generate_testset.py")

    results = {}
    for key, (label, base_url, model, env_key) in PROVIDERS.items():
        api_key = os.environ.get(env_key, "").strip()
        if not api_key:
            print(f"跳过 {label}: 缺少环境变量 {env_key}")
            continue
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=180)
        results[key] = run_provider(client, model, gt, label)

    out = os.path.join(HERE, "results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n结果 → {out}")
    if not results:
        raise SystemExit("\n没有可用的 provider（需设置 QWEN_API_KEY / GLM_API_KEY）")


if __name__ == "__main__":
    main()
