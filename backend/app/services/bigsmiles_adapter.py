"""BigSMILES 聚合物表示适配器（2026-10-11）。

BigSMILES 是聚合物/低聚物的标准线表示法（SMILES 无法表达重复单元、
支化、共聚组成）。涂料配方平台中树脂类原料需要此表示。

设计：
- 纯 Python，无模型，CPU 零压力（<10MB）。
- `bigsmiles` 包可选依赖；未安装时所有函数中性返回，fail-open。
- 提供：校验、重复单元提取（→ RDKit 算分子量）、随机采样。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# BigSMILES 随机对象基本形：{...}
_BIGSMILES_RE = re.compile(r"\{.*\}")


def bigsmiles_available() -> bool:
    try:
        __import__("bigsmiles")
        return True
    except Exception:
        return False


def is_bigsmiles(s: str) -> bool:
    """粗判是否为 BigSMILES（随机对象花括号语法）。"""
    return bool(s and _BIGSMILES_RE.search(s))


def validate_bigsmiles(s: str) -> dict:
    """校验 BigSMILES 合法性。返回 {"valid": bool, "error": str|None}。永不抛异常。"""
    if not s or not is_bigsmiles(s):
        return {"valid": False, "error": "not a BigSMILES string"}
    if not bigsmiles_available():
        return {"valid": True, "error": None}  # 无库时只做语法粗判
    try:
        from bigsmiles import BigSMILES
        BigSMILES(s)
        return {"valid": True, "error": None}
    except Exception as exc:
        return {"valid": False, "error": str(exc)[:200]}


def repeat_unit_smiles(bigsmiles_str: str) -> str | None:
    """提取第一个重复单元的 SMILES（去描述符），用于 RDKit 算分子量。无库时返回 None。"""
    if not bigsmiles_available() or not is_bigsmiles(bigsmiles_str):
        return None
    try:
        from bigsmiles import BigSMILES
        bs = BigSMILES(bigsmiles_str)
        # 取第一个 stochastic object 的第一个 repeat unit
        for obj in getattr(bs, "objects", []):
            units = getattr(obj, "repeat_units", None) or []
            if units:
                # 去 bonding descriptor（[<]/[>] 等）后返回
                raw = str(units[0])
                cleaned = re.sub(r"\[<?\]?\]?", "", raw)
                return cleaned or None
        return None
    except Exception as exc:
        logger.debug("repeat_unit_smiles failed: %s", exc)
        return None


def repeat_unit_mw(bigsmiles_str: str) -> float | None:
    """重复单元分子量（g/mol）。需 RDKit；任一缺失返回 None。"""
    smiles = repeat_unit_smiles(bigsmiles_str)
    if not smiles:
        return None
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        return float(Descriptors.MolWt(mol))
    except Exception:
        return None
