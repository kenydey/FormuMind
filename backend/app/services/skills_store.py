"""Persist skill enable/pin preferences under ``data/skills_prefs.json``."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DEFAULT: dict[str, Any] = {
    "disabled_ids": [],
    "pinned_ids": [],
    "disabled_connector_ids": [],
    "evidence_mode_default": False,
    "mcp_servers": [],
}


def _prefs_path() -> Path:
    return Path("./data").resolve() / "skills_prefs.json"


def load_prefs() -> dict[str, Any]:
    path = _prefs_path()
    if not path.is_file():
        return dict(_DEFAULT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(_DEFAULT)
        out = dict(_DEFAULT)
        out.update(data)
        out["disabled_ids"] = list(out.get("disabled_ids") or [])
        out["pinned_ids"] = list(out.get("pinned_ids") or [])
        out["disabled_connector_ids"] = list(out.get("disabled_connector_ids") or [])
        out["mcp_servers"] = list(out.get("mcp_servers") or [])
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("skills_prefs load failed: %s", exc)
        return dict(_DEFAULT)


def save_prefs(patch: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        cur = load_prefs()
        if "disabled_ids" in patch and patch["disabled_ids"] is not None:
            cur["disabled_ids"] = sorted({str(x) for x in patch["disabled_ids"]})
        if "pinned_ids" in patch and patch["pinned_ids"] is not None:
            cur["pinned_ids"] = [str(x) for x in patch["pinned_ids"]]
        if "evidence_mode_default" in patch and patch["evidence_mode_default"] is not None:
            cur["evidence_mode_default"] = bool(patch["evidence_mode_default"])
        if "disabled_connector_ids" in patch and patch["disabled_connector_ids"] is not None:
            cur["disabled_connector_ids"] = sorted({str(x) for x in patch["disabled_connector_ids"]})
        if "mcp_servers" in patch and patch["mcp_servers"] is not None:
            cur["mcp_servers"] = list(patch["mcp_servers"])
        path = _prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        return cur


def is_enabled(skill_id: str, *, activation_policy: str = "user-controlled") -> bool:
    if activation_policy == "always-on":
        return True
    prefs = load_prefs()
    return skill_id not in set(prefs.get("disabled_ids") or [])
