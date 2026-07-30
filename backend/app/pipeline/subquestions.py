"""Break a research topic into sub-questions, so retrieval has more than one angle.

Deep research used to run exactly one retrieval against the topic string. The
grading and claim-checking around it were real, but a single query can only find
what that query's wording reaches — everything the topic implies but does not say
was simply never searched for. ``QueryExpander`` does not close that gap: it
produces synonyms and IPC codes for the *same* question, and only inside the
conditional ``fallback`` branch, so a run that retrieves successfully never
expands anything at all.

Decomposition is what produces genuinely new angles. "水性环氧防腐涂料配方" becomes
questions about resin systems, curing agents, pigment loading and salt-spray
performance — each retrieving different documents, merged into one pool.

Offline, this degrades to the topic alone: one question, identical behaviour to
before. It never invents sub-questions from a template, because a made-up angle
would spend a retrieval round on a question nobody asked.
"""
from __future__ import annotations

import logging

from ..domain.schemas import Evidence

logger = logging.getLogger(__name__)

_MAX = 8


def _prompt(topic: str, n: int, domain: str = "") -> str:
    ctx = f"（产品领域：{domain}）" if domain else ""
    return (
        f"你是材料配方研究的检索规划者。把下面的研究主题{ctx}拆成 {n} 个"
        "彼此不重叠的检索子问题，覆盖不同的技术侧面（例如：树脂/基料体系、"
        "固化或成膜机理、关键功能填料与颜料、性能测试与失效模式、工艺与配比）。\n"
        "要求：每个子问题都能独立作为检索词；用简体中文；不要复述原主题；"
        "不要编号前缀。\n\n"
        f"研究主题：{topic}\n\n"
        '返回 JSON：{"questions":["...","..."]}'
    )


def decompose(topic: str, n: int, domain: str = "") -> list[str]:
    """Topic → sub-questions, always including the topic itself first.

    The topic stays in the list because it is the one query guaranteed to be what
    the user actually asked; the sub-questions are additions, not replacements.
    Returns ``[topic]`` when decomposition is disabled or unavailable.
    """
    topic = (topic or "").strip()
    if not topic:
        return []
    want = max(1, min(int(n), _MAX))
    if want <= 1:
        return [topic]

    from ..services import llm

    try:
        data = llm.complete_json(_prompt(topic, want - 1, domain))
    except Exception as exc:
        logger.debug("subquestion decomposition failed: %s", exc)
        data = None

    out = [topic]
    seen = {topic}
    for q in (data or {}).get("questions") or []:
        text = str(q or "").strip()
        # Length guard: a "sub-question" that is one word retrieves noise, and one
        # that is a paragraph is the model ignoring the instruction.
        if not (4 <= len(text) <= 120) or text in seen:
            continue
        out.append(text)
        seen.add(text)
        if len(out) >= want:
            break
    if len(out) == 1:
        logger.debug("subquestion decomposition produced nothing; using topic only")
    return out


def merge_evidence(batches: list[list[Evidence]]) -> list[Evidence]:
    """Interleave per-question results, deduped, best relevance kept.

    Round-robin rather than concatenation: taking each question's top hit before
    any question's second means a single prolific sub-question cannot crowd the
    others out of whatever cap applies downstream. That is the whole point of
    having asked several questions.
    """
    best: dict[str, Evidence] = {}
    order: list[str] = []
    for rank in range(max((len(b) for b in batches), default=0)):
        for batch in batches:
            if rank >= len(batch):
                continue
            ev = batch[rank]
            key = ev.identifier or ev.title
            if not key:
                continue
            if key in best:
                # Same document found by two questions — keep the higher score,
                # since agreement is evidence of relevance, not a reason to halve it.
                if (ev.relevance or 0) > (best[key].relevance or 0):
                    best[key] = ev
                continue
            best[key] = ev
            order.append(key)
    return [best[k] for k in order]
