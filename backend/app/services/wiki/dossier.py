"""Project Dossier Wiki — ensure skeleton + data.json sidecar (P4)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .dossier_pack import build_project_dossier_pack
from .schema import (
    DOSSIER_SECTIONS,
    dump_page,
    project_dossier_data_path,
    project_dossier_path,
    safe_key,
    utcnow_iso,
)
from .vertical_addendum import list_addenda, resolve_addendum

logger = logging.getLogger(__name__)

TEMPLATE = "project_dossier"


def _require_dossier_enabled() -> None:
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    if not getattr(settings, "wiki_project_dossier_enabled", False):
        raise PermissionError("wiki_project_dossier_enabled is false")


def ensure_project_dossier(
    project_id: str,
    *,
    campaign_id: str | None = None,
    vertical: str | None = None,
) -> dict[str, Any]:
    """Create or refresh empty-section skeleton for a project dossier.

    Always writes ``.md`` + ``.data.json``. Does not call LLM.
    """
    _require_dossier_enabled()
    pid = (project_id or "").strip()
    if not pid:
        return {"ok": False, "error": "project_id required"}

    pack = build_project_dossier_pack(pid, campaign_id=campaign_id)
    if vertical is not None:
        pack["vertical_addendum"] = (vertical or "").strip()
    elif not pack.get("vertical_addendum"):
        pack["vertical_addendum"] = (get_settings().wiki_dossier_vertical_addendum or "").strip()

    section_revisions = {s: 1 for s in DOSSIER_SECTIONS}
    path = project_dossier_path(pid)
    data_rel = project_dossier_data_path(pid)
    title = pack.get("title") or pid

    md = _render_skeleton_markdown(
        title=title,
        project_id=pid,
        pack=pack,
        section_revisions=section_revisions,
    )

    store = get_wiki_store()
    flags = ["unreviewed", "dossier"]
    if pack.get("flags", {}).get("missing_requirement"):
        flags.append("incomplete")

    row = store.upsert_page(
        path=path,
        kind="theme",
        title=str(title)[:512],
        norm_key=safe_key(pid),
        entity_id=f"theme:project:{safe_key(pid)}"[:64],
        markdown=md,
        source_ids=list(pack.get("literature", {}).get("source_ids") or []),
        flags=flags,
        replace_source_ids=True,
    )

    data_payload = {
        **pack,
        "path": path,
        "data_path": data_rel,
        "section_revisions": section_revisions,
        "page_id": row.id,
        "revision": int(row.revision or 1),
        "vertical_addendum_text_preview": (resolve_addendum(pack.get("vertical_addendum") or "") or "")[:240],
        "available_addenda": list_addenda(),
        "written_at": utcnow_iso(),
    }
    _write_data_json(store.root(), data_rel, data_payload)

    return {
        "ok": True,
        "path": path,
        "data_path": data_rel,
        "page_id": row.id,
        "revision": int(row.revision or 1),
        "project_id": pid,
        "template": TEMPLATE,
        "section_revisions": section_revisions,
    }


def get_dossier_pack(project_id: str, *, campaign_id: str | None = None) -> dict[str, Any]:
    """Return live DossierPack (and merge sidecar revisions if present)."""
    _require_dossier_enabled()
    pack = build_project_dossier_pack(project_id, campaign_id=campaign_id)
    store = get_wiki_store()
    data_rel = project_dossier_data_path(project_id)
    abs_path = store.root() / data_rel
    if abs_path.is_file():
        try:
            side = json.loads(abs_path.read_text(encoding="utf-8"))
            if isinstance(side.get("section_revisions"), dict):
                pack["section_revisions"] = side["section_revisions"]
            pack["sidecar_revision"] = side.get("revision")
            pack["sidecar_path"] = data_rel
        except Exception as exc:
            logger.debug("dossier sidecar read failed: %s", exc)
    else:
        pack["section_revisions"] = {s: 0 for s in DOSSIER_SECTIONS}
        pack["sidecar_path"] = data_rel
    return pack


def get_dossier_page(project_id: str) -> dict[str, Any] | None:
    _require_dossier_enabled()
    store = get_wiki_store()
    path = project_dossier_path(project_id)
    row = store.get_by_path(path)
    if row is None:
        return None
    md = store.read_markdown(path) or ""
    data_rel = project_dossier_data_path(project_id)
    side = None
    abs_data = store.root() / data_rel
    if abs_data.is_file():
        try:
            side = json.loads(abs_data.read_text(encoding="utf-8"))
        except Exception:
            side = None
    return {
        "page_id": row.id,
        "path": path,
        "data_path": data_rel,
        "title": row.title,
        "flags": list(row.flags or []),
        "revision": int(row.revision or 1),
        "markdown": md,
        "data": side,
    }


def _write_data_json(root: Path, rel: str, payload: dict[str, Any]) -> None:
    path = (root / rel).resolve()
    if not str(path).startswith(str(root.resolve())):
        raise ValueError(f"data path escapes wiki root: {rel}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join(["---"] * len(headers)) + " |"
    if not rows:
        empty = "| " + " | ".join(["—"] * len(headers)) + " |"
        return "\n".join([head, sep, empty])
    body = ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def _render_skeleton_markdown(
    *,
    title: str,
    project_id: str,
    pack: dict[str, Any],
    section_revisions: dict[str, int],
) -> str:
    req_rows = pack.get("requirements", {}).get("rows") or []
    s1_table = _md_table(
        ["指标 / 约束", "目标值", "单位", "方向", "来源"],
        [[r.get("metric"), r.get("value"), r.get("unit"), r.get("direction"), r.get("source")] for r in req_rows],
    )
    formula_rows = pack.get("formula", {}).get("rows") or []
    s3_table = _md_table(
        ["组分", "角色", "Wt%", "CAS/牌号", "来源"],
        [[r.get("name"), r.get("role"), r.get("weight_pct"), r.get("cas"), r.get("source")] for r in formula_rows],
    )
    empty_lit = _md_table(
        ["主题簇", "代表来源", "source_id", "要点", "Wiki/L1"],
        [],
    )
    empty_doe = _md_table(
        ["轮次", "design_type", "因子", "边界", "计划 id", "备注"],
        [],
    )
    empty_lab = _md_table(
        ["时间", "样本/item", "计划参数", "实际参数", "测量指标", "值", "方法", "附件"],
        [],
    )
    loop_hist = pack.get("loop", {}).get("history") or []
    s6_rows = []
    for i, h in enumerate(loop_hist[:20]):
        if isinstance(h, dict):
            s6_rows.append([i + 1, h.get("at") or "", json.dumps(h, ensure_ascii=False)[:80], "", "", ""])
    s6_table = _md_table(
        ["闭环轮次", "时刻", "rmse/摘要", "converged", "doe_plan_id", "备注"],
        s6_rows,
    )
    empty_art = _md_table(
        ["资产", "类型", "路径/URI", "关联节", "生成自"],
        [],
    )

    domain = pack.get("domain") or "—"
    substrate = (pack.get("requirements") or {}).get("substrate") or "—"
    campaign = pack.get("campaign_id") or "—"

    body = f"""# [[{title}]]

