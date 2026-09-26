"""Phase 1: perspective-guided outline for STORM longform reports."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ...config import get_settings
from .storm_schema import DEFAULT_PERSPECTIVES, ReportOutline, SectionSpec

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, float, dict | None], None]


def _require_storm_enabled() -> None:
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    if not getattr(settings, "wiki_project_dossier_enabled", False):
        raise PermissionError("wiki_project_dossier_enabled is false")
    if not getattr(settings, "wiki_dossier_report_enabled", False):
        raise PermissionError("wiki_dossier_report_enabled is false")
    if not getattr(settings, "wiki_storm_report_enabled", False):
        raise PermissionError("wiki_storm_report_enabled is false")


def _pack_topic(pack: dict[str, Any], topic: str) -> str:
    t = (topic or "").strip()
    if t:
        return t[:200]
    title = (pack.get("title") or "").strip()
    domain = (pack.get("domain") or "").strip()
    return (title or domain or "项目技术报告")[:200]


def _requirement_metrics(pack: dict[str, Any]) -> list[str]:
    rows = (pack.get("requirements") or {}).get("rows") or []
    out: list[str] = []
    for r in rows:
        if isinstance(r, dict) and r.get("metric"):
            out.append(str(r["metric"]))
    return out[:8]


def _formula_names(pack: dict[str, Any]) -> list[str]:
    rows = (pack.get("formula") or {}).get("rows") or []
    names: list[str] = []
    for r in rows:
        if isinstance(r, dict) and r.get("name"):
            names.append(str(r["name"]))
    return names[:8]


def build_deterministic_outline(
    pack: dict[str, Any],
    *,
    project_id: str,
    topic: str = "",
    max_sections: int = 6,
    perspectives: list[str] | None = None,
) -> ReportOutline:
    """Offline outline from DossierPack — no LLM required."""
    topic_s = _pack_topic(pack, topic)
    metrics = _requirement_metrics(pack)
    materials = _formula_names(pack)
    perspectives_use = list(perspectives or DEFAULT_PERSPECTIVES)[:5]
    metric_q = metrics[0] if metrics else "salt_spray_hours"
    mat_q = materials[0] if materials else "baseline formula"

    has_lit = bool((pack.get("literature") or {}).get("rows") or (pack.get("literature") or {}).get("source_ids"))
    has_formula = bool((pack.get("formula") or {}).get("rows"))
    has_doe = bool(
        (pack.get("doe") or {}).get("rows")
        or (pack.get("lab") or {}).get("rows")
        or (pack.get("experiments") or {}).get("rows")
        or (pack.get("campaign") or {}).get("rows")
    )
    has_loop = bool(
        (pack.get("loop") or {}).get("rows")
        or (pack.get("optimization") or {}).get("rows")
        or (pack.get("models") or {}).get("rows")
    )
    # Wave C: perspective-differentiated retrieval queries (not full interview loop).
    cost_q = f"{mat_q} 成本 供应"
    salt_q = f"{mat_q} {metric_q} 机理"

    candidates: list[SectionSpec] = [
        SectionSpec(
            section_id="sec_background",
            title="技术背景与目标指标",
            level=1,
            core_intent="对齐项目要求与成功判据，明确主指标与约束。",
            target_word_count=700,
            focal_entities=metrics[:4],
            retrieval_queries=[
                f"{topic_s} 技术要求 {metric_q}",
                f"{metric_q} 目标窗口 验收",
            ],
            depends_on=[],
        ),
        SectionSpec(
            section_id="sec_literature",
            title="文献与证据地图",
            level=1,
            core_intent="汇总项目文献/专利证据，标明可溯源 source_id。",
            target_word_count=800 if has_lit else 500,
            focal_entities=list((pack.get("literature") or {}).get("source_ids") or [])[:4],
            retrieval_queries=[
                f"{topic_s} 文献 专利 综述",
                salt_q,
            ],
            depends_on=["sec_background"],
        ),
        SectionSpec(
            section_id="sec_formula",
            title="基准配方与物料角色",
            level=1,
            core_intent="描述基准浴/膜配方组分、角色与关键 CAS。",
            target_word_count=700 if has_formula else 450,
            focal_entities=materials[:4],
            retrieval_queries=[
                f"{mat_q} 配方 组成 CAS",
                cost_q,
            ],
            depends_on=["sec_background"],
        ),
        SectionSpec(
            section_id="sec_doe_lab",
            title="DOE 与实验台账解读",
            level=1,
            core_intent="解读 DOE 因子窗口与已测台账，指出缺口。"
            if has_doe
            else "卷宗暂无 DOE/台账行：说明缺口与建议补测，勿编造数据。",
            target_word_count=900 if has_doe else 400,
            focal_entities=[metric_q],
            retrieval_queries=[
                f"{topic_s} DOE 因子 设计",
                f"{metric_q} 实验 测量 台账",
            ],
            depends_on=["sec_formula"],
        ),
        SectionSpec(
            section_id="sec_loop",
            title="寻优闭环与模型态势",
            level=1,
            core_intent="总结闭环 RMSE/候选与下一步实验建议。"
            if has_loop
            else "卷宗暂无闭环/模型行：给出可执行的下一步实验建议。",
            target_word_count=700 if has_loop else 400,
            focal_entities=[],
            retrieval_queries=[
                f"{topic_s} 优化 闭环 RMSE",
                f"{metric_q} 预测 偏差 校准",
            ],
            depends_on=["sec_doe_lab"],
        ),
        SectionSpec(
            section_id="sec_risks",
            title="风险、开放问题与下一步",
            level=1,
            core_intent="汇聚 Flag、合规/工艺风险与可执行下一步。",
            target_word_count=600,
            focal_entities=[],
            retrieval_queries=[
                f"{topic_s} 风险 VOC 合规",
                f"{topic_s} 开放问题 数据缺口",
            ],
            depends_on=["sec_loop", "sec_literature"],
        ),
    ]
    # Wave C: drop thin DOE/loop chapters when pack has no supporting rows
    # (keep at least background + literature + risks).
    drop: set[str] = set()
    if not has_doe:
        drop.add("sec_doe_lab")
    if not has_loop and "sec_doe_lab" in drop:
        drop.add("sec_loop")
    if drop:
        candidates = [s for s in candidates if s.section_id not in drop]
        for s in candidates:
            if s.section_id == "sec_loop" and "sec_doe_lab" in drop:
                s.depends_on = ["sec_formula"]
            if s.section_id == "sec_risks":
                s.depends_on = [d for d in s.depends_on if d not in drop]
                if "sec_loop" in drop and "sec_formula" not in s.depends_on:
                    s.depends_on.append("sec_formula")
    max_n = max(3, min(int(max_sections or 6), 12))
    sections = candidates[:max_n]
    # Drop depends_on pointing outside kept set
    kept = {s.section_id for s in sections}
    for s in sections:
        s.depends_on = [d for d in s.depends_on if d in kept]

    words = sum(s.target_word_count for s in sections)
    return ReportOutline(
        project_id=project_id,
        topic=topic_s,
        global_summary_goal=f"产出可审计的研发长文草稿：{topic_s}（draft_not_claims）。",
        estimated_total_words=words,
        perspectives=perspectives_use,
        sections=sections,
        source="deterministic",
    )


def generate_outline(
    pack: dict[str, Any],
    *,
    project_id: str,
    topic: str = "",
    max_sections: int = 6,
    perspectives: list[str] | None = None,
    use_llm: bool = False,
    progress_cb: ProgressCb | None = None,
) -> ReportOutline:
    """Build outline; optionally ask LLM, always fall back to deterministic."""
    _require_storm_enabled()
    if progress_cb:
        progress_cb("generating_outline", "正在生成报告大纲…", 0.12, None)

    base = build_deterministic_outline(
        pack,
        project_id=project_id,
        topic=topic,
        max_sections=max_sections,
        perspectives=perspectives,
    )
    if not use_llm:
        return base

    try:
        from ...services.llm import complete_structured

        class _OutlineLLM(ReportOutline):
            """Same shape; LLM fills sections."""

        system = (
            "你是工业配方 R&D 报告大纲编辑。输出严格 JSON，符合 schema。"
            "每章必须有 retrieval_queries（至少 1 条）与 core_intent。"
            "不得编造 DOI；focal_entities 只用给定材料/指标名。"
            "disclaimer: 这是研发草稿大纲，非 Claims。"
        )
        user = (
            f"主题: {base.topic}\n"
            f"视角: {', '.join(base.perspectives)}\n"
            f"已知指标: {', '.join(_requirement_metrics(pack)) or '无'}\n"
            f"已知组分: {', '.join(_formula_names(pack)) or '无'}\n"
            f"最多章节: {max_sections}\n"
            f"可参考确定性大纲:\n{base.model_dump_json(indent=2)}\n"
            "请输出更贴合主题的 ReportOutline（保留 project_id）。"
        )
        parsed, err = complete_structured(system, user, ReportOutline, retry=True)
        if parsed is None:
            logger.warning("storm outline LLM failed, using deterministic: %s", err)
            return base
        parsed.project_id = project_id
        parsed.topic = parsed.topic or base.topic
        parsed.source = "llm"
        if not parsed.perspectives:
            parsed.perspectives = list(base.perspectives)
        # Cap sections
        parsed.sections = parsed.sections[: max(3, min(int(max_sections or 6), 12))]
        for s in parsed.sections:
            if not s.retrieval_queries:
                s.retrieval_queries = [f"{parsed.topic} {s.title}"]
        return parsed
    except Exception as exc:
        logger.warning("storm outline LLM exception: %s", exc)
        return base


def persist_outline_sidecar(project_id: str, outline: ReportOutline) -> str:
    """Write outline JSON next to reports/ (App-maintained; not wiki_pages SSOT)."""
    from pathlib import Path

    from ...config import get_settings
    from .schema import project_report_path

    settings = get_settings()
    root = Path(getattr(settings, "wiki_root", None) or "data/wiki")
    try:
        from ...db.wiki_store import get_wiki_store

        store = get_wiki_store()
        root_fn = getattr(store, "root", None)
        if callable(root_fn):
            root = Path(root_fn())
        elif root_fn is not None:
            root = Path(root_fn)
    except Exception:
        pass
    rel = project_report_path(project_id, "storm").replace(".md", ".outline.json")
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(outline.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(rel).replace("\\", "/")
