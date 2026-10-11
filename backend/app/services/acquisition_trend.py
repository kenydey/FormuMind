"""P2-7: 下载成功率趋势告警 —— 运营可观测性。

设计：
- 每次调用 snapshot() 记录当前 acquisition 分布 + 成功率。
- 快照存内存（最近 100 个），轻量无 DB 压力。
- trend() 计算近期 vs 基期的成功率变化，跌幅超阈值则告警。
- 告警经 /api/kb/acquisition-trend 暴露，Cheng 在运营面板查看。

成功率定义：pdf/html/(pdf+html+none)，none=下载失败。
"""
from __future__ import annotations

import logging
import time
from collections import deque

logger = logging.getLogger(__name__)

_MAX_SNAPSHOTS = 100
_ALERT_DROP_PCT = 15.0  # 成功率跌幅超 15% 告警

_snapshots: deque = deque(maxlen=_MAX_SNAPSHOTS)


def _success_rate(stats: dict[str, int]) -> float:
    ok = stats.get("pdf", 0) + stats.get("html", 0)
    total = ok + stats.get("none", 0) + stats.get("text", 0)
    return ok / total if total else 1.0


def snapshot(project_id: str | None = None) -> dict:
    """记录一次 acquisition 快照。返回快照 dict。fail-open。"""
    try:
        from ..db.source_store import get_source_store
        stats = get_source_store().acquisition_stats(project_id=project_id)
        snap = {
            "ts": time.time(),
            "project_id": project_id,
            "stats": dict(stats),
            "success_rate": round(_success_rate(stats), 3),
            "total": sum(stats.values()),
        }
        _snapshots.append(snap)
        # D-3: 持久化到 DB（fail-open）
        try:
            import json
            import uuid
            from ..db.database import default_session_factory
            from ..db.models import AcquisitionSnapshot
            with default_session_factory()() as session:
                session.add(AcquisitionSnapshot(
                    id=uuid.uuid4().hex,
                    ts=snap["ts"],
                    project_id=project_id,
                    stats_json=json.dumps(dict(stats)),
                    success_rate=snap["success_rate"],
                ))
                session.commit()
        except Exception:
            pass
        return snap
    except Exception as exc:
        logger.debug("acquisition snapshot failed: %s", exc)
        return {}


def trend(window: int = 10) -> dict:
    """近期 window 个快照 vs 更早期的成功率趋势。

    返回 {"trend": "up"|"down"|"stable", "recent_rate": float,
           "baseline_rate": float, "drop_pct": float, "alert": bool}
    """
    snaps = [s for s in _snapshots if s.get("success_rate") is not None]
    if len(snaps) < 2:
        return {"trend": "stable", "alert": False, "reason": "insufficient data"}
    recent = snaps[-window:]
    baseline = snaps[:-window] or snaps[:1]
    recent_rate = sum(s["success_rate"] for s in recent) / len(recent)
    baseline_rate = sum(s["success_rate"] for s in baseline) / len(baseline)
    drop_pct = (baseline_rate - recent_rate) / baseline_rate * 100 if baseline_rate else 0.0
    alert = drop_pct > _ALERT_DROP_PCT
    trend_dir = "down" if drop_pct > 5 else ("up" if drop_pct < -5 else "stable")
    if alert:
        logger.warning(
            "acquisition success rate dropped %.1f%% (%.3f → %.3f)",
            drop_pct, baseline_rate, recent_rate,
        )
    return {
        "trend": trend_dir,
        "recent_rate": round(recent_rate, 3),
        "baseline_rate": round(baseline_rate, 3),
        "drop_pct": round(drop_pct, 1),
        "alert": alert,
        "snapshots": len(snaps),
    }


def clear() -> None:
    """测试用：清空快照。"""
    _snapshots.clear()