> **领域**：{domain} · **基材**：{substrate} · **状态**：进行中
> **卷宗**：Project Dossier · `project_id`=`{project_id}` · campaign=`{campaign}` · 截止 `{pack.get("updated_at")}`

## S1. 技术要求与目标指标
<!-- data:requirements -->
{s1_table}

_叙述待补（确定性表已写入；开启 wiki_dossier_llm_narrative 后可润色）。_

## S2. 文献与证据地图
<!-- data:literature -->
{empty_lit}

## S3. 基准配方与物料
<!-- data:formula -->
{s3_table}

## S4. DOE 设计与结果摘要
<!-- data:doe -->
{empty_doe}

## S5. 实验台账与测量
<!-- data:lab -->
{empty_lab}

## S6. 寻优、闭环与模型态
<!-- data:loop -->
{s6_table}

## S7. 图表与多模态资产
<!-- data:artifacts -->
{empty_art}

## S8. 开放问题 / Flag / 下一步

- 卷宗骨架已创建；请在检索入库、DOE、台账、闭环后执行节级 patch。
- 自动 patch 默认关闭（`wiki_dossier_auto_patch=false`）。
"""

    extra = {
        "template": TEMPLATE,
        "schema_version": 1,
        "llm_generated": False,
        "reviewed": False,
        "project_id": project_id,
        "campaign_id": pack.get("campaign_id") or "",
        "domain": domain,
        "section_revisions": section_revisions,
        "l1_paths": [],
        "doe_plan_ids": [],
        "experiment_ids": [],
        "artifact_ids": [],
        "vertical_addendum": pack.get("vertical_addendum") or "",
    }
    # dump_page wraps Summary/Evidence; for dossier we replace body after front-matter.
    stub = dump_page(
        kind="theme",
        title=title,
        entity_id=f"theme:project:{safe_key(project_id)}"[:64],
        norm_key=safe_key(project_id),
        source_ids=list(pack.get("literature", {}).get("source_ids") or []),
        flags=["unreviewed", "dossier"],
        summary="Project dossier skeleton",
        evidence_blocks=[],
        extra_meta=extra,
    )
    # Keep generated front-matter; swap human body for dossier sections.
    if stub.startswith("---"):
        end = stub.find("\n---", 3)
        if end > 0:
            header = stub[: end + 4]
            return header + "\n\n" + body.strip() + "\n"
    return body
