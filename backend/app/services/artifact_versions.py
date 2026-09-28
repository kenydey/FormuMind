"""Artifact version service (W4-1 / P1-16) — immutable version lineage for
formulation / report / knowledge artifacts.

A ``Lineage`` is the logical file (``project_id`` + ``name`` + ``kind``);
each ``Version`` is an immutable snapshot::

    {version_id, lineage_id, based_on_version_id, status, sha256,
     created_at, actor}

Status machine: ``staging`` (writable) → ``pending`` (submitted for review)
→ ``finalized`` (immutable). Once finalized, any change must create a new
version; illegal transitions are rejected with ``ValueError``.

Persistence mirrors ``services/session_plan.py``: lineages live under
``data/artifacts/lineages/{lineage_id}.json`` and versions under
``data/artifacts/versions/{version_id}/`` (``version.json`` record with an
integrity checksum plus a ``content.bin`` snapshot). ``verify_version()``
recomputes the content sha256 to detect tampering.

``finalize_version()`` invokes the W4-2 evidence-freeze hook
``freeze_evidence()`` fail-open: it freezes the manifest / evidence /
execution snapshots under the version directory, and a freeze failure is
logged but never blocks finalization.

Concurrency (B-1): every read → validate → write sequence is one atomic
transaction under a per-version (or per-lineage) ``threading.RLock`` —
``set_version_content``, ``submit_version`` and ``finalize_version`` hold the
version lock across load, status-guard and save, so a stale in-memory object
can no longer roll back ``pending``/``finalized`` or rewrite immutable
content. ``create_version`` holds the lineage lock across the version write
+ ``version_ids`` append (no orphan versions). All file writes are atomic
(same-dir temp file + flush/fsync + ``os.replace``).

Cross-process transactions (A-route): the threading locks above are
process-local. Every read → validate → write transaction additionally holds
an exclusive ``fcntl.flock`` on a per-version / per-lineage sibling
``.txn.lock`` file for its whole critical section (``create_version`` incl.
restore copy-on-write, ``set_version_content``, ``_transition``,
``finalize_version`` incl. the fail-open evidence freeze, which is local
disk IO only — no LLM / network calls inside any transaction). Lock order is
globally fixed — xproc file lock → threading RLock → ``_LOCK`` →
single-write fcntl in ``_atomic_write_bytes`` — never inverted, so no
deadlock. Granularity matches the thread locks (version vs lineage), so
unrelated artifacts never block each other; single-process deployments just
pay one extra flock per transaction.

Performance (P-2): ``verify_version()`` caches the content digest per
``version_id`` (process-local), stat-validated by mtime_ns + size so on-disk
tampering is still detected; content-write transactions invalidate the entry.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import platform
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

# ── B-1: per-version / per-lineage locks ─────────────────────────────────────
# Every read → validate → write sequence runs as one atomic transaction under
# the lock for its version (or lineage). Lock ordering is fixed and never
# inverted: lineage lock → version lock → _LOCK → digest-cache lock.
# RLock (not Lock) so helpers can be nested safely inside transactions.
_LOCKS_GUARD = threading.Lock()
_VERSION_LOCKS: dict[str, threading.RLock] = {}
_LINEAGE_LOCKS: dict[str, threading.RLock] = {}


def _version_lock(version_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _VERSION_LOCKS.get(version_id)
        if lock is None:
            lock = threading.RLock()
            _VERSION_LOCKS[version_id] = lock
        return lock


def _lineage_lock(lineage_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _LINEAGE_LOCKS.get(lineage_id)
        if lock is None:
            lock = threading.RLock()
            _LINEAGE_LOCKS[lineage_id] = lock
        return lock


# ── A-route: cross-process transaction locks ─────────────────────────────────
# threading.RLock is process-local. In multi-worker / multi-process
# deployments the full read → validate → write transaction must be mutually
# excluded across processes, otherwise:
#   proc A finalize reads pending → enters freeze_evidence (slow);
#   proc B set_version_content reads version.json (still pending) → guard
#   passes on the stale object → writes content.bin back, silently breaking
#   finalized immutability.
# Granularity mirrors the thread locks (per-version vs per-lineage) so
# unrelated artifacts never block each other. Ordering is fixed globally:
# xproc file lock → threading RLock → _LOCK → _atomic_write_bytes' fcntl;
# the xproc lock is never nested on the same path, so no self-deadlock.


@contextlib.contextmanager
def _xproc_file_lock(lock_path: Path):
    """Hold an exclusive cross-process lock for one transaction.

    ``fcntl.flock(LOCK_EX)`` on a sibling lock file; degraded to a no-op on
    non-POSIX platforms (threading locks + atomic writes remain).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass  # 非 POSIX：退化为仅线程锁 + 原子写
        try:
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _version_txn_lock_path(version_id: str) -> Path:
    """Sibling lock file serializing cross-process transactions on one version."""
    return _version_dir(version_id) / ".txn.lock"


