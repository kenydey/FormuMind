"""Static formulation action skills (Dim-5) — playbooks bound to Actions, not MCP agents."""
from __future__ import annotations

from typing import Any

# Domain-adaptive retrieval hints for playbooks that used to hardcode silane / coatings.
DOMAIN_SEARCH_HINTS: dict[str, str] = {
    "anticorrosion_coating": "防腐涂料 底漆 缓蚀",
    "degreaser": "脱脂剂 表面活性剂 清洗",
    "surface_treatment": "表面处理 转化膜 附着力",
    "autodeposition_coating": "自沉积涂料 酸致凝聚",
}
DEFAULT_SEARCH_HINT = "配方 组分 工艺"

# Legacy bookmark / prefs id → current id (one release of compatibility).
_SKILL_ALIASES: dict[str, str] = {
    "silane_recommend": "formula_recommend",
}

# Bound to existing FormuMind Actions / Research entry points — no LangGraph.
FORMULATION_SKILLS: list[dict[str, Any]] = [
    {
        "id": "formula_recommend",
        "kind": "playbook",
        "title": "配方推荐",
        "summary": "按当前产品域优先材料库 + CRAG 检索，产出 Top-N 配方候选（不锁死单一化学体系）。",
        "when_to_use": "需要从知识库快速拉出可落地的配方候选时。",
        "action": "recommend",
        "modal": "recommend",
        "icon": "⭐",
        "tools": ["kb_hybrid", "crag_grade", "formulation_recommend"],
        "checklist": [
            {"id": "retrieve", "title": "检索知识库"},
            {"id": "grade", "title": "CRAG 评估"},
            {"id": "recommend", "title": "生成配方榜"},
        ],
        "presets": {
            "prefer_materials_catalog": True,
            # Resolved at apply-time from ProductDomain (see resolve_search_hint).
            "search_hint_mode": "domain",
        },
    },
    {
        "id": "ccd_doe_screen",
        "kind": "playbook",
        "title": "CCD 筛选 DOE",
        "summary": "经典中心复合设计，适合 3–5 因子冷启动筛选。",
        "when_to_use": "有候选因子区间、尚无足够实测数据时。",
        "action": "doe",
        "modal": "doe",
        "icon": "🔬",
        "tools": ["doe_native", "factor_suggest", "workbench_export"],
        "checklist": [
            {"id": "factors", "title": "确认因子与区间"},
            {"id": "design", "title": "生成 CCD 方案"},
            {"id": "ledger", "title": "写入实验台账"},
        ],
        "presets": {
            "doe_engine": "native",
            "doe_design": "ccd",
        },
    },
    {
        "id": "bayesian_optimize",
        "kind": "playbook",
        "title": "贝叶斯寻优",
        "summary": "多目标 DOE 寻优闭环，收敛历史曲线进产物区。",
        "when_to_use": "已有若干实测点，需要 surrogate 驱动下一轮建议时。",
        "action": "optimize",
        "modal": "optimize",
        "icon": "📈",
        "tools": ["optimize_loop", "surrogate_model", "pareto_rank"],
        "checklist": [
            {"id": "init", "title": "加载历史实测"},
            {"id": "propose", "title": "提出候选"},
            {"id": "converge", "title": "更新收敛曲线"},
        ],
        "presets": {
            "optimize_engine": "auto",
        },
    },
    {
        "id": "self_driving_loop",
        "kind": "playbook",
        "title": "自驱动闭环",
        "summary": "数据→重训→寻优→下一批 DOE 一键迭代。",
        "when_to_use": "台账已有进度，希望自动推进下一轮实验时。",
        "action": "loop",
        "modal": "loop",
        "icon": "🔄",
        "tools": ["workbench_sync", "model_retrain", "next_doe"],
        "checklist": [
            {"id": "sync", "title": "同步实测"},
            {"id": "retrain", "title": "重训模型"},
            {"id": "next_batch", "title": "生成下一批"},
        ],
        "presets": {},
    },
    {
        "id": "deep_literature",
        "kind": "playbook",
        "title": "深度文献综述",
        "summary": "多智能体深度研究：广域检索 + KB + 带引用报告。",
        "when_to_use": "立项前需要带引用的综合研究报告时。",
        "action": "deep_research",
        "modal": None,
        "icon": "📑",
        "tools": ["web_agent", "kb_agent", "report_agent"],
        "checklist": [
            {"id": "web", "title": "广域检索"},
            {"id": "kb", "title": "知识库增强"},
            {"id": "report", "title": "综合报告"},
        ],
        "presets": {
            # Domain theme + 「综述」; no hard-coded coatings/silane query.
            "search_hint_mode": "domain_literature",
        },
    },
]


def resolve_search_hint(
    presets: dict[str, Any] | None,
    *,
    domain: str | None = None,
) -> str | None:
    """Resolve playbook search hint from presets + optional ProductDomain value.

    Modes:
    - ``domain`` / ``domain_literature``: map domain → hint (literature appends 综述)
    - explicit non-empty ``search_hint`` string still wins when mode is absent
    """
    presets = presets or {}
    mode = str(presets.get("search_hint_mode") or "").strip()
    explicit = presets.get("search_hint")
    if isinstance(explicit, str) and explicit.strip() and not mode:
        return explicit.strip()

    if mode in {"domain", "domain_literature"}:
        key = (domain or "").strip()
        base = DOMAIN_SEARCH_HINTS.get(key) or DEFAULT_SEARCH_HINT
        if mode == "domain_literature":
            return f"{base} 综述"
        return base

    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return None


def list_formulation_skills() -> list[dict[str, Any]]:
    return list(FORMULATION_SKILLS)


def get_formulation_skill(skill_id: str) -> dict[str, Any] | None:
    resolved = _SKILL_ALIASES.get(skill_id, skill_id)
    for s in FORMULATION_SKILLS:
        if s["id"] == resolved:
            return dict(s)
    return None
