#!/usr/bin/env python3
"""R5 (2026-09-04): 批量回填 RAW_MATERIALS seed 缺失的 SMILES(PubChem REST)。

一次性工具, 不改任何代码: 对 seed 中 smiles 为空的条目调 PubChem
name→SMILES, 结果落 JSON 供人工核验后由开发者应用(seed patch + kb_entities
同步)。聚合物/分散体/商品名在 PubChem 无单一结构 —— 解析失败属预期,
进入失败名单, 由人工决定是否给代表性单体近似(如 seed 现有 DGEBA/acrylic
单体近似先例)。

用法:
    .venv/bin/python scripts/backfill_catalog_smiles.py            # 全量
    .venv/bin/python scripts/backfill_catalog_smiles.py --limit 5  # 试跑

输出: stdout 汇总 + ./backfill_catalog_smiles_out.json
      {"resolved": {key: smiles}, "failed": [key, ...], "skipped": [keys with smiles]}
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_SMILES_KEYS = ("CanonicalSMILES", "ConnectivitySMILES", "SMILES")
_RATE_S = 0.35  # PubChem 公开端点礼貌限速(~3 QPS)

# 明显非单一分子的名称特征 —— 跳过网络, 直接进失败名单(合理失败)。
_POLYMER_HINTS = re.compile(
    r"emulsion|dispersion|polymer|resin|binder|hardener|latex|"
    r"oligomer|copolymer|acrylic\b|polyurethane|polyamide|epoxy|"
    r"silane\b|modified|blend|mixture", re.IGNORECASE,
)
_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def load_seed() -> dict[str, dict]:
    src = (Path("app/domain/knowledge.py")).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_SEED_MATERIALS":
            if node.value is not None:
                return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "_SEED_MATERIALS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise SystemExit("_SEED_MATERIALS not found")


def _valid_smiles(smi: str | None) -> str | None:
    if not smi:
        return None
    s = str(smi).strip()
    if len(s) < 4 or len(s) > 300:
        return None
    if re.search(r"\s", s) or not re.search(r"[A-Za-z]", s):
        return None
    return s


def _pubchem_smiles(name: str) -> str | None:
    import httpx

    encoded = name.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    url = f"{_BASE}/compound/name/{encoded}/property/SMILES,ConnectivitySMILES/JSON"
    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None
    props = ((data or {}).get("PropertyTable") or {}).get("Properties") or []
    for row in props:
        for key in _SMILES_KEYS:
            smi = _valid_smiles(row.get(key))
            if smi:
                return smi
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    seed = load_seed()
    resolved: dict[str, str] = {}
    failed: list[tuple[str, str]] = []
    skipped: list[str] = []

    targets = [k for k, spec in seed.items() if not _valid_smiles(spec.get("smiles"))]
    if args.limit:
        targets = targets[: args.limit]
    print(f"seed 共 {len(seed)} 条; 缺 smiles {len(targets)} 条(试跑 {args.limit} 条)" if args.limit
          else f"seed 共 {len(seed)} 条; 缺 smiles {len(targets)} 条")

    for i, key in enumerate(targets, 1):
        spec = seed.get(key, {})
        if _POLYMER_HINTS.search(key):
            failed.append((key, "polymer/dispersion — 无单一分子, 跳过网络"))
            print(f"[{i}/{len(targets)}] SKIP(polymer)  {key}")
            continue
        t0 = time.time()
        smi = _pubchem_smiles(key)
        dt = time.time() - t0
        if smi:
            resolved[key] = smi
            print(f"[{i}/{len(targets)}] OK   {key}  ({dt:.1f}s)")
        else:
            failed.append((key, "pubchem no hit"))
            print(f"[{i}/{len(targets)}] FAIL {key}  ({dt:.1f}s)")
        time.sleep(_RATE_S)

    # cas_no 补充尝试(seed 中带 formula/cas 的可顺带补 cas——本工具只补 smiles,
    # cas 由实体同步/人工处理, 记录有 cas 的便于核验)
    out = {
        "resolved": resolved,
        "failed": [{"name": k, "reason": r} for k, r in failed],
        "skipped_no_missing": skipped,
    }
    out_path = _BACKEND / "scripts" / "backfill_catalog_smiles_out.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n成功 {len(resolved)} / 失败 {len(failed)}")
    print(f"结果: {out_path}")
    if resolved:
        print("\n成功表(可直接 patch seed):")
        for k, s in resolved.items():
            print(f"    {k!r}: {s!r},")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
