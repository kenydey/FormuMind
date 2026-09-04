"""Claim-level sourcing for chat answers."""
from __future__ import annotations

import logging
import re

from ..config import Settings, get_settings
from ..domain.chat_schemas import SourcedClaim, StructuredAnswer
from ..domain.schemas import Evidence
from ..pipeline.claim_checker import ClaimVerdict, verify_claim_offline, verify_claims_llm

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s*")

# 2026-09-04 (P3): 每次问答新建 executor + shutdown(wait=False) 会让超时的
# deepseek 阻塞线程成为孤儿(60s idle×2 重试 ≈ 2 分钟才自然消亡), 慢窗口
# 高频问答下 OS 线程持续积累。改为模块级共享池(max_workers=2): 超时任务
# 仍占 worker 直至上游返回, 但总数封顶、排队自然节流, 不再无限新增线程。
import concurrent.futures as _cf

_CLAIM_EXECUTOR: _cf.ThreadPoolExecutor | None = None


def _claim_executor() -> _cf.ThreadPoolExecutor:
    global _CLAIM_EXECUTOR
    if _CLAIM_EXECUTOR is None:
        _CLAIM_EXECUTOR = _cf.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="claim-verify"
        )
    return _CLAIM_EXECUTOR


def build_sourced_claims(
    question: str,
    answer: str,
    sources: list[Evidence],
    structured: StructuredAnswer | None = None,
    *,
    settings: Settings | None = None,
) -> list[SourcedClaim] | None:
    settings = settings or get_settings()
    if not settings.chat_claim_check_enabled:
        return None

    claims = _extract_claims(answer, structured)
    if not claims:
        return []

    try:
        # 2026-09-04: deepseek 慢窗口会让 claims 的 LLM 验证挂 60-120s+
        # (实测 150s+ 卡死整次问答)——12s 硬超时, 超时降级 offline 验证,
        # 主回答不受影响。共享池(见模块级 _CLAIM_EXECUTOR)封顶线程数。
        _ex = _claim_executor()
        _fut = None
        try:
            _fut = _ex.submit(verify_claims_llm, question, claims, sources)
            verified = _fut.result(timeout=12)
        except Exception:
            if _fut is not None:
                _fut.cancel()  # 运行中取消无效, 但可清队列中未启动任务
            raise
    except Exception:
        verified = [verify_claim_offline(c, sources) for c in claims]

    out: list[SourcedClaim] = []
    for v in verified:
        chunk_ids = _indices_to_chunk_ids(v.evidence_indices, sources)
        status = _map_verdict(v.verdict)
        conf = 0.9 if status == "supported" else 0.4 if status == "weak" else 0.1
        out.append(
            SourcedClaim(
                text=v.text,
                chunk_ids=chunk_ids,
                confidence=conf,
                status=status,
            )
        )
    return out


def _extract_claims(answer: str, structured: StructuredAnswer | None) -> list[str]:
    if structured and structured.key_findings:
        return [c.strip() for c in structured.key_findings if c.strip()]
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(answer or "") if p.strip()]
    return parts[:8]


def _indices_to_chunk_ids(indices: list[int], sources: list[Evidence]) -> list[str]:
    ids: list[str] = []
    for idx in indices:
        if 0 <= idx < len(sources):
            ident = sources[idx].identifier or ""
            if ident.startswith("kb:"):
                ids.append(ident[3:])
            elif ident:
                ids.append(ident)
    return list(dict.fromkeys(ids))


def _map_verdict(verdict: ClaimVerdict) -> str:
    if verdict == ClaimVerdict.supported:
        return "supported"
    if verdict in (ClaimVerdict.insufficient, ClaimVerdict.conflicting):
        return "weak"
    return "unsupported"
