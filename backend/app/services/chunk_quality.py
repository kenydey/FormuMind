"""P2-8: chunk 质量抽检 —— 定期采样 LLM 评分，入库质量持续监控。

设计：
- sample_chunks(n): 随机采样 n 个 chunk。
- score_chunk(chunk): LLM 按 4 维度打分（完整性/准确性/结构化/噪声）。
- 抽检经 Celery beat 定期触发（默认每天 1 次，采样 20 个），或手动调 API。
- 评分结果存内存 + 日志，不阻塞入库（fail-open）。
- LLM 成本：20 chunk × ~500 token ≈ 1万 token/天，可忽略。

评分维度（1-5 分）：
- completeness: chunk 语义完整（非截断/碎片）
- accuracy: 无明显解析错误（乱码/错位）
- structure: 表格/标题结构保留
- noise: 无视觉占位符/广告噪声
"""
from __future__ import annotations

import json
import logging
import random
import time

logger = logging.getLogger(__name__)

_SCORE_DIMENSIONS = ("completeness", "accuracy", "structure", "noise")
_last_report: dict = {}


def sample_chunks(n: int = 20) -> list[dict]:
    """随机采样 n 个 chunk。fail-open。"""
    try:
        from ..db.database import default_session_factory
        from ..db.models import DocumentChunk
        with default_session_factory()() as session:
            total = session.query(DocumentChunk).count()
            if total == 0:
                return []
            # 随机偏移采样
            offsets = random.sample(range(total), min(n, total))
            out = []
            for off in offsets:
                row = session.query(DocumentChunk).offset(off).first()
                if row:
                    out.append({
                        "id": row.id, "source_id": row.source_id,
                        "text": (row.text or "")[:2000],
                        "page_no": row.page_no,
                    })
            return out
    except Exception as exc:
        logger.debug("sample_chunks failed: %s", exc)
        return []


def score_chunk(chunk: dict) -> dict:
    """LLM 给单个 chunk 打分。返回 {dimension: 1-5} + overall。失败返回 {}。"""
    text = (chunk.get("text") or "").strip()
    if len(text) < 50:
        return {}
    try:
        from ..config import get_settings
        from .llm import _complete_openai_compatible
        s = get_settings()
        api_key = s.deepseek_api_key or s.openai_api_key
        if not api_key:
            return {}
        model = s.deepseek_model or "deepseek-chat"
        prompt = (
            "你是文档解析质量评审员。给以下知识库文本块按 4 个维度打分（1-5分，5最优），"
            "只返回 JSON：{\"completeness\": N, \"accuracy\": N, \"structure\": N, \"noise\": N}。\n"
            "completeness: 语义完整，非截断碎片；accuracy: 无乱码/错位/解析错误；"
            "structure: 表格标题结构保留；noise: 无占位符/广告噪声。\n\n"
            f"文本块：\n{text[:1500]}"
        )
        out = _complete_openai_compatible(
            prompt, api_key, model, max_tokens=200,
            base_url=s.deepseek_base_url,
        )
        if not out:
            return {}
        import re
        m = re.search(r"\{.*\}", out, re.DOTALL)
        if not m:
            return {}
        scores = json.loads(m.group(0))
        # 校验范围
        cleaned = {}
        for d in _SCORE_DIMENSIONS:
            v = scores.get(d)
            if isinstance(v, (int, float)) and 1 <= v <= 5:
                cleaned[d] = float(v)
        if len(cleaned) == 4:
            cleaned["overall"] = round(sum(cleaned.values()) / 4, 2)
            return cleaned
        return {}
    except Exception as exc:
        logger.debug("score_chunk failed: %s", exc)
        return {}


def run_quality_sample(n: int = 20) -> dict:
    """执行一次抽检。返回报告 dict。"""
    global _last_report
    chunks = sample_chunks(n)
    if not chunks:
        return {"sampled": 0, "reason": "no chunks"}
    scores = []
    for c in chunks:
        s = score_chunk(c)
        if s:
            scores.append(s)
    if not scores:
        return {"sampled": len(chunks), "scored": 0, "reason": "LLM unavailable"}
    avg = {d: round(sum(s[d] for s in scores) / len(scores), 2)
           for d in _SCORE_DIMENSIONS}
    avg["overall"] = round(sum(s["overall"] for s in scores) / len(scores), 2)
    report = {
        "ts": time.time(),
        "sampled": len(chunks),
        "scored": len(scores),
        "average": avg,
        "alert": avg["overall"] < 3.5,  # 均分低于 3.5 告警
    }
    _last_report = report
    if report["alert"]:
        logger.warning("chunk quality below threshold: %.2f", avg["overall"])
    else:
        logger.info("chunk quality sample: overall=%.2f n=%d", avg["overall"], len(scores))
    return report


def last_report() -> dict:
    """上次抽检报告。"""
    return _last_report
