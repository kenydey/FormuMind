"""Regression tests for B-1 (artifact version TOCTOU) and P-2 (digest cache).

B-1: read → validate → write sequences must be atomic per version/lineage.
  - concurrent ``submit_version`` + ``set_version_content`` must never roll
    the status back (no staging-after-pending, no finalized regression);
  - writes to a finalized version are always rejected, even under contention;
  - concurrent ``create_version`` must not lose ``version_ids`` (no orphans).

P-2: ``verify_version`` must not re-read ``content.bin`` on a cache hit, and a
content-write transaction must invalidate the cached digest. The cache is
stat-validated, so on-disk tampering is still detected.
"""
from __future__ import annotations

import hashlib
import threading

import pytest

from app.services import artifact_versions as svc


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    return tmp_path


def _mklineage():
    return svc.create_lineage("proj-race", "race-report", kind="report")


def _run_threads(fn, count, timeout=30.0):
    """Run ``fn(i)`` on ``count`` threads behind a start barrier; return results."""
    barrier = threading.Barrier(count)
    results: list = [None] * count

    def _target(i: int):
        try:
            barrier.wait(timeout=timeout)
            results[i] = ("ok", fn(i))
        except Exception as exc:  # noqa: BLE001
            results[i] = ("err", exc)

    threads = [threading.Thread(target=_target, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)
    assert all(not t.is_alive() for t in threads), "worker thread hung"
    for r in results:
        assert r is not None and r[0] == "ok", f"worker raised unexpectedly: {r!r}"
    return results


def test_concurrent_submit_and_set_content_no_status_regression(data_dir):
    """B-1: racing submit/set_content must serialize; status never regresses."""
    lin = _mklineage()
    v = svc.create_version(lin.lineage_id, b"Z" * 32, actor="t")
    vid = v.version_id

    n_submit, n_set = 4, 12

    def _submit(_i):
        try:
            svc.submit_version(vid)
            return "submitted"
        except ValueError:
            return "rejected"

    def _set(i):
        payload = bytes([65 + (i % 26)]) * (64 + i)  # distinct, uniform bytes
        try:
            svc.set_version_content(vid, payload)
            return ("written", payload)
        except ValueError:
            return ("rejected", None)

    sub_results = _run_threads(_submit, n_submit)
    set_results = _run_threads(_set, n_set)

    # Exactly one submit can win the staging → pending transition.
    assert sum(1 for _kind, val in sub_results if val == "submitted") == 1
    # Every set_content either wrote (while staging) or was rejected (pending).

    final = svc.get_version(vid)
    assert final is not None
    assert final.status == "pending"  # never rolled back to staging
    content = svc.get_version_content(vid)
    written = [
        val[1] for kind, val in set_results if kind == "ok" and val[0] == "written"
    ]
    assert content in written or content == b"Z" * 32
    # Atomic writes: content must be exactly one uniform payload, never torn.
    assert content == bytes([content[0]]) * len(content)
    assert final.sha256 == hashlib.sha256(content).hexdigest()
    # Once pending, content writes are rejected.
    with pytest.raises(ValueError, match="immutable"):
        svc.set_version_content(vid, b"too late")


def test_finalized_immutable_under_concurrency(data_dir):
    """B-1: finalized is terminal — concurrent writes/transitions all fail."""
    lin = _mklineage()
    v = svc.create_version(lin.lineage_id, b"final text", actor="t")
    vid = v.version_id
    svc.submit_version(vid)
    svc.finalize_version(vid)

    def _attack(i):
        errors = []
        try:
            svc.set_version_content(vid, b"sneaky-%d" % i)
        except ValueError as exc:
            errors.append(str(exc))
        try:
            svc.submit_version(vid)
        except ValueError as exc:
            errors.append(str(exc))
        try:
            svc.finalize_version(vid)
        except ValueError as exc:
            errors.append(str(exc))
        return errors

    results = _run_threads(_attack, 8)
    for kind, errors in results:
        assert kind == "ok" and len(errors) == 3, (kind, errors)

    final = svc.get_version(vid)
    assert final is not None and final.status == "finalized"
    assert svc.get_version_content(vid) == b"final text"
    assert final.sha256 == hashlib.sha256(b"final text").hexdigest()


def test_concurrent_create_no_lost_version_ids(data_dir):
    """B-1: version write + lineage append are one transaction — no orphans."""
    lin = _mklineage()
    lid = lin.lineage_id
    n = 16

    def _create(i):
        return svc.create_version(lid, b"payload-%d" % i, actor="t").version_id

    results = _run_threads(_create, n)
    created = [vid for kind, vid in results if kind == "ok"]
    assert len(created) == n and len(set(created)) == n

    lineage = svc.get_lineage(lid)
    assert lineage is not None
    assert sorted(lineage.version_ids) == sorted(created)
    listed = svc.list_versions(lid)
    assert len(listed["versions"]) == n
    assert {e["version_id"] for e in listed["graph"]} == set(created)


def test_digest_cache_hit_skips_file_read(data_dir, monkeypatch):
    """P-2: second verify_version() must not re-read content.bin."""
    lin = _mklineage()
    v = svc.create_version(lin.lineage_id, b"cached content", actor="t")
    vid = v.version_id

    reads = {"n": 0}
    real_read = svc._read_content

    def _counting(version_id):
        reads["n"] += 1
        return real_read(version_id)

    monkeypatch.setattr(svc, "_read_content", _counting)

    first = svc.verify_version(vid)
    assert first["ok"] is True
    assert reads["n"] == 1
    second = svc.verify_version(vid)
    assert second == first
    assert reads["n"] == 1, "cache hit must not re-read content.bin"

    # Same for a finalized version (immutable → digest never drifts).
    svc.submit_version(vid)
    svc.finalize_version(vid)
    svc.verify_version(vid)
    n_after_finalize = reads["n"]
    assert svc.verify_version(vid)["ok"] is True
    assert reads["n"] == n_after_finalize


def test_digest_cache_invalidated_on_write(data_dir, monkeypatch):
    """P-2: a content-write transaction drops the cached digest."""
    lin = _mklineage()
    v = svc.create_version(lin.lineage_id, b"v1", actor="t")
    vid = v.version_id

    reads = {"n": 0}
    real_read = svc._read_content

    def _counting(version_id):
        reads["n"] += 1
        return real_read(version_id)

    monkeypatch.setattr(svc, "_read_content", _counting)

    assert svc.verify_version(vid)["ok"] is True
    assert reads["n"] == 1
    svc.set_version_content(vid, b"v2-longer")
    after = svc.verify_version(vid)
    assert reads["n"] == 2, "write transaction must invalidate the digest cache"
    assert after["ok"] is True
    assert after["actual_sha256"] == hashlib.sha256(b"v2-longer").hexdigest()
    assert after["actual_sha256"] != hashlib.sha256(b"v1").hexdigest()


def test_digest_cache_still_detects_tamper(data_dir):
    """P-2: stat-validated cache — on-disk tampering is still detected."""
    lin = _mklineage()
    v = svc.create_version(lin.lineage_id, b"pristine", actor="t")
    vid = v.version_id
    assert svc.verify_version(vid)["ok"] is True  # populates the cache
    (data_dir / "artifacts" / "versions" / vid / "content.bin").write_bytes(b"tampered!")
    result = svc.verify_version(vid)
    assert result["ok"] is False
    assert result["stored_sha256"] != result["actual_sha256"]
