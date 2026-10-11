"""S-2: 检索阈值自适应。

基于历史查询的 precision/recall 反馈动态调整检索阈值。
数据不足时使用默认值（fail-open）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


# 默认阈值
DEFAULT_THRESHOLDS = {
    "hybrid_alpha": 0.5,      # BM25 vs 向量权重
    "min_score": 0.1,         # 最低相关性分数
    "rerank_top_k": 20,       # rerank 候选数
}

# 历史数据文件
_HISTORY_FILE = Path.home() / ".formumind" / "threshold_history.jsonl"


def _load_history(days: int = 30) -> list[dict]:
    """加载最近 N 天的历史反馈。"""
    try:
        if not _HISTORY_FILE.exists():
            return []
        cutoff = time.time() - days * 86400
        records = []
        with open(_HISTORY_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("ts", 0) >= cutoff:
                        records.append(r)
                except Exception:
                    continue
        return records
    except Exception:
        return []


def record_feedback(query: str, precision: float, recall: float, threshold_used: dict) -> None:
    """记录一次查询的反馈（供后续自适应）。"""
    try:
        import fcntl
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "query": query[:100],
            "precision": precision,
            "recall": recall,
            "threshold": threshold_used,
        }
        with open(_HISTORY_FILE, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(record) + "\n")
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


def adapt_thresholds() -> dict:
    """S-2: 基于历史数据自适应阈值。

    规则：
    - precision < 0.7 且 recall > 0.8：提高 min_score（宁缺毋滥）
    - recall < 0.7 且 precision > 0.8：降低 min_score（宁滥毋缺）
    - 数据不足（<10 条）时返回默认值
    """
    try:
        history = _load_history()
        if len(history) < 10:
            return dict(DEFAULT_THRESHOLDS)

        avg_p = sum(r["precision"] for r in history) / len(history)
        avg_r = sum(r["recall"] for r in history) / len(history)

        thresholds = dict(DEFAULT_THRESHOLDS)
        if avg_p < 0.7 and avg_r > 0.8:
            thresholds["min_score"] = min(0.3, thresholds["min_score"] + 0.05)
        elif avg_r < 0.7 and avg_p > 0.8:
            thresholds["min_score"] = max(0.05, thresholds["min_score"] - 0.05)

        return thresholds
    except Exception:
        return dict(DEFAULT_THRESHOLDS)
