"""N-2/N-3: 配方化学有效性自动校验 + 成分兼容性检查。

N-2: RDKit 校验 SMILES 有效性/化合价；PubChem 核验 CAS 号。
N-3: 内置不兼容对规则库，检出冲突时警告。

全部 fail-open，不阻塞推荐主流程。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# N-3: 不兼容对规则库（成分名关键词，大小写不敏感）
# 格式：(关键词A, 关键词B, 原因)
# O-2: 从 6 对扩展到 20 对，覆盖常见涂料不兼容组合
INCOMPATIBLE_PAIRS: tuple[tuple[str, str, str], ...] = (
    # 强酸 + 强碱/活泼金属
    ("硫酸", "氢氧化钠", "强酸+强碱剧烈放热"),
    ("sulfuric acid", "sodium hydroxide", "strong acid + strong base"),
    ("盐酸", "氢氧化钠", "强酸+强碱放热"),
    ("hydrochloric acid", "sodium hydroxide", "acid + base exothermic"),
    # 氧化剂 + 易燃物/有机物
    ("硝酸", "乙醇", "氧化剂+易燃物"),
    ("nitric acid", "ethanol", "oxidizer + flammable"),
    ("过氧化氢", "丙酮", "氧化剂+有机物"),
    ("hydrogen peroxide", "acetone", "oxidizer + organic"),
    ("高锰酸钾", "甘油", "强氧化剂+有机物可自燃"),
    ("potassium permanganate", "glycerol", "oxidizer + organic spontaneous"),
    # 产气反应
    ("盐酸", "次氯酸钠", "产生氯气"),
    ("hydrochloric acid", "sodium hypochlorite", "chlorine gas"),
    ("氨水", "次氯酸钠", "产生氯胺"),
    ("ammonia", "sodium hypochlorite", "chloramine"),
    ("碳酸钙", "盐酸", "产生 CO2（除非有意）"),
    ("calcium carbonate", "hydrochloric acid", "CO2 release"),
    # 异氰酸酯相关
    ("异氰酸酯", "水", "反应放出 CO2（除非有意发泡）"),
    ("isocyanate", "water", "CO2 release unless foaming intended"),
    ("异氰酸酯", "乙醇", "反应生成氨基甲酸酯（配比失衡）"),
    ("isocyanate", "ethanol", "unbalanced urethane reaction"),
    # 环氧相关
    ("环氧树脂", "强酸", "可能开环降解"),
    ("epoxy resin", "strong acid", "possible ring-opening degradation"),
)


def validate_smiles(smiles: str) -> tuple[bool, str]:
    """N-2: RDKit 校验 SMILES。返回 (有效, 原因)。"""
    if not smiles or not smiles.strip():
        return True, ""
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles.strip())
        if mol is None:
            return False, f"SMILES 无效: {smiles[:40]}"
        # 化合价检查（Sanitize 已做，此处显式确认）
        try:
            Chem.SanitizeMol(mol)
        except Exception as e:
            return False, f"化合价异常: {e}"[:100]
        return True, ""
    except ImportError:
        return True, ""  # RDKit 不可用时跳过
    except Exception as e:
        logger.debug("SMILES validate failed: %s", e)
        return True, ""


def verify_cas(cas: str) -> tuple[bool, str]:
    """N-2: PubChem 核验 CAS 号。返回 (有效, 原因)。fail-open。"""
    if not cas or not cas.strip():
        return True, ""
    cas = cas.strip()
    # CAS 格式：2-7位-2位-1位校验码
    if not re.match(r"^\d{2,7}-\d{2}-\d$", cas):
        return False, f"CAS 格式无效: {cas}"
    try:
        from . import chemtools
        if not chemtools.pubchem_available():
            return True, ""
        # 用 chemtools 的缓存机制查询
        cid = chemtools._pubchem_cid(cas)
        if cid is None:
            return False, f"CAS 在 PubChem 无记录: {cas}"
        return True, ""
    except Exception:
        return True, ""  # fail-open


def check_compatibility(components: list[str]) -> list[str]:
    """N-3: 检查成分间不兼容对。返回警告列表。"""
    warnings = []
    lowered = [c.lower() for c in components]
    for kw_a, kw_b, reason in INCOMPATIBLE_PAIRS:
        has_a = any(kw_a.lower() in c for c in lowered)
        has_b = any(kw_b.lower() in c for c in lowered)
        if has_a and has_b:
            warnings.append(f"兼容性警告：{kw_a} + {kw_b} —— {reason}")
    return warnings


def validate_formula_chemistry(formula) -> list[str]:
    """对单个配方做 N-2+N-3 校验。返回警告列表。"""
    warnings = []
    comp_names = []
    for comp in getattr(formula, "components", []) or []:
        name = getattr(comp, "name", "") or ""
        comp_names.append(name)
        # N-2: SMILES 校验
        smiles = getattr(comp, "smiles", "") or ""
        if smiles:
            ok, reason = validate_smiles(smiles)
            if not ok:
                warnings.append(f"{name}: {reason}")
        # N-2: CAS 校验
        cas = getattr(comp, "cas", "") or getattr(comp, "cas_no", "") or ""
        if cas:
            ok, reason = verify_cas(cas)
            if not ok:
                warnings.append(f"{name}: {reason}")
        # O-3: CAS 自动补全 —— 无 CAS 时用 PubChem 名称查询补全
        elif name:
            try:
                completed = autocomplete_cas(name)
                if completed:
                    try:
                        if hasattr(comp, "cas"):
                            comp.cas = completed
                        elif hasattr(comp, "cas_no"):
                            comp.cas_no = completed
                    except Exception:
                        pass
            except Exception:
                pass
    # N-3: 兼容性
    warnings.extend(check_compatibility(comp_names))
    return warnings


def autocomplete_cas(name: str) -> str | None:
    """O-3: 用 PubChem 名称→CAS 自动补全。fail-open，返回 CAS 或 None。"""
    if not name or not name.strip():
        return None
    try:
        from . import chemtools
        if not chemtools.pubchem_available():
            return None
        # chemtools._pubchem_name_to_cas 已有缓存机制
        return chemtools._pubchem_name_to_cas(name.strip())
    except Exception:
        return None
