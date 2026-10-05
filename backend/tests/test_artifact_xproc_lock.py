"""Cross-process transaction locks (A-route) for artifact versions.

The ``threading.RLock``s in ``artifact_versions`` are process-local; the
single-write fcntl inside ``_atomic_write_bytes`` only covers one file
replace. These tests prove the *whole* read → validate → write transaction
is now serialized across processes via the per-version / per-lineage
``.txn.lock`` files:

- N processes racing ``finalize_version`` on the same version: exactly one
  wins, the rest get ``ValueError``, and finalized immutability holds
  (content + sha256 untouched).
- N processes racing restore (``create_version`` copy-on-write off the same
  finalized version): no ``version_ids`` lost to last-writer-wins, every new
  version carries the base content and the right ``based_on_version_id``.
- Deterministic interleaving: while one process holds the version txn lock,
  another process's ``finalize_version`` blocks until the lock is released —
  proving the lock covers the transaction, not just the single file write.

Uses real ``multiprocessing`` — ``fork`` where the platform has it, ``spawn`` on Windows (which has no
fork, and is where the lock is ``msvcrt.locking`` rather than ``flock``: this is the only test that puts the
Windows branch of ``_filelock`` under real cross-process contention). No timing-sensitive assertions except the
blocking check, which waits on events/barriers.
"""
from __future__ import annotations

import multiprocessing as mp
import threading
import time
from pathlib import Path

import pytest

from app.services import artifact_versions as svc


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    return tmp_path


def _mk_submitted(data_note="xproc"):
    lin = svc.create_lineage("proj-xproc", f"{data_note}-report", kind="report")
    v = svc.create_version(lin.lineage_id, b"xproc base content", actor="t")
    svc.submit_version(v.version_id)
    return lin, v


# ── workers (module level: picklable, so they run under ``spawn`` too) ───────

def _context():
    return mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")


def _use_root(root):
    # A spawned child re-imports this module, so it does not inherit the test's monkeypatch of the data root.
    svc._data_root = lambda: Path(root)


def _finalize_worker(vid, barrier, queue, root):
    """Thundering-herd finalize: exactly one process may win."""
    _use_root(root)
    try:
        barrier.wait(timeout=60)
        svc.finalize_version(vid)
        queue.put("finalized")
    except ValueError:
        queue.put("rejected")
    except Exception as exc:  # noqa: BLE001
        queue.put(f"error:{exc!r}")


def _restore_worker(lid, base_vid, idx, barrier, queue, root):
    """Concurrent copy-on-write restore off the same finalized version."""
    _use_root(root)
    try:
        barrier.wait(timeout=60)
        new = svc.create_version(
            lid, None, actor=f"restore-{idx}", based_on_version_id=base_vid
        )
        queue.put(("ok", new.version_id))
    except Exception as exc:  # noqa: BLE001
        queue.put(("error", repr(exc)))


def _txn_lock_holder(vid, lock_path_str, acquired, release, root):
    """Hold the raw version txn lock until ``release`` is set."""
    _use_root(root)
    with svc._xproc_file_lock(Path(lock_path_str)):
        acquired.set()
        assert release.wait(timeout=60), "release event never set"


# ── tests ────────────────────────────────────────────────────────────────────

def test_concurrent_finalize_multiprocess_single_winner(data_dir):
    """A-route: N processes racing finalize → exactly one winner, immutability holds."""
    _lin, v = _mk_submitted("finalize-race")
    vid = v.version_id

    ctx = _context()
    n = 8
    barrier = ctx.Barrier(n)
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_finalize_worker, args=(vid, barrier, queue, str(data_dir)))
        for _ in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert all(p.exitcode == 0 for p in procs), "worker process crashed"

    results = [queue.get(timeout=30) for _ in range(n)]
    assert results.count("finalized") == 1, f"expected exactly one winner: {results}"
    assert results.count("rejected") == n - 1, f"losers must be rejected: {results}"

    final = svc.get_version(vid)
    assert final is not None and final.status == "finalized"
    assert final.finalized_at is not None
    # Immutability: content untouched by the race.
    assert svc.get_version_content(vid) == b"xproc base content"
    assert svc.verify_version(vid)["ok"] is True
    # A late set_content is still rejected after the race.
    with pytest.raises(ValueError, match="immutable"):
        svc.set_version_content(vid, b"too late")


def test_concurrent_restore_multiprocess_no_lost_version_ids(data_dir):
    """A-route: concurrent copy-on-write restores must not lose version_ids."""
    lin, v = _mk_submitted("restore-race")
    svc.finalize_version(v.version_id)
    lid, base_vid = lin.lineage_id, v.version_id

    ctx = _context()
    n = 8
    barrier = ctx.Barrier(n)
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_restore_worker, args=(lid, base_vid, i, barrier, queue, str(data_dir)))
        for i in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert all(p.exitcode == 0 for p in procs), "worker process crashed"

    results = [queue.get(timeout=30) for _ in range(n)]
    assert all(kind == "ok" for kind, _ in results), f"restore failed: {results}"
    created = [new_vid for _, new_vid in results]
    assert len(set(created)) == n, "version ids must be unique"

    # No lost version_ids: lineage lists base + all n restores.
    lineage = svc.get_lineage(lid)
    assert lineage is not None
    assert sorted(lineage.version_ids) == sorted([base_vid] + created)

    # Each restore is an independent staging copy of the base content.
    for new_vid in created:
        nv = svc.get_version(new_vid)
        assert nv is not None
        assert nv.status == "staging"
        assert nv.based_on_version_id == base_vid
        assert svc.get_version_content(new_vid) == b"xproc base content"


def test_finalize_blocks_while_xproc_lock_held(data_dir):
    """Deterministic interleaving: finalize waits for the txn lock, not just a write.

    Process H holds the raw version ``.txn.lock``; the main process starts
    ``finalize_version`` which must block until H releases — proving the
    cross-process lock spans the whole read → validate → write transaction.
    """
    _lin, v = _mk_submitted("hold-race")
    vid = v.version_id
    lock_path = str(svc._version_txn_lock_path(vid))

    ctx = _context()
    acquired = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(
        target=_txn_lock_holder, args=(vid, lock_path, acquired, release, str(data_dir))
    )
    holder.start()
    try:
        assert acquired.wait(timeout=60), "holder never acquired the txn lock"

        done: dict = {}

        def _finalize():
            t0 = time.monotonic()
            svc.finalize_version(vid)
            done["elapsed"] = time.monotonic() - t0

        t = threading.Thread(target=_finalize)
        t.start()
        time.sleep(2.0)
        assert t.is_alive(), (
            "finalize_version must block while another process holds the txn lock "
            "(lock does not cover the transaction)"
        )
        release.set()
        t.join(timeout=60)
        assert not t.is_alive(), "finalize_version hung after lock release"
        assert done.get("elapsed", 0.0) >= 2.0
        assert svc.get_version(vid) is not None
        assert svc.get_version(vid).status == "finalized"
    finally:
        release.set()
        holder.join(timeout=60)
        assert holder.exitcode == 0, "holder process crashed"
