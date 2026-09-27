"""Lightweight Evidence Reviewer — second-pass claim↔citation check."""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def reviewer_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "evidence_reviewer_enabled", False))


def llm_rubric_enabled(settings: Any) -> bool:
    """LLM rubric 判定开关（默认关闭）。"""
    return bool(getattr(settings, "evidence_reviewer_llm_enabled", False))


class ReviewerModelError(Exception):
    """reviewer 专用模型（evidence_reviewer_model）调用失败。

    P1-19：配置了专用小模型时，调用失败 / 返回非法 → 明确抛错，
    不静默回退启发式（避免"以为审了"）。
    """


def reviewer_model_name(settings: Any) -> str | None:
    """解析 reviewer 专用模型名。

    未配置（空字符串）→ None（沿用全局 llm_model，保持向后兼容）。
    """
    try:
        name = getattr(settings, "evidence_reviewer_model", None)
    except Exception:  # noqa: BLE001
        name = None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


# ---------------------------------------------------------------------------
# P1-11 reviewer 工具边界
#
# reviewer 会话禁止调用任何工具：当前实现仅调用 llm.complete_json 做判定，
# 无工具分发能力（api/chat.py 的 review_answer / run_fix_loop 调用链已核查，
# repair_fn 调的是 chat LLM，不属于 reviewer 工具）。
# 白名单为空即"全部拒绝"；assert_reviewer_tool_boundary() 在流水线入口调用，
# 既是文档化边界，也是防未来回归的断言。
# ---------------------------------------------------------------------------
REVIEWER_ALLOWED_TOOLS: frozenset[str] = frozenset()


def assert_reviewer_tool_boundary(tool_name: str | None = None) -> None:
    """断言 reviewer 工具边界。

    无参数调用 = 声明本流水线不经过工具分发（review 入口调用）；
    带 tool_name = 校验单次工具调用，白名单为空时一律拒绝。
    """
    if tool_name is None:
        return
    if tool_name not in REVIEWER_ALLOWED_TOOLS:
        raise PermissionError(f"reviewer 工具调用被拒绝：{tool_name!r} 不在白名单内")


# ---------------------------------------------------------------------------
# LLM rubric prompt（模板，非代码逻辑）
#
# 落实 aipoch rubric：
#   §5.7 查不到 ≠ 伪造：只有"找到的矛盾"才定罪；查不到来源 → 不定罪、不 warn
#   §5.8 伪造引用是唯一例外：具体标识符 + 本轮新确立 + 全会话无 trace 才定罪；
#       背景知识归因（无具体标识符）豁免
#   §5.4 八条 fail 判定臂，每条配"mere absence 不算 contradiction"反例
#   §5.2/§5.3 只看本轮请求 + 有效计划；落盘工件从严、聊天散文只看行动误导性
#   §5.9 finding.evidence 只能引用实际读到的记录
#   §5.11 输出 JSON 契约
# ---------------------------------------------------------------------------
REVIEWER_RUBRIC_PROMPT = """你是 Evidence Reviewer：一名严格但公平的"断言↔引用"二审员。
只审查"可查证的具体断言"（checkable claims）；不评价风格、措辞、格式。

## 适用范围（§5.2 / §5.3）
- 只看本轮请求与当前有效计划相关的回答内容，不追溯历史轮次。
- 落盘工件（文档/报告/导出文件）从严：无据的数字与关键表述必须有来源标注。
- 聊天散文回答从宽：只看是否存在"行动误导性"（用户按此行动会造成实质后果），不苛求每句散文都有脚注。

## 核心规则一：查不到 ≠ 伪造（§5.7）
- 只有"找到的矛盾"（found contradiction）才能定罪为 failure：即引用来源的实际内容与回答断言冲突。
- 查不到来源、来源不可访问、摘要不含该细节 → 不定罪、不 warn，最多视为弱支撑或无发现。
- 反例：回答称"某文献报道 X 工艺"，检索不到该文献 → 不是 contradiction，不得判 failure。

## 核心规则二：伪造引用是唯一例外（§5.8）
当且仅当以下三者同时成立，才可因伪造引用判 failure：
  1. 引用以具体标识符呈现（DOI / PMID / 作者+年份+期刊等）；
  2. 该引用呈现为本轮回答中新确立的来源；
  3. 全会话记录中无任何 trace（CITATIONS 列表、检索记录、历史引用均未出现过它）。
背景知识归因豁免：没有具体标识符的一般性归因（如"业内常用做法"）不适用本条。

## Fail 判定臂（§5.4）——八条
每条仅在"找到矛盾证据"时触发；mere absence（单纯找不到）不算 contradiction：
1. 数字矛盾：回答数值与引用来源数值冲突。反例：来源没提该数值 → 不算矛盾。
2. 身份矛盾：引用的文献/标准实际不存在或与描述不符（须满足 §5.8 三条件）。反例：文献存在但摘要未覆盖细节 → 不算。
3. 归因错误：把 A 来源的结论归于 B 来源。反例：B 来源未提及该结论 → 只是弱支撑，不是归因错误。
4. 范围外推：把特定条件下的结论断言为普适。反例：回答已限定条件 → 不算外推。
5. 方法错配：引用的方法与回答描述的方法实质不同。反例：方法细节在来源中未展开 → 不算错配。
6. 时效矛盾：引用已被撤稿/替代版本推翻且回答未说明。反例：仅是较旧版本 → 不算矛盾。
7. 单位/量级错误：单位换算或数量级与来源差 10 倍以上。反例：单位写法差异但数值一致 → 不算。
8. 选择性引用：引用来源实际包含相反结论而回答隐瞒。反例：来源未讨论反面 → 不算隐瞒。

## Finding 的证据约束（§5.9）
finding.evidence 只能引用"实际读到的记录"：即下方 CITATIONS 列表中的条目，或回答正文中的具体句子。不得引用你没见过的来源。

## 防自证（P1-27）
- review 结论不得引用被 review 对象自身的陈述作为证据：回答中的断言不能用回答中的另一句话来"证明"（循环论证无效）。
- §5.9 允许引用回答原文仅用于定位被审查的句子，不得为其真实性背书；若某 finding.evidence 只有回答自引而无 CITATIONS 外部记录，该 finding 视为无效。

## 输出契约（§5.11）
- 只输出一个 JSON 对象，不要输出除 JSON 外的任何文字，不要用 markdown 代码围栏。
- schema：
  {{"status": "pass"|"warning"|"failure",
    "findings": [{{"rule": "5.4-1" 等规则编号,
                  "severity": "blocking"|"major"|"minor",
                  "title": "一句话标题",
                  "detail": "具体说明",
                  "evidence": ["引用的 CITATIONS 条目编号或原文句子"]}}],
    "notes": ["补充说明"]}}
- severity：blocking=必须修否则阻断导出；major=建议修；minor=提示。
- 无 checkable claims 时输出 {{"status":"pass","findings":[],"notes":[]}}。

QUESTION:
{question}

ANSWER:
{answer}

CITATIONS（实际读到的记录，最多 20 条；编号即为引用来源）:
{citations_block}
"""

