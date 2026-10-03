"""P1 #20: disk-backed surrogate model artifacts (joblib) with version rollback.

Layout::

    {model_artifacts_dir}/
      {safe_project_id}/
        {metric}/
          current.json          # {"version_id", "pinned", "data_hash", "feature_version", ...}
          {version_id}.joblib   # {"model", "info": ModelInfo dict}
          {version_id}.meta.json  # the info fields a version list needs, without loading the model

Restores only when ``data_hash`` + ``feature_version`` match the current
training set — otherwise callers retrain and write a new version.

``pinned`` in ``current.json`` is set by a rollback: the chosen version stays the
serving model across retrains and restarts (new versions are archived next to it
without taking over) until it is explicitly released.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_segment(value: str) -> str:
    text = (value or "default").strip() or "default"
    return _SAFE.sub("_", text)[:120]


def artifacts_root(settings=None) -> Path:
    from ..config import get_settings

    settings = settings or get_settings()
    root = Path(getattr(settings, "model_artifacts_dir", "./data/models") or "./data/models")
    root.mkdir(parents=True, exist_ok=True)
    return root


def metric_dir(project_id: str, metric: str, *, settings=None) -> Path:
    d = artifacts_root(settings) / safe_segment(project_id) / safe_segment(metric)
    d.mkdir(parents=True, exist_ok=True)
    return d


def compute_data_hash(rows: list[Any], metric: str) -> str:
    """Stable hash of the training rows that feed one (project, metric) model."""
    h = hashlib.sha256()
    h.update(metric.encode("utf-8"))
    for rec in rows:
        factors = getattr(rec, "factors", {}) or {}
        measured = getattr(rec, "measured", {}) or {}
        payload = {
            "label": getattr(rec, "label", "") or "",
            "project_id": getattr(rec, "project_id", "") or "",
            "cure": getattr(rec, "cure_temperature_c", None),
            "factors": {str(k): factors[k] for k in sorted(factors, key=str)},
            "y": measured.get(metric),
        }
        h.update(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()[:16]


def _current_path(project_id: str, metric: str, *, settings=None) -> Path:
    return metric_dir(project_id, metric, settings=settings) / "current.json"


def read_current(project_id: str, metric: str, *, settings=None) -> dict | None:
    path = _current_path(project_id, metric, settings=settings)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("model current.json unreadable (%s/%s): %s", project_id, metric, exc)
        return None


_META_FIELDS = (
    "trained_at", "n_samples", "r2", "cv_r2", "rmse", "backend", "data_hash", "feature_version",
)


def _meta_path(directory: Path, version_id: str) -> Path:
    return directory / f"{version_id}.meta.json"


def _read_meta(directory: Path, version_id: str) -> dict:
    path = _meta_path(directory, version_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def list_versions(project_id: str, metric: str, *, settings=None) -> list[dict]:
    """Newest-first version metadata for one metric.

    Each row carries ``version_id``, ``is_current`` and, when known, the fields in
    ``_META_FIELDS`` (read from a small sidecar written at save time, so listing never
    has to unpickle a model). The current row also says whether it is ``pinned``.
    """
    d = metric_dir(project_id, metric, settings=settings)
    current = read_current(project_id, metric, settings=settings) or {}
    current_id = current.get("version_id")
    out: list[dict] = []
    for path in sorted(d.glob("*.joblib"), key=lambda p: p.stat().st_mtime, reverse=True):
        vid = path.stem
        meta = {"version_id": vid, "path": str(path), "is_current": vid == current_id, "pinned": False}
        meta.update(_read_meta(d, vid))
        if vid == current_id:
            # current.json is authoritative for the serving version (and covers artifacts
            # written before sidecars existed).
            meta.update({k: current.get(k) for k in _META_FIELDS if current.get(k) is not None})
            meta["pinned"] = bool(current.get("pinned"))
        out.append(meta)
    return out


def newest_version_id(project_id: str, metric: str, *, settings=None) -> str | None:
    """The most recently written artifact, whether or not it is current."""
    versions = list_versions(project_id, metric, settings=settings)
    return versions[0]["version_id"] if versions else None


def pinned_version_id(project_id: str, metric: str, *, settings=None) -> str | None:
    """The version a rollback pinned, or None."""
    current = read_current(project_id, metric, settings=settings) or {}
    return str(current["version_id"]) if current.get("pinned") and current.get("version_id") else None


def has_version_for_data(project_id: str, metric: str, data_hash: str, *, settings=None) -> bool:
    """True when an archived version was trained on exactly this data (its id ends in the hash)."""
    suffix = f"_{safe_segment(data_hash)}"
    return any(v["version_id"].endswith(suffix) for v in list_versions(project_id, metric, settings=settings))


def save_model(
    project_id: str,
    metric: str,
    model: Any,
    info_dict: dict,
    *,
    make_current: bool = True,
    settings=None,
) -> str:
    """Persist model + info. Returns version_id.

    ``make_current=False`` archives the version next to the serving one without taking
    over — used while a rollback is pinned.
    """
    trained_at = info_dict.get("trained_at") or _utcnow_iso()
    data_hash = info_dict.get("data_hash") or "unknown"
    version_id = info_dict.get("version_id") or f"{trained_at.replace(':', '').replace('+', 'p')}_{data_hash}"
    version_id = safe_segment(version_id)
    info_dict = {**info_dict, "trained_at": trained_at, "version_id": version_id}

    d = metric_dir(project_id, metric, settings=settings)
    artifact = d / f"{version_id}.joblib"
    _dump_artifact(artifact, {"model": model, "info": info_dict})
    try:
        _meta_path(d, version_id).write_text(
            json.dumps({k: info_dict.get(k) for k in _META_FIELDS}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # the list is a convenience; the artifact is what matters
        logger.warning("model meta sidecar not written (%s/%s): %s", project_id, metric, exc)
    if make_current:
        _write_pointer(project_id, metric, version_id, info_dict, pinned=False, settings=settings)
    return version_id


def _write_pointer(
    project_id: str, metric: str, version_id: str, info: dict, *, pinned: bool, settings=None
) -> None:
    pointer = {
        "version_id": version_id,
        "pinned": bool(pinned),
        "data_hash": info.get("data_hash"),
        "feature_version": info.get("feature_version"),
        "trained_at": info.get("trained_at"),
        "backend": info.get("backend"),
        "n_samples": info.get("n_samples"),
        "r2": info.get("r2"),
        "cv_r2": info.get("cv_r2"),
        "rmse": info.get("rmse"),
    }
    _current_path(project_id, metric, settings=settings).write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _dump_artifact(path: Path, payload: dict) -> None:
    try:
        import joblib

        joblib.dump(payload, path)
        return
    except Exception:
        pass
    import pickle

    path.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))


def _load_artifact(path: Path) -> dict | None:
    try:
        import joblib

        payload = joblib.load(path)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    try:
        import pickle

        payload = pickle.loads(path.read_bytes())
        if isinstance(payload, dict):
            return payload
    except Exception as exc:
        logger.warning("model artifact load failed %s: %s", path, exc)
    return None


def load_model(
    project_id: str,
    metric: str,
    *,
    data_hash: str | None = None,
    feature_version: str | None = None,
    version_id: str | None = None,
    settings=None,
) -> tuple[Any, dict] | None:
    """Load artifact if hash/feature_version match (or explicit version_id)."""
    d = metric_dir(project_id, metric, settings=settings)
    if version_id:
        path = d / f"{safe_segment(version_id)}.joblib"
        if not path.is_file():
            return None
    else:
        cur = read_current(project_id, metric, settings=settings)
        if not cur or not cur.get("version_id"):
            return None
        if data_hash and cur.get("data_hash") != data_hash:
            return None
        if feature_version and cur.get("feature_version") != feature_version:
            return None
        path = d / f"{safe_segment(str(cur['version_id']))}.joblib"
        if not path.is_file():
            return None

    payload = _load_artifact(path)
    if not payload or "model" not in payload:
        return None
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    if version_id is None:
        if data_hash and info.get("data_hash") and info.get("data_hash") != data_hash:
            return None
        if feature_version and info.get("feature_version") and info.get("feature_version") != feature_version:
            return None
    return payload["model"], info


def set_current_version(
    project_id: str, metric: str, version_id: str, *, pinned: bool = False, settings=None
) -> bool:
    """Point current.json at an existing artifact (rollback). ``pinned`` keeps it there
    across retrains and restarts until released."""
    loaded = load_model(project_id, metric, version_id=version_id, settings=settings)
    if loaded is None:
        return False
    _, info = loaded
    _write_pointer(project_id, metric, version_id, info, pinned=pinned, settings=settings)
    return True


def release_pin(project_id: str, metric: str, *, settings=None) -> bool:
    """Clear the pin flag without changing which version is current. False when nothing was pinned."""
    current = read_current(project_id, metric, settings=settings)
    if not current or not current.get("pinned"):
        return False
    current["pinned"] = False
    _current_path(project_id, metric, settings=settings).write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True
