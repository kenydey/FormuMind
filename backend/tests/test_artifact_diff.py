"""Tests for W4-3 (P1-14): artifact version diff.

Covers: insert/delete/modify correctness at the op level, character-level
secondary alignment for changed lines, truncated degradation above 200 KB
(line-level ops only, truncated=True), and the API end-to-end contract::

    {"version_id", "against_id", "truncated",
     "ops": [{"type": "equal"|"insert"|"delete",
              "old_text": str, "new_text": str}]}
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import artifact_versions as api_mod
from app.services import artifact_diff as diffsvc
from app.services import artifact_versions as svc


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def api_client(data_dir):
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


def _lineage(api_client, name="report-a"):
    r = api_client.post(
        "/api/artifacts/lineages",
        json={"project_id": "proj-1", "name": name, "kind": "report"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _version(api_client, lineage_id, content="v1 text", based_on=None):
    body = {"content": content}
    if based_on:
        body["based_on_version_id"] = based_on
    r = api_client.post(f"/api/artifacts/lineages/{lineage_id}/versions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── pure diff correctness ────────────────────────────────────────────────────

def _assert_contract(result, *, truncated):
    """Shared contract checks: op shapes + exact reassembly of both texts."""
    assert result["truncated"] is truncated
    allowed = {"equal", "insert", "delete"}
    for op in result["ops"]:
        assert op["type"] in allowed, op
        assert op["type"] != "equal" or op["old_text"] == op["new_text"]
        assert op["type"] != "insert" or op["old_text"] == ""
        assert op["type"] != "delete" or op["new_text"] == ""


def _assert_reassembly(old_text, new_text, result):
    ops = result["ops"]
    assert "".join(op["old_text"] for op in ops) == old_text
    assert "".join(op["new_text"] for op in ops) == new_text


def test_diff_identical_texts():
    result = diffsvc.diff_versions("same\ntext\n", "same\ntext\n")
    _assert_contract(result, truncated=False)
    assert result["ops"] == [
        {"type": "equal", "old_text": "same\ntext\n", "new_text": "same\ntext\n"}
    ]


def test_diff_insert_and_delete():
    old, new = "keep\nold\n", "keep\nnew\n"
    result = diffsvc.diff_versions(old, new)
    _assert_contract(result, truncated=False)
    _assert_reassembly(old, new, result)
    assert result["ops"][0] == {"type": "equal", "old_text": "keep\n", "new_text": "keep\n"}
    # The changed line is character-aligned, not a whole-line delete+insert.
    assert not any(
        op == {"type": "delete", "old_text": "old\n", "new_text": ""}
        for op in result["ops"]
    )


def test_diff_char_level_secondary_alignment():
    # Single-line change must be split into fine-grained ops, not a whole-line
    # delete+insert pair.
    old, new = "hello world", "hello there"
    result = diffsvc.diff_versions(old, new)
    _assert_contract(result, truncated=False)
    _assert_reassembly(old, new, result)
    ops = result["ops"]
    assert ops[0] == {"type": "equal", "old_text": "hello ", "new_text": "hello "}
    assert not any(
        op == {"type": "delete", "old_text": "hello world", "new_text": ""}
        for op in ops
    )
    # Fragments reference only text that actually changed / stayed.
    assert any(op["type"] == "delete" and op["old_text"] for op in ops)
    assert any(op["type"] == "insert" and op["new_text"] for op in ops)


def test_diff_empty_texts():
    result = diffsvc.diff_versions("", "")
    assert result == {"truncated": False, "ops": []}
    result = diffsvc.diff_versions("", "added\n")
    _assert_contract(result, truncated=False)
    assert result["ops"] == [
        {"type": "insert", "old_text": "", "new_text": "added\n"}
    ]


# ── truncated degradation ────────────────────────────────────────────────────

def test_diff_truncated_above_200kb():
    lines = [f"data line {i:05d} value xyz\n" for i in range(9000)]  # ~234 KB
    big_old = "".join(lines)
    assert len(big_old.encode("utf-8")) > diffsvc.TRUNCATED_LIMIT_BYTES
    big_new = big_old.replace("data line 00042", "CHANGED LINE 00042", 1)
    result = diffsvc.diff_versions(big_old, big_new)
    assert result["truncated"] is True
    # Only line-level ops: the changed line appears as a whole-line
    # delete+insert pair, never character fragments.
    deletes = [op for op in result["ops"] if op["type"] == "delete"]
    inserts = [op for op in result["ops"] if op["type"] == "insert"]
    assert len(deletes) == 1 and len(inserts) == 1
    assert deletes[0]["old_text"] == "data line 00042 value xyz\n"
    assert inserts[0]["new_text"] == "CHANGED LINE 00042 value xyz\n"
    assert deletes[0]["new_text"] == "" and inserts[0]["old_text"] == ""


def test_diff_not_truncated_just_below_limit():
    small = "".join(f"row {i:06d}\n" for i in range(15000))  # ~165 KB < 200 KB
    assert len(small.encode("utf-8")) < diffsvc.TRUNCATED_LIMIT_BYTES
    result = diffsvc.diff_versions(small, small + "tail\n")
    _assert_contract(result, truncated=False)
    _assert_reassembly(small, small + "tail\n", result)


def test_diff_heavy_repetition_never_hangs():
    """Regression: tens of thousands of identical lines must not turn the
    matcher quadratic. Runs in a thread so a regression fails instead of
    hanging the suite."""
    import threading

    big_old = "same line\n" * 20000  # ~200 KB of identical lines, under limit
    big_new = big_old.replace("same line", "SAME LINE", 1)
    box: dict = {}
    worker = threading.Thread(
        target=lambda: box.update(result=diffsvc.diff_versions(big_old, big_new)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=20)
    assert not worker.is_alive(), "diff hung on heavily repetitive input"
    result = box["result"]
    _assert_contract(result, truncated=False)
    _assert_reassembly(big_old, big_new, result)


# ── API end-to-end ───────────────────────────────────────────────────────────

def test_api_diff_contract(api_client):
    lin = _lineage(api_client)
    v1 = _version(api_client, lin["lineage_id"], content="line1\nline2 old\nline3\n")
    v2 = _version(
        api_client, lin["lineage_id"], content="line1\nline2 new\nline3\n",
        based_on=v1["version_id"],
    )
    r = api_client.get(
        f"/api/artifacts/versions/{v2['version_id']}/diff",
        params={"against": v1["version_id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_id"] == v2["version_id"]
    assert body["against_id"] == v1["version_id"]
    assert body["truncated"] is False
    assert isinstance(body["ops"], list) and body["ops"]
    allowed = {"equal", "insert", "delete"}
    for op in body["ops"]:
        assert op["type"] in allowed
        assert op["type"] != "equal" or op["old_text"] == op["new_text"]
        assert op["type"] != "insert" or op["old_text"] == ""
        assert op["type"] != "delete" or op["new_text"] == ""
    # changed line shows up as fine-grained delete+insert (char-level)
    assert any(op == {"type": "delete", "old_text": "old", "new_text": ""} for op in body["ops"])
    assert any(op == {"type": "insert", "old_text": "", "new_text": "new"} for op in body["ops"])


def test_api_diff_missing_version_404(api_client):
    lin = _lineage(api_client)
    v1 = _version(api_client, lin["lineage_id"], content="text\n")
    r = api_client.get(
        f"/api/artifacts/versions/{v1['version_id']}/diff",
        params={"against": "no-such-version"},
    )
    assert r.status_code == 404
    r = api_client.get("/api/artifacts/versions/no-such-version/diff", params={"against": v1["version_id"]})
    assert r.status_code == 404


def test_api_diff_missing_against_param_422(api_client):
    lin = _lineage(api_client)
    v1 = _version(api_client, lin["lineage_id"], content="text\n")
    r = api_client.get(f"/api/artifacts/versions/{v1['version_id']}/diff")
    assert r.status_code == 422