_LLM_STATUSES = {"pass", "warning", "failure"}
_LLM_SEVERITIES = {"blocking", "major", "minor"}
_CITATION_CAP = 20
_CITATION_SNIPPET_CAP = 300


def _citation_brief(item: Any) -> dict[str, str]:
    """从引用条目提取 title/doi/snippet（dict 或对象均可）。"""
    if isinstance(item, dict):
        title = item.get("title") or item.get("name") or ""
        doi = item.get("doi") or ""
        snippet = (
            item.get("snippet") or item.get("abstract") or item.get("summary") or ""
        )
    else:
        title = getattr(item, "title", "") or ""
        doi = getattr(item, "doi", "") or ""
        snippet = getattr(item, "snippet", "") or getattr(item, "abstract", "") or ""
    return {
        "title": str(title),
        "doi": str(doi),
        "snippet": str(snippet)[:_CITATION_SNIPPET_CAP],
    }


def _validate_llm_review(data: Any) -> dict[str, Any] | None:
    """校验 LLM 输出 schema；非法 → None（fail-open）。"""
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status not in _LLM_STATUSES:
        return None
    raw_findings = data.get("findings") or []
    if not isinstance(raw_findings, list):
        return None
    findings: list[dict[str, Any]] = []
    for f in raw_findings:
        if not isinstance(f, dict):
            continue
        evidence = f.get("evidence")
        findings.append(
            {
                "rule": str(f.get("rule") or ""),
                "severity": f.get("severity")
                if f.get("severity") in _LLM_SEVERITIES
                else "minor",
                "title": str(f.get("title") or ""),
                "detail": str(f.get("detail") or ""),
                "evidence": [str(e) for e in evidence if e]
                if isinstance(evidence, list)
                else [],
            }
        )
    raw_notes = data.get("notes")
    notes = (
        [str(n) for n in raw_notes if n]
        if isinstance(raw_notes, list)
        else []
    )
    return {"status": status, "findings": findings, "notes": notes}


def review_answer_llm(
    question: str,
    answer: str,
    citations: list[Any],
    *,
    settings: Any,
) -> dict[str, Any] | None:
    """LLM rubric 判定 pass。

    citations 取前 20 条（title/doi/snippet[:300]），调用 llm.complete_json。
    未配置专用模型时：任何异常 / 输出非法 / LLM 未配置 → 返回 None，
    由调用方回退启发式（fail-open）。
    P1-19：配置了 evidence_reviewer_model 时，调用失败 / 输出非法 →
    抛 ReviewerModelError（明确报错，不静默回退，避免"以为审了"）。
    """
    if not (answer or "").strip():
        return None
    model = reviewer_model_name(settings)
    try:
        from . import llm as _llm

        briefs = [_citation_brief(c) for c in (citations or [])[:_CITATION_CAP]]
        citations_block = "\n".join(
            f"[{i + 1}] {b['title']} | doi: {b['doi'] or '无'} | {b['snippet']}"
            for i, b in enumerate(briefs)
        ) or "(无引用)"
        prompt = REVIEWER_RUBRIC_PROMPT.format(
            question=question or "",
            answer=answer or "",
            citations_block=citations_block,
        )
        data = _llm.complete_json(prompt, model=model)
    except ReviewerModelError:
        raise
    except Exception as exc:  # noqa: BLE001
        if model:
            logger.error("reviewer 模型 %r 调用失败: %s", model, exc)
            raise ReviewerModelError(f"reviewer 模型 {model!r} 调用失败: {exc}") from exc
        logger.debug("evidence reviewer LLM skipped: %s", exc)
        return None
    review = _validate_llm_review(data)
    if review is None and model:
        raise ReviewerModelError(f"reviewer 模型 {model!r} 返回非法结果")
    return review


