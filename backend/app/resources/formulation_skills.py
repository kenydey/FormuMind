"""Static formulation action skills (Dim-5) — coatings playbooks, not MCP agents."""
from __future__ import annotations

from typing import Any

# Bound to existing FormuMind Actions / Research entry points — no LangGraph.
FORMULATION_SKILLS: list[dict[str, Any]] = [
    {
        "id": "silane_recommend",
        "kind": "playbook",
        "title": "硅烷偶联推荐",
        "summary": "优先材料库 + CRAG 检索，产出 Top-N 硅烷/环氧偶联配方候选。",
        "when_to_use": "需要快速从知识库拉出可落地的偶联剂/底漆配方时。",
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
            "search_hint": "硅烷偶联剂 环氧 底漆",
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
            "search_hint": "金属表面处理 防腐涂料 综述",
        },
    },
]


def list_formulation_skills() -> list[dict[str, Any]]:
    return list(FORMULATION_SKILLS)


def get_formulation_skill(skill_id: str) -> dict[str, Any] | None:
    for s in FORMULATION_SKILLS:
        if s["id"] == skill_id:
            return dict(s)
    return None
