"""O-1: N-1/N-4 数据追踪基础设施。

记录每次推荐的：
- prompt 版本（v1/v2）
- 是否启用共识（N-1）
- 接地通过率

2 周后基于数据决定是否默认开启。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


_TRACK_FILE = Path.home() / ".formumind" / "recommend_ab_tracking.jsonl"


def track_recommendation(
    recommend_id: str,
    prompt_version: str,
    consensus_enabled: bool,
    grounding_rate: float,
    n_candidates: int,
) -> None:
    """记录一次推荐的 A/B 数据。fail-open。"""
    try:
        _TRACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "recommend_id": recommend_id,
            "prompt_version": prompt_version,
            "consensus_enabled": consensus_enabled,
            "grounding_rate": grounding_rate,
            "n_candidates": n_candidates,
        }
        with open(_TRACK_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def get_ab_stats(days: int = 14) -> dict:
    """获取 A/B 统计（最近 N 天）。

    返回各版本的平均接地通过率，用于决策是否默认开启。
    """
    try:
        if not _TRACK_FILE.exists():
            return {"sufficient_data": False, "n": 0}

        cutoff = time.time() - days * 86400
        v1_rates = []
        v2_rates = []
        consensus_rates = []
        no_consensus_rates = []

        with open(_TRACK_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("ts", 0) < cutoff:
                        continue
                    rate = r.get("grounding_rate", 0)
                    if r.get("prompt_version") == "v1":
                        v1_rates.append(rate)
                    elif r.get("prompt_version") == "v2":
                        v2_rates.append(rate)
                    if r.get("consensus_enabled"):
                        consensus_rates.append(rate)
                    else:
                        no_consensus_rates.append(rate)
                except Exception:
                    continue

        n_total = len(v1_rates) + len(v2_rates)
        return {
            "sufficient_data": n_total >= 50,  # 至少 50 条才可信
            "n": n_total,
            "v1_avg": sum(v1_rates) / len(v1_rates) if v1_rates else 0,
            "v2_avg": sum(v2_rates) / len(v2_rates) if v2_rates else 0,
            "consensus_avg": sum(consensus_rates) / len(consensus_rates) if consensus_rates else 0,
            "no_consensus_avg": sum(no_consensus_rates) / len(no_consensus_rates) if no_consensus_rates else 0,
        }
    except Exception:
        return {"sufficient_data": False, "n": 0}
