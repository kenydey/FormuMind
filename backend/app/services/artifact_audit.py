"""Artifact reproducibility audit — artifact_audit.py

对落盘工件（模型训练产物、配方优化结果、导出报告等）做八项可复现性审计。
纯函数、无外部依赖；空 artifact / 缺字段不抛错，只产出 warn/fail。

输入规范（dict，缺字段按缺失处理）：
    {name, version, params, inputs, outputs, env, dependencies}
外加可选定位字段：artifact_id / storage_path / path / uri / url / location。

preflight 的接入钩子由另一个 agent 实现，本模块只暴露 audit_artifact。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["AuditFinding", "AUDIT_CHECKS", "audit_artifact"]

AuditStatus = Literal["pass", "fail", "warn"]

# 检查项名（输出顺序固定）
AUDIT_CHECKS = (
    "dependencies_declared",
    "random_seed",
    "input_validation",
    "output_schema",
    "version_pinned",
    "env_snapshot",
    "params_complete",
    "artifact_locatable",
)

# 版本未锁定的标记值
_UNPINNED_VERSIONS = {"", "latest"}


@dataclass
class AuditFinding:
    """单项审计结果。"""

    check: str  # 检查项名（见 AUDIT_CHECKS）
    status: AuditStatus  # "pass" | "fail" | "warn"
    detail: str  # 中文说明


def _as_dict(value: Any) -> dict:
    """非 dict 值一律按空 dict 处理，避免上游字段缺失/类型异常时抛错。"""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def audit_artifact(artifact: dict) -> list[AuditFinding]:
    """对单个工件做八项可复现性审计，返回按固定顺序排列的检查结果。"""
    art = _as_dict(artifact)
    findings = [
        _check_dependencies_declared(art),
        _check_random_seed(art),
        _check_input_validation(art),
        _check_output_schema(art),
        _check_version_pinned(art),
        _check_env_snapshot(art),
        _check_params_complete(art),
        _check_artifact_locatable(art),
    ]
    return findings


def _check_dependencies_declared(art: dict) -> AuditFinding:
    """1. 依赖声明：dependencies 非空且每项含 name+version，否则 fail。"""
    deps = _as_list(art.get("dependencies"))
    if not deps:
        return AuditFinding("dependencies_declared", "fail", "未声明任何依赖，无法还原运行环境")
    bad = [
        i
        for i, d in enumerate(deps)
        if not isinstance(d, dict) or not d.get("name") or not d.get("version")
    ]
    if bad:
        return AuditFinding(
            "dependencies_declared",
            "fail",
            f"第 {', '.join(str(i) for i in bad)} 项依赖缺少 name 或 version",
        )
    return AuditFinding("dependencies_declared", "pass", f"已声明 {len(deps)} 项依赖，均含 name 与 version")


def _has_random_seed(art: dict) -> tuple[bool, str]:
    """在 params / env 中查找随机种子或确定性声明（key 大小写不敏感）。"""
    for section in ("params", "env"):
        sec = _as_dict(art.get(section))
        for key, value in sec.items():
            k = str(key).lower()
            if "seed" in k and value is not None:
                return True, f"{section}.{key}={value}"
            if k in ("deterministic", "determinism", "reproducible") and value:
                return True, f"{section}.{key} 已声明确定性"
    return False, ""


def _check_random_seed(art: dict) -> AuditFinding:
    """2. 随机种子：params/env 含 seed 或声明确定性，否则 warn。"""
    ok, where = _has_random_seed(art)
    if ok:
        return AuditFinding("random_seed", "pass", f"随机种子/确定性声明位于 {where}")
    return AuditFinding("random_seed", "warn", "未找到随机种子或确定性声明，结果可能不可复现")


def _check_input_validation(art: dict) -> AuditFinding:
    """3. 输入校验：inputs 每项有 id/hash，否则 warn。"""
    inputs = _as_list(art.get("inputs"))
    if not inputs:
        return AuditFinding("input_validation", "warn", "未声明输入项，无法校验输入完整性")
    bad = [
        i
        for i, item in enumerate(inputs)
        if not isinstance(item, dict) or not (item.get("id") or item.get("hash"))
    ]
    if bad:
        return AuditFinding(
            "input_validation",
            "warn",
            f"第 {', '.join(str(i) for i in bad)} 项输入缺少 id 与 hash，无法验证输入一致性",
        )
    return AuditFinding("input_validation", "pass", f"{len(inputs)} 项输入均有 id 或 hash")


def _check_output_schema(art: dict) -> AuditFinding:
    """4. 输出 schema：outputs 每项有 schema/columns，否则 warn。"""
    outputs = _as_list(art.get("outputs"))
    if not outputs:
        return AuditFinding("output_schema", "warn", "未声明输出项，无法校验输出结构")
    bad = [
        i
        for i, item in enumerate(outputs)
        if not isinstance(item, dict) or not (item.get("schema") or item.get("columns"))
    ]
    if bad:
        return AuditFinding(
            "output_schema",
            "warn",
            f"第 {', '.join(str(i) for i in bad)} 项输出缺少 schema 或 columns",
        )
    return AuditFinding("output_schema", "pass", f"{len(outputs)} 项输出均有 schema 或 columns")


def _check_version_pinned(art: dict) -> AuditFinding:
    """5. 版本锁定：关键依赖不能是 latest 或空版本，否则 fail。"""
    deps = _as_list(art.get("dependencies"))
    named = [d for d in deps if isinstance(d, dict) and d.get("name")]
    if not named:
        return AuditFinding("version_pinned", "fail", "无已声明依赖可校验版本锁定")
    bad = [
        str(d.get("name"))
        for d in named
        if str(d.get("version") or "").strip().lower() in _UNPINNED_VERSIONS
    ]
    if bad:
        return AuditFinding(
            "version_pinned",
            "fail",
            f"以下依赖版本未锁定（latest/空）: {', '.join(bad)}",
        )
    return AuditFinding("version_pinned", "pass", f"{len(named)} 项依赖版本均已锁定")


def _check_env_snapshot(art: dict) -> AuditFinding:
    """6. 环境快照：env 含 python/关键包版本，否则 warn。"""
    env = _as_dict(art.get("env"))
    if not env:
        return AuditFinding("env_snapshot", "warn", "未记录环境快照，无法还原运行时环境")
    keys = {str(k).lower() for k in env.keys()}
    has_python = any("python" in k for k in keys)
    has_packages = bool(keys & {"packages", "pip_freeze", "requirements", "conda_env", "site-packages"})
    if has_python or has_packages:
        return AuditFinding("env_snapshot", "pass", "环境快照含 python/包版本信息")
    return AuditFinding("env_snapshot", "warn", "环境快照缺少 python 或包版本信息")


def _check_params_complete(art: dict) -> AuditFinding:
    """7. 参数完整：params 非空，否则 warn。"""
    params = _as_dict(art.get("params"))
    if not params:
        return AuditFinding("params_complete", "warn", "未记录运行参数，无法复现本次运行")
    return AuditFinding("params_complete", "pass", f"已记录 {len(params)} 个运行参数")


_LOCATOR_KEYS = ("artifact_id", "storage_path", "path", "uri", "url", "location", "file_path")


def _check_artifact_locatable(art: dict) -> AuditFinding:
    """8. 工件可定位：有存储路径或 artifact_id，否则 fail。"""
    for key in _LOCATOR_KEYS:
        if art.get(key):
            return AuditFinding("artifact_locatable", "pass", f"可通过 {key} 定位工件")
    return AuditFinding("artifact_locatable", "fail", "缺少存储路径与 artifact_id，工件不可定位")
