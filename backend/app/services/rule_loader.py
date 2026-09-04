"""领域规则配置加载(R1, 2026-09-04)。

配方体系的安全/分类/歧义规则从代码硬编码抽离到 TOML(包内
``app/resources/rules/``, 可被 ``FORMUMIND_RULES_DIR`` 覆盖指向用户可编辑
副本)。零新依赖(tomllib, py3.11 stdlib), TOML 支持注释、git 可评审。

加载失败/文件缺失 → 返回内置兜底常量(即迁移前的硬编码值)——配置化永不
破坏现有行为。进程启动后规则变更需重启生效(lru_cache, 规则低频变更)。

改造模式(消费方):
    rules = load_rules("acid_stability")   # lru_cache 命中 ~µs 级
    alkali = rules["strong_alkali"]
"""
from __future__ import annotations

import functools
import logging
import os
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

_RULES_DIR_ENV = "FORMUMIND_RULES_DIR"
# app/resources/rules/ —— rule_loader.py 位于 app/services/, 上两级为 app/。
_PKG_RULES_DIR = Path(__file__).resolve().parent.parent / "resources" / "rules"

# 兜底默认(1:1 迁移自改造前的硬编码常量——见各消费模块 git 历史)。
# 注意: acid_stability 兜底仅是 TOML 缺失时的最后防线; _RESIN_ROLES 等
# 检查策略常量仍留在消费代码(属逻辑非数据)。
_FALLBACKS: dict[str, dict] = {
    "acid_stability": {
        "strong_alkali": {
            "exact": ["Sodium metasilicate", "Sodium tripolyphosphate"],
            "prefixes": ["Sodium hydroxide", "Potassium hydroxide"],
            "reason": "强碱 {names} 与酸性浴 pH 冲突（中和放热，浴失控）",
        },
        "carbonate_fillers": {
            "substrings": ["carbonate", "bicarbonate", "chalk", "limestone"],
            "reason": "碳酸盐填料 {names} 在酸性浴中释放 CO₂（起泡）",
        },
        "reactive_metals": {
            "names": ["Zinc dust", "Zinc oxide", "Aluminum powder", "Aluminium powder"],
            "reason": "活泼金属 {names} 在酸性浴中析氢（安全与膜层缺陷风险）",
        },
        "amine_neutralised": {
            "substrings": ["amine", "ammonia"],
            "reason": "含胺中和剂组分在低 pH 浴中可能质子化失效（建议核实乳液酸耐受性）",
        },
    },
    "linker_roles": {
        "role_hints": {
            "resin": ["resin", "epoxy", "binder"],
            "hardener": ["hardener", "curing_agent"],
            "catalyst": ["catalyst"],
            "pigment": ["pigment", "colorant"],
            "filler": ["filler", "extender"],
            "solvent": ["solvent", "carrier"],
            "additive": ["additive", "defoamer", "dispersant", "wetting_agent"],
            "inhibitor": ["corrosion_inhibitor", "flash_rust_inhibitor"],
        }
    },
    "ambiguous_terms": {
        "水性": {
            "candidates": [
                ["waterborne acrylic emulsion（水乳液）", ""],
                ["water-reducible solvent paint（水稀释溶剂型）", ""],
            ]
        },
        "快干": {
            "candidates": [
                ["常温自干体系", ""],
                ["低温烘烤加速体系", ""],
            ]
        },
        "环氧": {
            "candidates": [
                ["双酚A型环氧树脂", "chem:catalog:bisphenol_a_epoxy"],
                ["环氧改性丙烯酸", ""],
            ]
        },
    },
}


def _rules_dir() -> Path:
    over = os.environ.get(_RULES_DIR_ENV)
    if over:
        p = Path(over)
        if p.is_dir():
            return p
        logger.warning(
            "rules: FORMUMIND_RULES_DIR=%s 不存在, 回退包内默认 %s", over, _PKG_RULES_DIR
        )
    return _PKG_RULES_DIR


@functools.lru_cache(maxsize=16)
def load_rules(kind: str) -> dict:
    """Load one rule table (TOML) with built-in fallback on any failure."""
    fallback = _FALLBACKS.get(kind)
    if fallback is None:
        raise KeyError(f"未知规则表: {kind!r} (可用: {sorted(_FALLBACKS)})")
    path = _rules_dir() / f"{kind}.toml"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return data
    except FileNotFoundError:
        logger.warning("rules: %s 缺失, 使用内置兜底", path)
        return fallback
    except tomllib.TOMLDecodeError as exc:
        logger.exception("rules: %s 解析失败, 使用内置兜底: %s", path, exc)
        return fallback
    except OSError as exc:
        logger.exception("rules: %s 读取失败, 使用内置兜底: %s", path, exc)
        return fallback


def reload_rules() -> None:
    """清空缓存(测试/运维用: 规则文件变更后调用使新配置生效)。"""
    load_rules.cache_clear()