def _lineage_txn_lock_path(lineage_id: str) -> Path:
    """Sibling lock file serializing cross-process transactions on one lineage."""
    p = _lineage_path(lineage_id)
    return p.with_name(p.name + ".txn.lock")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically.

    Uses a same-directory temp file + flush/fsync + ``os.replace`` so readers
    never observe a half-written file (B-1: non-atomic ``write_text`` let
    concurrent readers parse truncated JSON).

    An fcntl exclusive lock on a sibling ``.lock`` file serializes writers
    across processes (multi-worker deployments); the in-process threading
    locks remain the fast path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "w") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            # 非 POSIX / 无 fcntl 环境：退化为仅线程锁 + 原子写
            pass
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        finally:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


# ── P-2: per-process content-digest cache ────────────────────────────────────
# verify_version() used to re-read the whole content.bin on every call (the
# review-runs list hit this for every run). Invariant from W4-1: a finalized
# version's content is immutable, so its digest never drifts. Cache entries
# are stat-validated (mtime_ns + size): an entry is only reused when the file
# is provably untouched since it was cached, so on-disk tampering is still
# detected (test_sha256_tamper_detected keeps passing). Any content-write
# transaction explicitly invalidates the entry (staging/pending versions).
_DIGEST_CACHE: dict[str, tuple[str, int, int]] = {}  # version_id -> (digest, mtime_ns, size)
_DIGEST_CACHE_LOCK = threading.Lock()


def _digest_cache_invalidate(version_id: str) -> None:
    with _DIGEST_CACHE_LOCK:
        _DIGEST_CACHE.pop(version_id, None)


def _digest_cache_lookup(version_id: str, content_path: Path) -> str | None:
    with _DIGEST_CACHE_LOCK:
        entry = _DIGEST_CACHE.get(version_id)
    if entry is None:
        return None
    digest, mtime_ns, size = entry
    try:
        st = content_path.stat()
    except OSError:
        return None
    if st.st_mtime_ns == mtime_ns and st.st_size == size:
        return digest
    return None


def _digest_cache_store(version_id: str, content_path: Path, digest: str) -> None:
    try:
        st = content_path.stat()
    except OSError:
        return
    with _DIGEST_CACHE_LOCK:
        _DIGEST_CACHE[version_id] = (digest, st.st_mtime_ns, st.st_size)

STATUS_STAGING = "staging"
STATUS_PENDING = "pending"
STATUS_FINALIZED = "finalized"
_STATUSES = (STATUS_STAGING, STATUS_PENDING, STATUS_FINALIZED)

# Only forward transitions are legal; finalized is terminal.
_ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_STAGING: (STATUS_PENDING,),
    STATUS_PENDING: (STATUS_FINALIZED,),
    STATUS_FINALIZED: (),
}

_SCHEMA = "1"


def artifact_versions_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "artifact_versions_enabled", True))


def _data_root() -> Path:
    return Path("./data").resolve()


