"""P5 Report generator — DossierPack → markdown draft (not Claims evidence).

Deterministic templates first; optional LLM polish when
``wiki_dossier_llm_narrative`` + ``use_llm``. Never treats L2 narrative as
Claims; citations are source_ids / measurement row refs only.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .dossier import ensure_project_dossier, get_dossier_pack
from .dossier_narrative import validate_narrative
from .schema import dump_page, project_report_path, safe_key, utcnow_iso

logger = logging.getLogger(__name__)

REPORT_TEMPLATES: dict[str, dict[str, Any]] = {
    "briefing": {
        "title": "文献简报 · Briefing Doc",
        "slices": ("requirements", "loop", "literature", "flags"),
        "blurb": "要求摘要 + 闭环态势 + 文献地图；引用仅 source_ids。",
    },
    "feasibility": {
        "title": "技术可行性评估",
        "slices": ("requirements", "formula", "doe", "lab", "flags"),
        "blurb": "要求 / 基准配方 / DOE / 台账缺口。",
    },
    "formula-compare": {
        "title": "配方对比纪要",
        "slices": ("formula", "loop", "flags"),
        "blurb": "基准配方与闭环候选对比。",
    },
    "patent-memo": {
        "title": "专利挖掘备忘",
        "slices": ("literature", "flags"),
        "blurb": "文献/来源地图；须回链 Raw source_ids。",
    },
}


def list_report_templates() -> list[dict[str, str]]:
    return [
        {
            "id": tid,
            "title": str(meta["title"]),
            "blurb": str(meta["blurb"]),
            "slices": ",".join(meta["slices"]),
        }
        for tid, meta in REPORT_TEMPLATES.items()
    ]


def _require_report_enabled() -> None:
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    if not getattr(settings, "wiki_project_dossier_enabled", False):
        raise PermissionError("wiki_project_dossier_enabled is false")
    if not getattr(settings, "wiki_dossier_report_enabled", False):
        raise PermissionError("wiki_dossier_report_enabled is false")


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join(["---"] * len(headers)) + " |"
    if not rows:
        return "\n".join([head, sep, "| " + " | ".join(["—"] * len(headers)) + " |"])

    def cell(c: Any) -> str:
        return "" if c is None else str(c).replace("|", "\\|").replace("\n", " ")

    body = ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def _slice_requirements(pack: dict[str, Any]) -> str:
    rows = (pack.get("requirements") or {}).get("rows") or []
    table = _md_table(
        ["指标 / 约束", "目标值", "单位", "方向", "来源"],
        [[r.get("metric"), r.get("value"), r.get("unit"), r.get("direction"), r.get("source")] for r in rows],
    )
    return f"### 技术要求\n\n{table}\n"


def _slice_formula(pack: dict[str, Any]) -> str:
    rows = (pack.get("formula") or {}).get("rows") or []
    table = _md_table(
        ["组分", "角色", "Wt%", "CAS/牌号", "来源"],
        [[r.get("name"), r.get("role"), r.get("weight_pct"), r.get("cas"), r.get("source")] for r in rows],
    )
    return f"### 基准配方\n\n{table}\n"


def _slice_doe(pack: dict[str, Any]) -> str:
    plans = (pack.get("doe") or {}).get("plans") or []
    table = _md_table(
        ["轮次", "design_type", "边界", "计划 id", "备注"],
        [
            [p.get("round"), p.get("design_type"), (p.get("bounds") or "")[:80], p.get("plan_id"), p.get("notes")]
            for p in plans
            if isinstance(p, dict)
        ],
    )
    return f"### DOE 设计\n\n{table}\n"


def _slice_lab(pack: dict[str, Any]) -> str:
    rows = (pack.get("lab") or {}).get("rows") or []
    table = _md_table(
        ["时间", "样本", "指标", "值", "溯源"],
        [
            [(r.get("at") or "")[:19], r.get("item"), r.get("metric"), r.get("value"), r.get("source")]
            for r in rows
            if isinstance(r, dict)
        ],
    )
    return f"### 实验台账（测量行）\n\n{table}\n"


def _slice_loop(pack: dict[str, Any]) -> str:
    loop = pack.get("loop") or {}
    hist = []
    for i, h in enumerate((loop.get("history") or [])[:15]):
        if isinstance(h, dict):
            hist.append(
                [
                    i + 1,
                    h.get("at") or "",
                    h.get("rmse") if h.get("rmse") is not None else json.dumps(h, ensure_ascii=False)[:60],
                    h.get("converged"),
                ]
            )
    hist_t = _md_table(["轮次", "时刻", "rmse/摘要", "converged"], hist)
    cands = [
        [c.get("name"), c.get("score"), json.dumps(c.get("predicted") or {}, ensure_ascii=False)[:60]]
        for c in (loop.get("candidates") or [])[:8]
        if isinstance(c, dict)
    ]
    cand_t = _md_table(["候选配方", "分数", "预测"], cands)
    return f"### 闭环与候选\n\n{hist_t}\n\n{cand_t}\n"


def _slice_literature(pack: dict[str, Any]) -> str:
    rows = (pack.get("literature") or {}).get("rows") or []
    table = _md_table(
        ["主题簇", "代表来源", "source_id", "要点"],
        [
            [r.get("cluster"), r.get("title"), r.get("source_id"), r.get("snippet")]
            for r in rows
            if isinstance(r, dict)
        ],
    )
    return f"### 文献与来源地图\n\n{table}\n"


def _slice_flags(pack: dict[str, Any]) -> str:
    flags = pack.get("flags") or {}
    lines = [f"- `{k}`" for k, v in flags.items() if v]
    if not lines:
        lines = ["- （无空切片 Flag）"]
    return "### 开放 Flag\n\n" + "\n".join(lines) + "\n"


_SLICE_RENDERERS = {
    "requirements": _slice_requirements,
    "formula": _slice_formula,
    "doe": _slice_doe,
    "lab": _slice_lab,
    "loop": _slice_loop,
    "literature": _slice_literature,
    "flags": _slice_flags,
}


def _citations_block(pack: dict[str, Any]) -> str:
    """Citations must be Raw/source_ids or measurement refs — never L2 prose."""
    lit_ids = list((pack.get("literature") or {}).get("source_ids") or [])
    lab_refs = []
    for r in (pack.get("lab") or {}).get("rows") or []:
        if isinstance(r, dict) and r.get("source"):
            lab_refs.append(str(r["source"]))
    lab_refs = list(dict.fromkeys(lab_refs))[:40]

    lines = [
        "## 引用与溯源（非 Claims）",
        "",
        "> 下列标识指向 Raw / 台账行。**不得**把本报告叙述句当作 Claims 证据。",
        "",
        "### source_ids",
    ]
    if lit_ids:
        lines.extend(f"- `{sid}`" for sid in lit_ids[:60])
    else:
        lines.append("- （无）")
    lines.extend(["", "### 测量行溯源"])
    if lab_refs:
        lines.extend(f"- `{ref}`" for ref in lab_refs)
    else:
        lines.append("- （无）")
    lines.append("")
    return "\n".join(lines)


def render_report_markdown(
    *,
    template: str,
    pack: dict[str, Any],
    prompt: str = "",
    polish: str = "",
) -> str:
    meta = REPORT_TEMPLATES[template]
    title = meta["title"]
    project_id = pack.get("project_id") or ""
    parts = [
        f"# {title}",
        "",
        f"> 项目：`{project_id}` · `{pack.get('title') or ''}` · domain=`{pack.get('domain') or '—'}`",
        f"> 模板：`{template}` · Pack 截止 `{pack.get('updated_at')}` · **研发草稿，需人工审核**",
        "",
        f"_{meta['blurb']}_",
        "",
    ]
    if (prompt or "").strip():
        parts.extend(["### 用户提示（已记录，未改表内数字）", "", prompt.strip(), ""])
    if (polish or "").strip():
        parts.extend(["## 执行摘要（LLM 润色，非证据）", "", polish.strip(), ""])
    parts.append("## 卷宗切片（确定性）")
    parts.append("")
    for slice_name in meta["slices"]:
        renderer = _SLICE_RENDERERS.get(slice_name)
        if renderer:
            parts.append(renderer(pack))
    parts.append(_citations_block(pack))
    parts.extend(
        [
            "---",
            "",
            f"_Generated from DossierPack · template=`{template}` · {utcnow_iso()}_",
            "",
        ]
    )
    return "\n".join(parts)


def _try_llm_polish(*, template: str, pack: dict[str, Any], body: str, prompt: str) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"used_llm": False, "model": "", "prompt_hash": "", "error": None}
    settings = get_settings()
    if not getattr(settings, "wiki_dossier_llm_narrative", False):
        meta["error"] = "narrative_flag_off"
        return "", meta
    try:
        from pydantic import BaseModel, Field

        from ..llm import complete_structured

        class ReportPolish(BaseModel):
            executive_summary: str = Field(
                description="Chinese executive summary only; no tables; no image paths; no new numbers"
            )

        system = (
            "You polish a FormuMind R&D draft report executive summary. "
            "Use ONLY facts present in the provided markdown. "
            "Do not invent numbers, citations, ASTM data, or asset paths. "
            "Do not output markdown tables. Reply in Chinese. "
            "This summary is NOT Claims evidence."
        )
        user = (
            f"Template: {template}\nProject: {pack.get('title')}\n"
            f"User prompt: {prompt or '(none)'}\n\nDraft excerpt:\n{body[:5000]}\n"
        )
        meta["prompt_hash"] = hashlib.sha1(f"{system}\n{user}".encode()).hexdigest()[:16]
        meta["model"] = getattr(settings, "llm_model", "") or ""
        out, err = complete_structured(system, user, ReportPolish, retry=False)
        if out is None:
            meta["error"] = err or "llm_failed"
            return "", meta
        text = (out.executive_summary or "").strip()
        bad = validate_narrative(text)
        if bad:
            meta["error"] = f"validation:{bad}"
            return "", meta
        meta["used_llm"] = True
        return text, meta
    except Exception as exc:  # noqa: BLE001
        logger.info("report LLM polish skipped: %s", exc)
        meta["error"] = str(exc)
        return "", meta


def generate_report(
    project_id: str,
    template: str,
    *,
    campaign_id: str | None = None,
    prompt: str = "",
    use_llm: bool = False,
    ensure_dossier: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    """Build a report markdown from live DossierPack and optionally persist."""
    _require_report_enabled()
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id required")
    tpl = (template or "").strip().lower()
    if tpl not in REPORT_TEMPLATES:
        raise ValueError(f"unknown report template: {template}; choose from {list(REPORT_TEMPLATES)}")

    if ensure_dossier:
        try:
            ensure_project_dossier(pid, campaign_id=campaign_id)
        except Exception as exc:
            logger.debug("ensure dossier before report soft-failed: %s", exc)

    pack = get_dossier_pack(pid, campaign_id=campaign_id)
    body = render_report_markdown(template=tpl, pack=pack, prompt=prompt)
    llm_meta: dict[str, Any] = {"used_llm": False}
    if use_llm:
        polish, llm_meta = _try_llm_polish(template=tpl, pack=pack, body=body, prompt=prompt)
        if polish:
            body = render_report_markdown(template=tpl, pack=pack, prompt=prompt, polish=polish)

    path = project_report_path(pid, tpl)
    title = f"{REPORT_TEMPLATES[tpl]['title']} · {pack.get('title') or pid}"
    out: dict[str, Any] = {
        "ok": True,
        "project_id": pid,
        "template": tpl,
        "path": path,
        "title": title,
        "markdown": body,
        "llm": llm_meta,
        "source_ids": list((pack.get("literature") or {}).get("source_ids") or []),
        "pack_updated_at": pack.get("updated_at"),
        "disclaimer": "draft_not_claims",
    }

    if not persist:
        return out

    store = get_wiki_store()
    flags = ["unreviewed", "report", "draft"]
    if llm_meta.get("used_llm"):
        flags.append("llm_draft")
    extra = {
        "template": f"report_{tpl}",
        "report_template": tpl,
        "schema_version": 1,
        "llm_generated": bool(llm_meta.get("used_llm")),
        "reviewed": False,
        "project_id": pid,
        "campaign_id": pack.get("campaign_id") or "",
        "disclaimer": "draft_not_claims",
        "model": llm_meta.get("model") or "",
        "prompt_hash": llm_meta.get("prompt_hash") or "",
    }
    md = dump_page(
        kind="report",
        title=title,
        entity_id=f"report:{tpl}:{safe_key(pid)}"[:64],
        norm_key=f"{safe_key(pid)}-{tpl}"[:80],
        source_ids=list((pack.get("literature") or {}).get("source_ids") or []),
        flags=flags,
        summary=body[:400],
        evidence_blocks=[body],
        extra_meta=extra,
    )
    # dump_page wraps summary/evidence; for reports we prefer our composed body after FM.
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end > 0:
            md = md[: end + 4] + "\n\n" + body.strip() + "\n"

    row = store.upsert_page(
        path=path,
        kind="report",
        title=title[:512],
        norm_key=f"{safe_key(pid)}-{tpl}"[:80],
        entity_id=f"report:{tpl}:{safe_key(pid)}"[:64],
        markdown=md,
        source_ids=list((pack.get("literature") or {}).get("source_ids") or []),
        flags=flags,
        replace_source_ids=True,
    )
    out["page_id"] = row.id
    out["revision"] = int(row.revision or 1)
    out["markdown"] = md
    out["persisted"] = True
    return out
