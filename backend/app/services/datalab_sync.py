"""C-5: Datalab ELN -> measurement_store sync (real-measurement ingest path).

What this module is: the CODE path that pulls lab measurements out of a
Datalab item and lands them in :class:`MeasurementStore`, so the optimizer
can consume real measurements instead of predictor scores.

Data protocol: FormuMind writes measurements to Datalab as a comment block
with ``block_id == MEASUREMENT_BLOCK_ID`` (see ``datalab_block``), whose
``data`` is ``{"experiment_id": int, "measurements": [{metric, value, unit,
test_method, ...}, ...]}``. This module reads that block back.

Status honesty (2026-10-01): the sync logic is implemented and mock-tested
(httpx.MockTransport). End-to-end validation against a REAL Datalab instance
is BLOCKED — no reachable URL in this environment (localhost:5001 refused,
no Docker). ``validated`` stays None until a real run; see
``backend/scripts/verify_datalab.py``.
"""
from __future__ import annotations

import logging
from typing import Any

from ..db.datalab_client import datalab_headers, parse_item_envelope
from ..db.measurement_store import MeasurementStore
from ..db.models import ExperimentRow
from ..db.session_utils import commit_session
from ..domain.schemas import Measurement
from .http_safe import make_client

logger = logging.getLogger(__name__)

#: blocks_obj entry id FormuMind uses for lab measurements in Datalab.
MEASUREMENT_BLOCK_ID = "formumind_measurements"


def fetch_item_data(
    api_url: str,
    item_id: str,
    *,
    timeout: float = 15.0,
    _transport: Any = None,
) -> dict[str, Any] | None:
    """GET a Datalab item's data envelope. None on any failure (fail-open).

    Endpoint path follows the Datalab headless ``/items/<id>/`` convention
    used by the sibling read APIs; the exact path is confirmed on the first
    real run (see verify_datalab.py).
    """
    url = (api_url or "").rstrip("/")
    if not url or not item_id:
        return None
    try:
        with make_client(
            base_url=url, timeout=timeout, headers=datalab_headers(), transport=_transport
        ) as client:
            resp = client.get(f"/items/{item_id}/")
            if resp.status_code != 200:
                logger.warning("datalab fetch item %s: HTTP %s", item_id, resp.status_code)
                return None
            return parse_item_envelope(resp.json())
    except Exception as exc:  # noqa: BLE001
        logger.warning("datalab fetch item %s failed: %s", item_id, exc)
        return None


def extract_measurements(item_data: dict[str, Any]) -> list[Measurement]:
    """Parse the measurement block out of a Datalab item envelope.

    Returns [] when the block is missing or malformed (fail-open: a sync
    with nothing to sync is not an error). Never raises.
    """
    try:
        blocks = item_data.get("blocks_obj")
        if not isinstance(blocks, dict):
            return []
        block = blocks.get(MEASUREMENT_BLOCK_ID)
        if not isinstance(block, dict):
            return []
        data = block.get("data")
        if not isinstance(data, dict):
            return []
        raw_list = data.get("measurements")
        if not isinstance(raw_list, list):
            return []
        out: list[Measurement] = []
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            try:
                out.append(Measurement.model_validate(raw))
            except Exception:  # noqa: BLE001
                logger.debug("datalab sync: skipping malformed measurement %r", raw)
        return out
    except Exception:  # noqa: BLE001
        logger.debug("datalab sync: extract_measurements failed", exc_info=True)
        return []