def _map_llm_review(llm_review: dict[str, Any]) -> dict[str, Any]:
    """把 LLM rubric 结果映射为旧字段，保持 reviewer_fix_loop 兼容。

    - notes：保留 LLM notes，另把每条 finding 展开为一行 "[rule·severity] title：detail"
    - suggestion：由 findings 自动生成（"请处理 N 条 blocking…"）
    - unsupported_count：severity=blocking 且规则为 5.7/5.8（无据/伪造）类
    - weak_count：severity=major
    - findings：保留原始 findings 供上游使用
    """
    findings = llm_review.get("findings") or []
    notes: list[str] = list(llm_review.get("notes") or [])
    for f in findings:
        notes.append(
            f"[{f.get('rule') or 'n/a'}·{f.get('severity')}] "
            f"{f.get('title')}：{f.get('detail')}"
        )
    blocking = [f for f in findings if f.get("severity") == "blocking"]
    major = [f for f in findings if f.get("severity") == "major"]
    unsupported = [
        f
        for f in blocking
        if str(f.get("rule") or "").startswith(("5.7", "5.8"))
    ]
    status = llm_review.get("status") or "pass"
    suggestion = None
    if status != "pass":
        suggestion = (
            f"请处理 {len(blocking)} 条 blocking、{len(major)} 条 major："
            "删除无据断言，或补充可跳转来源；数值工艺参数必须带来源。"
        )
    return {
        "status": status,
        "notes": notes,
        "suggestion": suggestion,
        "unsupported_count": len(unsupported),
        "weak_count": len(major),
        "findings": findings,
    }


def review_answer(
    question: str,
    answer: str,
    citations: list[Any],
    *,
    settings: Any,
) -> dict[str, Any] | None:
    """Heuristic + optional LLM rubric pass. Fail-open (returns None on errors).

    P1-19：配置了 evidence_reviewer_model 时，LLM 失败抛 ReviewerModelError
   （不回退启发式）；未配置时保持 fail-open。
    """
    assert_reviewer_tool_boundary()  # P1-11：reviewer 流水线不经过工具分发
    if not reviewer_enabled(settings) or not (answer or "").strip():
        return None
    # LLM rubric 优先：开关开且 LLM 成功 → 用 LLM 结果；否则回退启发式
    if llm_rubric_enabled(settings):
        llm_review = review_answer_llm(question, answer, citations, settings=settings)
        if llm_review is not None:
            return _map_llm_review(llm_review)
    try:
        from .chat_claims import build_sourced_claims

        claims = build_sourced_claims(
            question,
            answer,
            citations,
            structured=None,
            settings=settings,
        ) or []
        unsupported = [c for c in claims if getattr(c, "status", None) == "unsupported"]
        weak = [c for c in claims if getattr(c, "status", None) == "weak"]
        # Also flag invented-looking DOIs already marked in answer footer
        doi_warn = "DOI 校验" in (answer or "")
        status = "pass"
        if unsupported or doi_warn:
            status = "failure" if unsupported else "warning"
        elif weak:
            status = "warning"
        notes: list[str] = []
        if unsupported:
            notes.append(f"{len(unsupported)} 条断言无据")
        if weak:
            notes.append(f"{len(weak)} 条弱支撑")
        if doi_warn:
            notes.append("DOI 校验提出警告")
        suggestion = None
        if status != "pass":
            suggestion = (
                "请收紧表述：删除无据断言，或补充可跳转来源；"
                "数值工艺参数必须带来源。"
            )
        return {
            "status": status,
            "notes": notes,
            "suggestion": suggestion,
            "unsupported_count": len(unsupported),
            "weak_count": len(weak),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("evidence reviewer skipped: %s", exc)
        return None


_NUMERIC_BARE = re.compile(
    r"\b(\d+(?:\.\d+)?\s*(?:wt%|%|℃|°C|MPa|μm|µm|hrs?|h|min))\b"
)


def flag_bare_numerics(answer: str) -> list[str]:
    """Soft signal: numerics with units that lack nearby citation markers."""
    hits: list[str] = []
    text = answer or ""
    for m in _NUMERIC_BARE.finditer(text):
        start = max(0, m.start() - 40)
        window = text[start : m.end() + 10]
        if "[^" in window or "(doi" in window.lower() or "doi.org" in window.lower():
            continue
        hits.append(m.group(1))
    return hits[:12]