def _safe_id(raw: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", raw or "")[:64] or "anon"


def _lineage_path(lineage_id: str) -> Path:
    return _data_root() / "artifacts" / "lineages" / f"{_safe_id(lineage_id)}.json"


def _version_dir(version_id: str) -> Path:
    return _data_root() / "artifacts" / "versions" / _safe_id(version_id)


def _utcnow() -> float:
    return time.time()


@dataclass
class Lineage:
    lineage_id: str
    project_id: str
    name: str
    kind: str
    created_at: float = field(default_factory=_utcnow)
    version_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "lineage_id": self.lineage_id,
            "project_id": self.project_id,
            "name": self.name,
            "kind": self.kind,
            "created_at": self.created_at,
            "version_ids": list(self.version_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Lineage":
        return cls(
            lineage_id=str(data.get("lineage_id", "")),
            project_id=str(data.get("project_id", "")),
            name=str(data.get("name", "")),
            kind=str(data.get("kind", "")),
            created_at=float(data.get("created_at") or 0.0),
            version_ids=[str(v) for v in (data.get("version_ids") or [])],
        )


@dataclass
class Version:
    version_id: str
    lineage_id: str
    based_on_version_id: str | None
    status: str = STATUS_STAGING
    sha256: str = ""  # sha256 of the content snapshot
    content_bytes: int = 0
    created_at: float = field(default_factory=_utcnow)
    actor: str | None = None
    finalized_at: float | None = None
    evidence_frozen: bool = False  # set by W4-2 freeze_evidence hook

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "version_id": self.version_id,
            "lineage_id": self.lineage_id,
            "based_on_version_id": self.based_on_version_id,
            "status": self.status,
            "sha256": self.sha256,
            "content_bytes": self.content_bytes,
            "created_at": self.created_at,
            "actor": self.actor,
            "finalized_at": self.finalized_at,
            "evidence_frozen": self.evidence_frozen,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Version":
        return cls(
            version_id=str(data.get("version_id", "")),
            lineage_id=str(data.get("lineage_id", "")),
            based_on_version_id=data.get("based_on_version_id"),
            status=str(data.get("status", STATUS_STAGING)),
            sha256=str(data.get("sha256", "")),
            content_bytes=int(data.get("content_bytes") or 0),
            created_at=float(data.get("created_at") or 0.0),
            actor=data.get("actor"),
            finalized_at=data.get("finalized_at"),
            evidence_frozen=bool(data.get("evidence_frozen", False)),
        )


def _record_checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _save_lineage(lineage: Lineage) -> None:
    """Persist a lineage record (atomic write). Caller must hold the lineage lock
    when this is part of a larger read-modify-write transaction."""
    path = _lineage_path(lineage.lineage_id)
    payload = lineage.to_dict()
    payload["sha256_record"] = _record_checksum(payload)
    with _LOCK:
        _atomic_write_bytes(
            path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        )


def _load_lineage(lineage_id: str) -> Lineage | None:
    path = _lineage_path(lineage_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    stored = data.pop("sha256_record", None)
    if stored != _record_checksum(data):
        raise ValueError(f"lineage file checksum mismatch: {lineage_id}")
    return Lineage.from_dict(data)


def _save_version(version: Version, content: bytes | None = None) -> None:
    """Persist the version record; optionally rewrite the content snapshot.

    Content may only be (re)written while the version is in ``staging`` —
    finalized versions are immutable.

    B-1: the caller MUST hold the version lock and pass a freshly loaded
    ``Version`` — the status guard below is only sound on a non-stale object.
    Writes are atomic (temp file + fsync + os.replace).
    """
    if content is not None and version.status != STATUS_STAGING:
        raise ValueError(
            f"version {version.version_id} is {version.status}: content is immutable, "
            "create a new version instead"
        )
    vdir = _version_dir(version.version_id)
    vdir.mkdir(parents=True, exist_ok=True)
    if content is not None:
        version.sha256 = _content_sha256(content)
        version.content_bytes = len(content)
        _digest_cache_invalidate(version.version_id)  # P-2: staging/pending write
        with _LOCK:
            _atomic_write_bytes(vdir / "content.bin", content)
    payload = version.to_dict()
    payload["sha256_record"] = _record_checksum(payload)
    with _LOCK:
        _atomic_write_bytes(
            vdir / "version.json",
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        )


def _load_version(version_id: str) -> Version | None:
    vdir = _version_dir(version_id)
    path = vdir / "version.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    stored = data.pop("sha256_record", None)
    if stored != _record_checksum(data):
        raise ValueError(f"version file checksum mismatch: {version_id}")
    return Version.from_dict(data)


def _read_content(version_id: str) -> bytes:
    path = _version_dir(version_id) / "content.bin"
    if not path.is_file():
        raise KeyError(f"content snapshot missing for version {version_id}")
    return path.read_bytes()


# ── public API ──────────────────────────────────────────────────────────────

def create_lineage(project_id: str, name: str, kind: str = "report") -> Lineage:
    """Create a logical artifact file (a lineage that versions attach to)."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id must be a non-empty string")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    lineage = Lineage(
        lineage_id=uuid.uuid4().hex[:16],
        project_id=project_id.strip(),
        name=name.strip(),
        kind=(kind or "report").strip(),
    )
    _save_lineage(lineage)
    logger.info("artifact lineage created: %s (%s)", lineage.lineage_id, lineage.name)
    return lineage


def get_lineage(lineage_id: str) -> Lineage | None:
    return _load_lineage(lineage_id)


def create_version(
    lineage_id: str,
    content: bytes | None,
    *,
    actor: str | None = None,
    based_on_version_id: str | None = None,
) -> Version:
    """Create a new ``staging`` version on a lineage.

    ``based_on_version_id`` must reference an existing version of the same
    lineage (forms the derivation graph). When ``content`` is None, the
    content snapshot is copied from the based-on version (copy-on-write,
    used by W4-4 restore).

    B-1: the version write + lineage ``version_ids`` append run as ONE atomic
    transaction under the lineage lock — concurrent creates can no longer
    lose ``version_ids`` (orphan versions).

    A-route: the whole transaction additionally holds the per-lineage
    cross-process lock, so concurrent creates/restores from *other processes*
    can no longer last-writer-win the ``version_ids`` list.
    """
    with _xproc_file_lock(_lineage_txn_lock_path(lineage_id)):
        with _lineage_lock(lineage_id):
            lineage = _load_lineage(lineage_id)
            if lineage is None:
                raise KeyError(f"lineage not found: {lineage_id}")
            if based_on_version_id is not None:
                base = _load_version(based_on_version_id)
                if base is None:
                    raise KeyError(f"based-on version not found: {based_on_version_id}")
                if base.lineage_id != lineage_id:
                    raise ValueError("based_on_version_id must belong to the same lineage")
                if content is None:
                    content = _read_content(based_on_version_id)
            if content is None:
                content = b""
            version = Version(
                version_id=uuid.uuid4().hex[:16],
                lineage_id=lineage_id,
                based_on_version_id=based_on_version_id,
                status=STATUS_STAGING,
                actor=actor,
            )
            with _version_lock(version.version_id):
                _save_version(version, content=content)
                lineage.version_ids.append(version.version_id)
                _save_lineage(lineage)
            logger.info(
                "artifact version created: %s (lineage %s, based_on=%s)",
                version.version_id, lineage_id, based_on_version_id,
            )
            return version


def get_version(version_id: str) -> Version | None:
    """Load a version; raises ``ValueError`` on record checksum mismatch."""
    return _load_version(version_id)


def get_version_content(version_id: str) -> bytes:
    version = _load_version(version_id)
    if version is None:
        raise KeyError(f"version not found: {version_id}")
    return _read_content(version_id)


def set_version_content(version_id: str, content: bytes) -> Version:
    """Rewrite a version's content snapshot — staging only (immutability guard).

    B-1: load → guard → save is one atomic transaction under the version lock,
    so a concurrent ``submit``/``finalize`` can no longer be rolled back by a
    stale in-memory object.

    A-route: additionally serialized across processes via the per-version
    xproc lock (file lock acquired before the thread lock).
    """
    with _xproc_file_lock(_version_txn_lock_path(version_id)):
        with _version_lock(version_id):
            version = _load_version(version_id)
            if version is None:
                raise KeyError(f"version not found: {version_id}")
            _save_version(version, content=content)  # raises on non-staging (fresh object)
            return version


def _transition(version_id: str, to_status: str) -> Version:
    """Load → validate → mutate → save as one atomic transaction (B-1).

    The transition legality check runs on a freshly loaded record while
    holding the version lock, so a concurrent writer cannot slip a status
    regression between the check and the write.

    A-route: additionally serialized across processes via the per-version
    xproc lock (file lock acquired before the thread lock).
    """
    with _xproc_file_lock(_version_txn_lock_path(version_id)):
        with _version_lock(version_id):
            version = _load_version(version_id)
            if version is None:
                raise KeyError(f"version not found: {version_id}")
            if to_status not in _ALLOWED_TRANSITIONS.get(version.status, ()):
                raise ValueError(
                    f"illegal transition {version.status} → {to_status} for version {version_id}"
                )
            version.status = to_status
            _save_version(version)
            return version


def submit_version(version_id: str) -> Version:
    """staging → pending (submit a version for review)."""
    version = _transition(version_id, STATUS_PENDING)
    logger.info("artifact version submitted: %s", version_id)
    return version


def finalize_version(version_id: str) -> Version:
    """pending → finalized. Immutable afterwards.

    Invokes the W4-2 evidence-freeze hook fail-open: freeze failures are
    logged and never block finalization.

    B-1: the whole transition + freeze + re-save runs under the version lock,
    so a concurrent ``submit``/``set_version_content`` can neither roll the
    status back nor rewrite content after finalization.

    A-route: additionally serialized across processes via the per-version
    xproc lock (file lock acquired before the thread lock). The evidence
    freeze is local disk IO only (manifest JSON, provenance graph, receipt
    write — no LLM / network calls), so holding the lock across it is safe.
    """
    with _xproc_file_lock(_version_txn_lock_path(version_id)):
        with _version_lock(version_id):
            version = _load_version(version_id)
            if version is None:
                raise KeyError(f"version not found: {version_id}")
            if STATUS_FINALIZED not in _ALLOWED_TRANSITIONS.get(version.status, ()):
                raise ValueError(
                    f"illegal transition {version.status} → {STATUS_FINALIZED} "
                    f"for version {version_id}"
                )
            version.status = STATUS_FINALIZED
            version.finalized_at = _utcnow()
            _save_version(version)
            try:
                freeze_evidence(version.version_id, version.to_dict())
                version.evidence_frozen = True
                _save_version(version)
            except Exception:  # fail-open: a freeze failure must never block finalize
                logger.exception("freeze_evidence failed for %s (fail-open)", version_id)
            logger.info("artifact version finalized: %s", version_id)
            return version


def freeze_evidence(version_id: str, version_dict: dict[str, Any]) -> dict[str, Any]:
    """W4-2 (P1-4): freeze version-level evidence snapshots.

    Called by ``finalize_version()`` fail-open: failures are logged and never
    block finalization. Persists ``evidence.json`` under the version directory
    with three payloads:

    * ``manifest_snapshot`` = ``{manifest_json, checksum}`` — the lineage's
      project literature manifest at finalize time; ``checksum`` reuses
      ``literature_manifest.content_hash_manifest`` (Wave B checksum logic).
    * ``evidence_json`` — claim / passage citation list: frozen manifest
      items with ``CitationLocator``-schema locators (W4-6,
      ``domain/citations.py``), plus provenance claim→source edges upstream
      of the lineage.
    * ``execution_snapshot_json`` — run params / environment in
      ``tech_report.write_output_receipt`` format (P1-34).

    Returns the frozen payload (without the integrity-checksum wrapper).
    """
    from . import literature_manifest as lm
    from ..domain.citations import CitationLocator

    lineage_id = str(version_dict.get("lineage_id") or "")
    lineage = _load_lineage(lineage_id)
    if lineage is None:
        raise KeyError(f"lineage not found for version {version_id}: {lineage_id}")
    project_id = lineage.project_id

    # 1) manifest snapshot — reuse Wave B structure + checksum logic.
    manifest = lm.load_manifest(project_id)
    manifest_json = {
        "project_id": manifest.get("project_id"),
        "schema_version": manifest.get("schema_version"),
        "captured_at": manifest.get("captured_at"),
        "items": manifest.get("items") or [],
        "frozen": manifest.get("frozen"),
        "retrievals": manifest.get("retrievals") or [],
        "coverage": manifest.get("coverage"),
    }
    manifest_checksum = lm.content_hash_manifest(manifest)

    # 2) evidence: frozen corpus items (with locators) + provenance claims.
    sources: list[dict[str, Any]] = []
    for item in lm.frozen_items(project_id):
        loc = CitationLocator.from_dict(item.get("locator"))
        sources.append(
            {
                "item_id": str(item.get("id")),
                "doi": item.get("doi"),
                "title": item.get("title"),
                "screening": item.get("screening"),
                "locator": loc.to_dict() if loc is not None else None,
            }
        )
    claims = _freeze_claims(lineage_id)

    # 3) execution snapshot in write_output_receipt format (P1-34).
    execution_snapshot = _execution_snapshot(
        version_id, version_dict, manifest_checksum, len(sources), len(claims)
    )

    payload: dict[str, Any] = {
        "schema": _SCHEMA,
        "version_id": version_id,
        "lineage_id": lineage_id,
        "project_id": project_id,
        "frozen_at": _utcnow(),
        "manifest_snapshot": {
            "manifest_json": manifest_json,
            "checksum": manifest_checksum,
        },
        "evidence_json": {"claims": claims, "sources": sources},
        "execution_snapshot_json": execution_snapshot,
    }
    record = dict(payload)
    record["evidence_checksum"] = _record_checksum(payload)
    vdir = _version_dir(version_id)
    vdir.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _atomic_write_bytes(
            vdir / "evidence.json",
            json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    logger.info(
        "evidence frozen for version %s: %d sources, %d claims, manifest %s",
        version_id, len(sources), len(claims), manifest_checksum[:12],
    )
    return payload


def _freeze_claims(lineage_id: str) -> list[dict[str, Any]]:
    """Claim/passage citation list from provenance edges upstream of a lineage.

    Collects ``claim`` nodes reachable from the ``("artifact", lineage_id)``
    node and their ``cites`` source links. Fail-open internally: returns []
    when provenance is unavailable.
    """
    try:
        from . import provenance as prov
        edges = prov.lineage("artifact", lineage_id, depth=3)
    except Exception:
        logger.warning("_freeze_claims failed (fail-open)", exc_info=True)
        return []
    cites: dict[str, set[str]] = {}
    for e in edges or []:
        if not isinstance(e, dict) or e.get("from_type") != "claim":
            continue
        cid = str(e.get("from_id") or "")
        if not cid:
            continue
        if e.get("to_type") == "source":
            cites.setdefault(cid, set()).add(str(e.get("to_id") or ""))
        else:
            cites.setdefault(cid, set())
    return [
        {"claim_id": cid, "cites": sorted(sids - {""})}
        for cid, sids in sorted(cites.items())
    ]


def _execution_snapshot(
    version_id: str,
    version_dict: dict[str, Any],
    manifest_checksum: str,
    source_count: int,
    claim_count: int,
) -> dict[str, Any]:
    """Run params / environment snapshot in ``write_output_receipt`` format.

    Also appends a sidecar receipt via ``tech_report.write_output_receipt``
    (fail-open); the returned dict is always complete even if that write fails.
    """
    inputs = {
        "version_id": version_id,
        "lineage_id": version_dict.get("lineage_id"),
        "based_on_version_id": version_dict.get("based_on_version_id"),
        "actor": version_dict.get("actor"),
        "content_sha256": version_dict.get("sha256"),
        "content_bytes": version_dict.get("content_bytes"),
    }
    outputs = {
        "manifest_checksum": manifest_checksum,
        "frozen_source_count": source_count,
        "claim_count": claim_count,
    }
    snapshot: dict[str, Any] = {
        "run_kind": "artifact_finalize",
        "run_id": version_id,
        "input_hash": _record_checksum(inputs),
        "inputs_summary": sorted(inputs.keys()),
        "outputs_manifest": [
            {"key": k, "type": type(v).__name__, "preview": str(v)[:120]}
            for k, v in outputs.items()
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }
    try:
        from . import tech_report as tr
        tr.write_output_receipt(
            "artifact_finalize", version_id, inputs=inputs, outputs=outputs
        )
    except Exception:
        logger.warning("output-receipt sidecar failed (fail-open)", exc_info=True)
    return snapshot


def get_evidence(version_id: str) -> dict[str, Any]:
    """Return the frozen evidence record for a version (read-only).

    Raises ``KeyError`` when the version does not exist, ``ValueError`` when
    no evidence was frozen (not finalized, or the fail-open freeze failed).
    """
    version = _load_version(version_id)
    if version is None:
        raise KeyError(f"version not found: {version_id}")
    path = _version_dir(version_id) / "evidence.json"
    if not path.is_file():
        raise ValueError(f"evidence not frozen for version {version_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_evidence(version_id: str) -> dict[str, Any]:
    """Recompute the evidence integrity checksum and compare with the stored one.

    Also compares the live project manifest hash against the frozen snapshot
    hash — informational only, since the live corpus may legitimately evolve
    after the freeze.
    """
    record = get_evidence(version_id)  # KeyError / ValueError propagate
    stored = record.pop("evidence_checksum", None)
    actual = _record_checksum(record)
    ok = stored is not None and stored == actual

    live_checksum: str | None = None
    manifest_unchanged: bool | None = None
    try:
        from . import literature_manifest as lm
        lineage = _load_lineage(str(record.get("lineage_id") or ""))
        if lineage is not None:
            live_checksum = lm.content_hash_manifest(
                lm.load_manifest(lineage.project_id)
            )
            frozen_checksum = (record.get("manifest_snapshot") or {}).get("checksum")
            manifest_unchanged = live_checksum == frozen_checksum
    except Exception:
        logger.warning("verify_evidence: live manifest compare failed", exc_info=True)

    return {
        "version_id": version_id,
        "ok": ok,
        "stored_evidence_checksum": stored,
        "actual_evidence_checksum": actual,
        "evidence_frozen": True,
        "manifest_snapshot_checksum": (
            (record.get("manifest_snapshot") or {}).get("checksum")
        ),
        "manifest_live_checksum": live_checksum,
        "manifest_unchanged": manifest_unchanged,
    }


def list_versions(lineage_id: str) -> dict[str, Any]:
    """All versions of a lineage plus the basedOnVersionId derivation graph."""
    lineage = _load_lineage(lineage_id)
    if lineage is None:
        raise KeyError(f"lineage not found: {lineage_id}")
    versions: list[dict[str, Any]] = []
    graph: list[dict[str, Any]] = []
    for vid in lineage.version_ids:
        try:
            v = _load_version(vid)
        except ValueError:
            logger.warning("skipping version with checksum mismatch: %s", vid)
            continue
        if v is None:
            continue
        versions.append(v.to_dict())
        graph.append({"version_id": v.version_id, "based_on_version_id": v.based_on_version_id})
    versions.sort(key=lambda d: d["created_at"])
    return {"lineage": lineage.to_dict(), "versions": versions, "graph": graph}


def verify_version(version_id: str) -> dict[str, Any]:
    """Recompute the content sha256 and compare with the stored snapshot hash.

    Detects tampering of ``content.bin`` after the fact.

    P-2: the content digest is cached per ``version_id`` (process-local). A
    cache entry is only reused when the file's mtime_ns + size are unchanged
    since it was cached, so on-disk tampering still invalidates it and is
    detected. Content-write transactions invalidate the entry explicitly.
    """
    version = _load_version(version_id)
    if version is None:
        raise KeyError(f"version not found: {version_id}")
    content_path = _version_dir(version_id) / "content.bin"
    actual = _digest_cache_lookup(version_id, content_path)
    if actual is None:
        actual = _content_sha256(_read_content(version_id))
        _digest_cache_store(version_id, content_path, actual)
    ok = actual == version.sha256
    return {
        "version_id": version_id,
        "ok": ok,
        "stored_sha256": version.sha256,
        "actual_sha256": actual,
        "content_bytes": version.content_bytes,
        "status": version.status,
    }
