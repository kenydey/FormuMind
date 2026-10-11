"""迁移学习：利用历史实验数据热启动优化。

相似项目的历史最优解作为新项目优化的初始点。
"""
from __future__ import annotations


def find_similar_history(
    requirement: dict,
    history_db: list[dict],
    top_k: int = 3,
) -> list[dict]:
    """查找相似历史项目。

    基于目标指标和约束的相似度。
    fail-open：异常时返回空列表。
    """
    try:
        if not history_db:
            return []
        # 简单相似度：目标指标交集大小
        req_metrics = set(requirement.get("objectives", []))
        scored = []
        for h in history_db:
            h_metrics = set(h.get("objectives", []))
            overlap = len(req_metrics & h_metrics)
            if overlap > 0:
                scored.append((overlap, h))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [h for _, h in scored[:top_k]]
    except Exception:
        return []


def warm_start_points(
    similar_history: list[dict],
    factors: list,
) -> list[list[float]]:
    """从相似历史生成热启动初始点。

    返回历史最优解的因子值列表。
    """
    try:
        points = []
        for h in similar_history:
            best = h.get("best_point")
            if best and isinstance(best, list):
                # 确保维度匹配
                if len(best) == len(factors):
                    points.append(best)
        return points
    except Exception:
        return []
