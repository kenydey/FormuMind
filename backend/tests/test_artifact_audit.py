"""Unit tests for artifact_audit (W1-9 / P0-12).

八项检查逐项构造 pass/fail 用例；空 artifact / 缺字段不抛错。
"""
from __future__ import annotations

import pytest

from app.services import artifact_audit as aa


def base_artifact() -> dict:
    """全部八项通过的基准工件。"""
    return {
        "name": "dispersion_stability_v1",
        "version": "1.2.0",
        "params": {"seed": 42, "n_iter": 200, "temp_C": 60},
        "inputs": [{"id": "formula-123", "hash": "sha256:abc"}],
        "outputs": [{"name": "result.csv", "schema": {"columns": ["x", "y"]}}],
        "env": {"python": "3.11.5", "packages": {"numpy": "1.26.0"}},
        "dependencies": [
            {"name": "numpy", "version": "1.26.0"},
            {"name": "pandas", "version": "2.1.0"},
        ],
        "artifact_id": "art-001",
        "storage_path": "/data/artifacts/art-001",
    }


def status_of(findings: list[aa.AuditFinding], check: str) -> str:
    return next(f.status for f in findings if f.check == check)


# ---------- 全通过 / 结构 ----------


def test_all_pass():
    findings = aa.audit_artifact(base_artifact())
    assert [f.check for f in findings] == list(aa.AUDIT_CHECKS)  # 固定顺序
    assert all(f.status == "pass" for f in findings)
    assert all(isinstance(f.detail, str) and f.detail for f in findings)


def test_empty_artifact_no_raise():
    findings = aa.audit_artifact({})
    assert len(findings) == 8
    assert all(f.status in ("fail", "warn") for f in findings)


def test_none_and_non_dict_no_raise():
    assert len(aa.audit_artifact(None)) == 8
    assert len(aa.audit_artifact("not-a-dict")) == 8


def test_missing_fields_no_raise():
    # 只有 name：各检查按缺字段处理，不抛错
    findings = aa.audit_artifact({"name": "x"})
    assert len(findings) == 8
    assert status_of(findings, "params_complete") == "warn"


# ---------- 1. dependencies_declared ----------


@pytest.mark.parametrize("deps", [None, [], "numpy"])
def test_dependencies_declared_fail_empty(deps):
    art = base_artifact()
    art["dependencies"] = deps
    assert status_of(aa.audit_artifact(art), "dependencies_declared") == "fail"


@pytest.mark.parametrize(
    "deps",
    [
        [{"version": "1.0"}],  # 缺 name
        [{"name": "numpy"}],  # 缺 version
        [{"name": "", "version": "1.0"}],  # 空 name
        ["numpy"],  # 非 dict
    ],
)
def test_dependencies_declared_fail_incomplete(deps):
    art = base_artifact()
    art["dependencies"] = deps
    assert status_of(aa.audit_artifact(art), "dependencies_declared") == "fail"


# ---------- 2. random_seed ----------


def test_random_seed_missing_warn():
    art = base_artifact()
    art["params"] = {"n_iter": 200}
    assert status_of(aa.audit_artifact(art), "random_seed") == "warn"


def test_random_seed_in_env_pass():
    art = base_artifact()
    art["params"] = {"n_iter": 200}
    art["env"] = {"python": "3.11.5", "random_seed": 7}
    assert status_of(aa.audit_artifact(art), "random_seed") == "pass"


def test_random_seed_deterministic_pass():
    art = base_artifact()
    art["params"] = {"n_iter": 200, "deterministic": True}
    assert status_of(aa.audit_artifact(art), "random_seed") == "pass"


# ---------- 3. input_validation ----------


def test_input_validation_missing_id_hash_warn():
    art = base_artifact()
    art["inputs"] = [{"name": "formula-123"}]  # 无 id/hash
    assert status_of(aa.audit_artifact(art), "input_validation") == "warn"


def test_input_validation_empty_warn():
    art = base_artifact()
    art["inputs"] = []
    assert status_of(aa.audit_artifact(art), "input_validation") == "warn"


# ---------- 4. output_schema ----------


def test_output_schema_missing_warn():
    art = base_artifact()
    art["outputs"] = [{"name": "result.csv"}]  # 无 schema/columns
    assert status_of(aa.audit_artifact(art), "output_schema") == "warn"


def test_output_schema_columns_pass():
    art = base_artifact()
    art["outputs"] = [{"name": "result.csv", "columns": ["x", "y"]}]
    assert status_of(aa.audit_artifact(art), "output_schema") == "pass"


# ---------- 5. version_pinned ----------


def test_version_pinned_latest_fail():
    art = base_artifact()
    art["dependencies"] = [
        {"name": "numpy", "version": "1.26.0"},
        {"name": "torch", "version": "latest"},
    ]
    findings = aa.audit_artifact(art)
    assert status_of(findings, "version_pinned") == "fail"
    assert "torch" in next(f.detail for f in findings if f.check == "version_pinned")


@pytest.mark.parametrize("version", ["", "   ", "LATEST"])
def test_version_pinned_empty_or_case_fail(version):
    art = base_artifact()
    art["dependencies"] = [{"name": "numpy", "version": version}]
    findings = aa.audit_artifact(art)
    assert status_of(findings, "version_pinned") == "fail"
    if version == "":
        # dependencies_declared 独立 fail（空 version）
        assert status_of(findings, "dependencies_declared") == "fail"


# ---------- 6. env_snapshot ----------


def test_env_snapshot_missing_python_warn():
    art = base_artifact()
    art["env"] = {"os": "linux"}  # 无 python/包版本
    assert status_of(aa.audit_artifact(art), "env_snapshot") == "warn"


def test_env_snapshot_empty_warn():
    art = base_artifact()
    art["env"] = {}
    assert status_of(aa.audit_artifact(art), "env_snapshot") == "warn"


# ---------- 7. params_complete ----------


@pytest.mark.parametrize("params", [None, {}, "n=1"])
def test_params_complete_empty_warn(params):
    art = base_artifact()
    art["params"] = params
    assert status_of(aa.audit_artifact(art), "params_complete") == "warn"


# ---------- 8. artifact_locatable ----------


def test_artifact_locatable_missing_fail():
    art = base_artifact()
    del art["artifact_id"]
    del art["storage_path"]
    assert status_of(aa.audit_artifact(art), "artifact_locatable") == "fail"


def test_artifact_locatable_uri_pass():
    art = base_artifact()
    del art["artifact_id"]
    del art["storage_path"]
    art["uri"] = "s3://bucket/art-001"
    assert status_of(aa.audit_artifact(art), "artifact_locatable") == "pass"