def apply_lab_measurements(
    experiment_id: int,
    measurements: list[Measurement],
    *,
    session_factory: Any = None,
) -> dict[str, Any]:
    """P0-2: store -> optimizer 链路。写入明细并回写父表 measured。

    ``MeasurementStore.add_in()`` 只插 ``measurements`` 明细表, 但训练
    registry (``SqlExperimentStore.all()``) 只读 ``experiments.measured``
    JSON —— 不回写, 同步来的 lab 数据对 ``load_prior_measurements()`` 和
    优化器永远不可见。这是 ELN -> store -> 优化器链路断掉的第二环。

    同一事务内: ① 写明细行; ② merge ``{metric: value}`` 进父
    ``ExperimentRow.measured`` (新值覆盖同名旧值)。Fail-open: 永不抛异常,
    返回 ``{"written": int, "mirrored": bool, "errors": [...]}``。
    """
    from ..db import database as _database

    report: dict[str, Any] = {"written": 0, "mirrored": False, "errors": []}
    try:
        factory = session_factory or _database.default_session_factory()
        store = MeasurementStore(factory)
        with commit_session(factory) as session:
            n = store.add_in(session, experiment_id, measurements)
            row = session.get(ExperimentRow, experiment_id)
            if row is None:
                report["errors"].append(
                    f"experiment {experiment_id} not found; "
                    f"{n} measurement rows stored without parent mirror"
                )
            else:
                merged = dict(row.measured or {})
                for m in measurements:
                    merged[m.metric] = float(m.value)
                row.measured = merged  # 赋新 dict, SQLAlchemy 可检测到变更
                report["mirrored"] = True
            report["written"] = int(n)
        return report
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_lab_measurements failed for experiment %s: %s", experiment_id, exc)
        report["errors"].append(str(exc))
        return report


def sync_item_data_to_store(
    item_data: dict[str, Any] | None,
    experiment_id: int,
    *,
    session_factory: Any = None,
    item_id: str = "",
) -> dict[str, Any]:
    """Core of the sync: Datalab envelope -> store + optimizer-visible mirror.

    ``item_data`` is the parsed envelope from :func:`fetch_item_data` (None
    when Datalab is unreachable). Never raises.
    """
    report: dict[str, Any] = {
        "synced": 0,
        "skipped": 0,
        "errors": [],
        "validated": None,
        "mirrored": False,
    }
    try:
        if item_data is None:
            report["errors"].append(f"datalab item {item_id} unreachable or invalid")
            return report
        measurements = extract_measurements(item_data)
        if not measurements:
            report["skipped"] += 1
            return report
        applied = apply_lab_measurements(
            experiment_id, measurements, session_factory=session_factory
        )
        report["synced"] = applied["written"]
        report["mirrored"] = applied["mirrored"]
        report["errors"].extend(applied["errors"])
        return report
    except Exception as exc:  # noqa: BLE001
        logger.warning("datalab sync failed for item %s: %s", item_id, exc)
        report["errors"].append(str(exc))
        return report


def block_experiment_id(item_data: dict[str, Any] | None) -> Any:
    """Return the ``experiment_id`` the measurement block claims, if any.

    Used by the manual sync endpoint to verify the caller's Datalab-item <->
    experiment binding before writing. Never raises.
    """
    try:
        blocks = (item_data or {}).get("blocks_obj")
        if not isinstance(blocks, dict):
            return None
        block = blocks.get(MEASUREMENT_BLOCK_ID)
        if not isinstance(block, dict):
            return None
        data = block.get("data")
        if not isinstance(data, dict):
            return None
        return data.get("experiment_id")
    except Exception:  # noqa: BLE001
        return None


def sync_datalab_to_store(
    api_url: str,
    item_id: str,
    experiment_id: int,
    *,
    session_factory: Any = None,
    timeout: float = 15.0,
    _transport: Any = None,
) -> dict[str, Any]:
    """Pull measurements for one Datalab item into the measurement store.

    Returns a report dict: {"synced": int, "skipped": int, "errors": [...],
    "validated": None, "mirrored": bool}. ``validated`` is always None here —
    it flips only on a real end-to-end run (see verify_datalab.py). Never raises.
    """
    item_data = fetch_item_data(api_url, item_id, timeout=timeout, _transport=_transport)
    return sync_item_data_to_store(
        item_data, experiment_id, session_factory=session_factory, item_id=item_id
    )
