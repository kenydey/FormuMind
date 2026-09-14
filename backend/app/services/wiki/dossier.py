"""Project Dossier Wiki — ensure / patch / refresh + data.json sidecar (P4)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from .dossier_pack import build_project_dossier_pack
from .dossier_narrative import (
    attach_narrative,
    extract_narrative_from_section,
    generate_section_narrative,
    narrative_enabled,
)
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
_NARRATIVE_PLACEHOLDER = (
    "_叙述待补（确定性表已写入；开启 wiki_dossier_llm_narrative 后可润色）。_"
)

# API may pass "S1" or "S1_requirements"
_SECTION_ALIASES: dict[str, str] = {
    "S1": "S1_requirements",
    "S2": "S2_literature",
    "S3": "S3_baseline_formula",
    "S4": "S4_doe",
    "S5": "S5_lab_ledger",
    "S6": "S6_optimize_loop",
    "S7": "S7_artifacts",
    "S8": "S8_open_questions",
}
for _s in DOSSIER_SECTIONS:
    _SECTION_ALIASES[_s] = _s
    _SECTION_ALIASES[_s.upper()] = _s

_SECTION_TITLES: dict[str, str] = {
    "S1_requirements": "S1. 技术要求与目标指标",
    "S2_literature": "S2. 文献与证据地图",
    "S3_baseline_formula": "S3. 基准配方与物料",
    "S4_doe": "S4. DOE 设计与结果摘要",
    "S5_lab_ledger": "S5. 实验台账与测量",
    "S6_optimize_loop": "S6. 寻优、闭环与模型态",
    "S7_artifacts": "S7. 图表与多模态资产",
    "S8_open_questions": "S8. 开放问题 / Flag / 下一步",
}

_EVENT_SECTIONS: dict[str, list[str]] = {
    "project_updated": ["S1_requirements", "S3_baseline_formula", "S8_open_questions"],
    "literature_ingested": ["S2_literature", "S8_open_questions"],
    "doe_updated": ["S4_doe", "S8_open_questions"],
    "lab_recorded": ["S4_doe", "S5_lab_ledger", "S8_open_questions"],
    "loop_updated": ["S6_optimize_loop", "S7_artifacts", "S8_open_questions"],
    "optimize_completed": ["S6_optimize_loop", "S7_artifacts", "S8_open_questions"],
    "attachment_uploaded": ["S7_artifacts", "S5_lab_ledger", "S8_open_questions"],
}


def _require_dossier_enabled() -> None:
    settings = get_settings()
    if not settings.wiki_enabled:
        raise PermissionError("wiki_enabled is false")
    if not getattr(settings, "wiki_project_dossier_enabled", False):
        raise PermissionError("wiki_project_dossier_enabled is false")


def normalize_section(section: str) -> str:
    key = (section or "").strip()
    canon = _SECTION_ALIASES.get(key) or _SECTION_ALIASES.get(key.upper())
    if not canon:
        raise ValueError(f"unknown dossier section: {section}")
    return canon


def normalize_sections(sections: list[str] | None) -> list[str]:
    if not sections:
        return list(DOSSIER_SECTIONS)
    out: list[str] = []
    seen: set[str] = set()
    for s in sections:
        c = normalize_section(s)
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join(["---"] * len(headers)) + " |"
    if not rows:
        empty = "| " + " | ".join(["—"] * len(headers)) + " |"
        return "\n".join([head, sep, empty])

    def cell(c: Any) -> str:
        return "" if c is None else str(c).replace("|", "\\|").replace("\n", " ")

    body = ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def render_section(section: str, pack: dict[str, Any]) -> str:
    """Render one section body (no ## heading). Deterministic tables only."""
    key = normalize_section(section)
    if key == "S1_requirements":
        req_rows = pack.get("requirements", {}).get("rows") or []
        table = _md_table(
            ["指标 / 约束", "目标值", "单位", "方向", "来源"],
            [
                [r.get("metric"), r.get("value"), r.get("unit"), r.get("direction"), r.get("source")]
                for r in req_rows
            ],
        )
        return (
            f"<!-- data:requirements -->\n{table}"
        )
    if key == "S2_literature":
        lit_rows = pack.get("literature", {}).get("rows") or []
        table = _md_table(
            ["主题簇", "代表来源", "source_id", "要点", "Wiki/L1"],
            [
                [
                    r.get("cluster"),
                    r.get("title"),
                    r.get("source_id"),
                    r.get("snippet"),
                    r.get("l1"),
                ]
                for r in lit_rows
            ],
        )
        return f"<!-- data:literature -->\n{table}"
    if key == "S3_baseline_formula":
        formula_rows = pack.get("formula", {}).get("rows") or []
        table = _md_table(
            ["组分", "角色", "Wt%", "CAS/牌号", "来源"],
            [
                [r.get("name"), r.get("role"), r.get("weight_pct"), r.get("cas"), r.get("source")]
                for r in formula_rows
            ],
        )
        return f"<!-- data:formula -->\n{table}"
    if key == "S4_doe":
        plans = pack.get("doe", {}).get("plans") or []
        plan_rows = [
            [
                p.get("round"),
                p.get("design_type"),
                len(p.get("factors") or []) if isinstance(p.get("factors"), list) else "",
                (p.get("bounds") or "")[:120],
                p.get("plan_id"),
                p.get("notes"),
            ]
            for p in plans
            if isinstance(p, dict)
        ]
        plan_table = _md_table(
            ["轮次", "design_type", "因子", "边界", "计划 id", "备注"],
            plan_rows,
        )
        runs = pack.get("doe", {}).get("runs") or []
        run_rows = [
            [
                r.get("run"),
                json.dumps(r.get("factors") or {}, ensure_ascii=False)[:80],
                r.get("value"),
                r.get("method"),
                r.get("passed"),
            ]
            for r in runs[:40]
            if isinstance(r, dict)
        ]
        run_table = _md_table(
            ["试验 / run", "关键因子取值", "主指标实测", "方法", "通过?"],
            run_rows,
        )
        return f"<!-- data:doe -->\n{plan_table}\n\n{run_table}"
    if key == "S5_lab_ledger":
        lab_rows = pack.get("lab", {}).get("rows") or []
        table = _md_table(
            ["时间", "样本/item", "计划参数", "实际参数", "测量指标", "值", "方法", "附件"],
            [
                [
                    (r.get("at") or "")[:19],
                    r.get("item"),
                    r.get("planned"),
                    r.get("actual"),
                    r.get("metric"),
                    r.get("value"),
                    r.get("method"),
                    r.get("attachment"),
                ]
                for r in lab_rows
                if isinstance(r, dict)
            ],
        )
        return f"<!-- data:lab -->\n{table}"
    if key == "S6_optimize_loop":
        loop = pack.get("loop") or {}
        hist_rows: list[list[Any]] = []
        for i, h in enumerate((loop.get("history") or [])[:20]):
            if not isinstance(h, dict):
                continue
            rmse = h.get("rmse_by_metric") or h.get("rmse") or h.get("summary")
            if isinstance(rmse, (dict, list)):
                rmse = json.dumps(rmse, ensure_ascii=False)[:80]
            hist_rows.append(
                [
                    i + 1,
                    h.get("at") or h.get("ts") or "",
                    rmse if rmse is not None else "",
                    h.get("converged") if h.get("converged") is not None else "",
                    h.get("doe_plan_id") or "",
                    (h.get("note") or h.get("notes") or "")[:40],
                ]
            )
        hist_table = _md_table(
            ["闭环轮次", "时刻", "rmse/摘要", "converged", "doe_plan_id", "备注"],
            hist_rows,
        )
        cand_rows = [
            [
                c.get("name"),
                json.dumps(c.get("predicted") or {}, ensure_ascii=False)[:60],
                c.get("score"),
                "",
            ]
            for c in (loop.get("candidates") or [])[:10]
            if isinstance(c, dict)
        ]
        cand_table = _md_table(
            ["候选配方", "预测主指标", "分数", "版本/快照"],
            cand_rows,
        )
        return f"<!-- data:loop -->\n{hist_table}\n\n{cand_table}"
    if key == "S7_artifacts":
        art_rows = (pack.get("artifacts") or {}).get("rows") or []
        table = _md_table(
            ["资产", "类型", "路径/URI", "关联节", "生成自"],
            [
                [r.get("name"), r.get("kind"), r.get("uri"), r.get("section"), r.get("from")]
                for r in art_rows
                if isinstance(r, dict)
            ],
        )
        return f"<!-- data:artifacts -->\n{table}"
    if key == "S8_open_questions":
        flags = pack.get("flags") or {}
        lines: list[str] = []
        if flags.get("missing_requirement"):
            lines.append("- Flag：缺少 Requirement。")
        if flags.get("empty_literature"):
            lines.append("- Flag：尚无文献 / source_ids。")
        if flags.get("empty_doe"):
            lines.append("- Flag：尚无 DOE 计划。")
        if flags.get("empty_lab"):
            lines.append("- Flag：尚无实验台账条目。")
        if flags.get("empty_loop"):
            lines.append("- Flag：尚无闭环 / 寻优历史。")
        if not lines:
            lines.append("- 主要切片已有数据；请人工 review 后勾选 reviewed。")
        lines.append("- 自动 patch 默认关闭（`wiki_dossier_auto_patch=false`）。")
        return "\n".join(lines)
    raise ValueError(f"unknown dossier section: {section}")


def _replace_section_body(markdown: str, section: str, new_body: str) -> str:
    key = normalize_section(section)
    title = _SECTION_TITLES[key]
    pattern = re.compile(
        rf"(## {re.escape(title)}\n)(.*?)(?=\n## S[1-8]\.|\Z)",
        re.DOTALL,
    )
    replacement = f"## {title}\n{new_body.strip()}\n\n"
    new_md, n = pattern.subn(lambda _m: replacement, markdown, count=1)
    if n == 0:
        raise ValueError(f"section anchor not found: {key}")
    return new_md


def _split_fm(markdown: str) -> tuple[str, str]:
    if markdown.startswith("---"):
        end = markdown.find("\n---", 3)
        if end > 0:
            return markdown[: end + 4], markdown[end + 4 :].lstrip("\n")
    return "", markdown


def compose_section_body(
    section: str,
    pack: dict[str, Any],
    *,
    use_llm: bool = False,
    previous_section_body: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Deterministic table + optional LLM narrative. Returns (body, narrative_meta)."""
    key = normalize_section(section)
    table = render_section(key, pack)
    prev = extract_narrative_from_section(previous_section_body or "")
    # S8 flags list is itself the body; optional narrative appends after.
    if key == "S8_open_questions":
        if use_llm and narrative_enabled():
            narr, meta = generate_section_narrative(
                key, table_md=table, pack=pack, previous_narrative=prev
            )
            if meta.get("used_llm") and narr:
                return attach_narrative(table, narr), meta
            return table, meta
        return table, None

    if use_llm and narrative_enabled():
        narr, meta = generate_section_narrative(
            key, table_md=table, pack=pack, previous_narrative=prev
        )
        if meta.get("used_llm") and narr:
            return attach_narrative(table, narr), meta
        # failure: keep previous narrative if any, else placeholder
        return attach_narrative(table, prev or _NARRATIVE_PLACEHOLDER), meta

    return attach_narrative(table, prev or _NARRATIVE_PLACEHOLDER), None


def _extract_section_body(markdown: str, section: str) -> str:
    key = normalize_section(section)
    title = _SECTION_TITLES[key]
    pattern = re.compile(
        rf"## {re.escape(title)}\n(.*?)(?=\n## S[1-8]\.|\Z)",
        re.DOTALL,
    )
    m = pattern.search(markdown)
    return (m.group(1) if m else "").strip()


def _build_body(
    title: str,
    project_id: str,
    pack: dict[str, Any],
    *,
    use_llm: bool = False,
    previous_markdown: str | None = None,
) -> tuple[str, dict[str, Any]]:
    domain = pack.get("domain") or "—"
    substrate = (pack.get("requirements") or {}).get("substrate") or "—"
    campaign = pack.get("campaign_id") or "—"
    parts = [
        f"# [[{title}]]",
        "",
        f"> **领域**：{domain} · **基材**：{substrate} · **状态**：进行中",
        (
            f"> **卷宗**：Project Dossier · `project_id`=`{project_id}` · "
            f"campaign=`{campaign}` · 截止 `{pack.get('updated_at')}`"
        ),
        "",
    ]
    narrative_meta: dict[str, Any] = {}
    for sid in DOSSIER_SECTIONS:
        prev_body = _extract_section_body(previous_markdown or "", sid) if previous_markdown else None
        body, meta = compose_section_body(sid, pack, use_llm=use_llm, previous_section_body=prev_body)
        if meta:
            narrative_meta[sid] = meta
        parts.append(f"## {_SECTION_TITLES[sid]}")
        parts.append(body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n", narrative_meta


def _render_full_markdown(
    *,
    title: str,
    project_id: str,
    pack: dict[str, Any],
    section_revisions: dict[str, int],
    section_hashes: dict[str, str] | None = None,
    use_llm: bool = False,
    previous_markdown: str | None = None,
    llm_generated: bool = False,
) -> tuple[str, dict[str, Any]]:
    body, narrative_meta = _build_body(
        title,
        project_id,
        pack,
        use_llm=use_llm,
        previous_markdown=previous_markdown,
    )
    extra = {
        "template": TEMPLATE,
        "schema_version": 1,
        "llm_generated": llm_generated or any(
            (m or {}).get("used_llm") for m in narrative_meta.values()
        ),
        "reviewed": False,
        "project_id": project_id,
        "campaign_id": pack.get("campaign_id") or "",
        "domain": pack.get("domain") or "",
        "section_revisions": section_revisions,
        "section_hashes": section_hashes or {},
        "l1_paths": [],
        "doe_plan_ids": [
            p.get("plan_id")
            for p in (pack.get("doe") or {}).get("plans") or []
            if isinstance(p, dict) and p.get("plan_id")
        ],
        "experiment_ids": [
            r.get("source")
            for r in (pack.get("lab") or {}).get("rows") or []
            if isinstance(r, dict) and r.get("source")
        ],
        "artifact_ids": [
            r.get("name")
            for r in (pack.get("artifacts") or {}).get("rows") or []
            if isinstance(r, dict) and r.get("name")
        ],
        "vertical_addendum": pack.get("vertical_addendum") or "",
    }
    stub = dump_page(
        kind="theme",
        title=title,
        entity_id=f"theme:project:{safe_key(project_id)}"[:64],
        norm_key=safe_key(project_id),
        source_ids=list(pack.get("literature", {}).get("source_ids") or []),
        flags=["unreviewed", "dossier"],
        summary="Project dossier",
        evidence_blocks=[],
        extra_meta=extra,
    )
    if stub.startswith("---"):
        end = stub.find("\n---", 3)
        if end > 0:
            return stub[: end + 4] + "\n\n" + body.strip() + "\n", narrative_meta
    return body, narrative_meta


def _write_data_json(root: Path, rel: str, payload: dict[str, Any]) -> None:
    path = (root / rel).resolve()
    if not str(path).startswith(str(root.resolve())):
        raise ValueError(f"data path escapes wiki root: {rel}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_sidecar(store, project_id: str) -> dict[str, Any] | None:
    data_rel = project_dossier_data_path(project_id)
    abs_path = store.root() / data_rel
    if not abs_path.is_file():
        return None
    try:
        return json.loads(abs_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _persist_dossier(
    *,
    project_id: str,
    pack: dict[str, Any],
    markdown: str,
    section_revisions: dict[str, int],
    section_hashes: dict[str, str],
) -> dict[str, Any]:
    store = get_wiki_store()
    path = project_dossier_path(project_id)
    data_rel = project_dossier_data_path(project_id)
    title = pack.get("title") or project_id
    flags = ["unreviewed", "dossier"]
    if pack.get("flags", {}).get("missing_requirement"):
        flags.append("incomplete")

    row = store.upsert_page(
        path=path,
        kind="theme",
        title=str(title)[:512],
        norm_key=safe_key(project_id),
        entity_id=f"theme:project:{safe_key(project_id)}"[:64],
        markdown=markdown,
        source_ids=list(pack.get("literature", {}).get("source_ids") or []),
        flags=flags,
        replace_source_ids=True,
    )

    data_payload = {
        **pack,
        "path": path,
        "data_path": data_rel,
        "section_revisions": section_revisions,
        "section_hashes": section_hashes,
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
        "project_id": project_id,
        "template": TEMPLATE,
        "section_revisions": section_revisions,
        "section_hashes": section_hashes,
    }


def ensure_project_dossier(
    project_id: str,
    *,
    campaign_id: str | None = None,
    vertical: str | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Create or refresh full dossier tables from live pack. LLM narrative optional."""
    _require_dossier_enabled()
    pid = (project_id or "").strip()
    if not pid:
        return {"ok": False, "error": "project_id required"}

    pack = build_project_dossier_pack(pid, campaign_id=campaign_id)
    if vertical is not None:
        pack["vertical_addendum"] = (vertical or "").strip()
    elif not pack.get("vertical_addendum"):
        pack["vertical_addendum"] = (get_settings().wiki_dossier_vertical_addendum or "").strip()

    store = get_wiki_store()
    path = project_dossier_path(pid)
    previous_md = store.read_markdown(path) if store.get_by_path(path) else None

    section_revisions = {s: 1 for s in DOSSIER_SECTIONS}
    section_hashes = {s: _content_hash(render_section(s, pack)) for s in DOSSIER_SECTIONS}
    title = pack.get("title") or pid
    md, narrative_meta = _render_full_markdown(
        title=title,
        project_id=pid,
        pack=pack,
        section_revisions=section_revisions,
        section_hashes=section_hashes,
        use_llm=use_llm,
        previous_markdown=previous_md,
    )
    out = _persist_dossier(
        project_id=pid,
        pack=pack,
        markdown=md,
        section_revisions=section_revisions,
        section_hashes=section_hashes,
    )
    out["narrative"] = narrative_meta
    out["llm_narrative_requested"] = bool(use_llm)
    return out


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
            if isinstance(side.get("section_hashes"), dict):
                pack["section_hashes"] = side["section_hashes"]
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


def patch_dossier_sections(
    project_id: str,
    sections: list[str] | None = None,
    *,
    campaign_id: str | None = None,
    vertical: str | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Rebuild selected sections from live pack; bump revisions only when table hash changes."""
    _require_dossier_enabled()
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id required")

    wanted = normalize_sections(sections)
    store = get_wiki_store()
    path = project_dossier_path(pid)
    if store.get_by_path(path) is None:
        ensure_project_dossier(pid, campaign_id=campaign_id, vertical=vertical, use_llm=use_llm)

    pack = build_project_dossier_pack(pid, campaign_id=campaign_id)
    if vertical is not None:
        pack["vertical_addendum"] = (vertical or "").strip()
    elif not pack.get("vertical_addendum"):
        side0 = _read_sidecar(store, pid)
        if side0 and side0.get("vertical_addendum"):
            pack["vertical_addendum"] = side0["vertical_addendum"]
        else:
            pack["vertical_addendum"] = (get_settings().wiki_dossier_vertical_addendum or "").strip()

    side = _read_sidecar(store, pid) or {}
    section_revisions = {s: int((side.get("section_revisions") or {}).get(s) or 0) for s in DOSSIER_SECTIONS}
    section_hashes = {s: str((side.get("section_hashes") or {}).get(s) or "") for s in DOSSIER_SECTIONS}

    md = store.read_markdown(path) or ""
    if not md:
        title = pack.get("title") or pid
        md, _ = _render_full_markdown(
            title=title,
            project_id=pid,
            pack=pack,
            section_revisions=section_revisions,
            section_hashes=section_hashes,
            use_llm=use_llm,
        )

    _, body = _split_fm(md)
    patched: list[str] = []
    skipped: list[str] = []
    narrative_meta: dict[str, Any] = {}
    force_narrative = bool(use_llm and narrative_enabled())

    for sid in wanted:
        table = render_section(sid, pack)
        new_hash = _content_hash(table)
        table_changed = section_hashes.get(sid) != new_hash
        if not table_changed and not force_narrative:
            skipped.append(sid)
            continue
        prev_body = _extract_section_body(md, sid)
        new_body, meta = compose_section_body(
            sid,
            pack,
            use_llm=use_llm,
            previous_section_body=prev_body,
        )
        if meta:
            narrative_meta[sid] = meta
        body = _replace_section_body(body, sid, new_body)
        if table_changed:
            section_hashes[sid] = new_hash
            section_revisions[sid] = int(section_revisions.get(sid) or 0) + 1
            patched.append(sid)
        elif meta and meta.get("used_llm"):
            # Narrative-only refresh: bump revision lightly
            section_revisions[sid] = int(section_revisions.get(sid) or 0) + 1
            patched.append(sid)
        else:
            skipped.append(sid)

    title = pack.get("title") or pid
    fresh, _ = _render_full_markdown(
        title=title,
        project_id=pid,
        pack=pack,
        section_revisions=section_revisions,
        section_hashes=section_hashes,
        llm_generated=any((m or {}).get("used_llm") for m in narrative_meta.values()),
    )
    fresh_fm, _ = _split_fm(fresh)
    final_md = (fresh_fm + "\n\n" + body.strip() + "\n") if fresh_fm else body

    out = _persist_dossier(
        project_id=pid,
        pack=pack,
        markdown=final_md,
        section_revisions=section_revisions,
        section_hashes=section_hashes,
    )
    out["patched_sections"] = patched
    out["skipped_unchanged"] = skipped
    out["narrative"] = narrative_meta
    out["llm_narrative_requested"] = bool(use_llm)
    return out


def refresh_dossier(
    project_id: str,
    *,
    campaign_id: str | None = None,
    sections: list[str] | None = None,
    vertical: str | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Ensure dossier exists then patch sections (default: all)."""
    _require_dossier_enabled()
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id required")
    store = get_wiki_store()
    if store.get_by_path(project_dossier_path(pid)) is None:
        ensure_project_dossier(pid, campaign_id=campaign_id, vertical=vertical, use_llm=use_llm)
    return patch_dossier_sections(
        pid,
        sections,
        campaign_id=campaign_id,
        vertical=vertical,
        use_llm=use_llm,
    )


def notify_dossier_event(
    project_id: str,
    event: str,
    *,
    campaign_id: str | None = None,
    sections: list[str] | None = None,
) -> dict[str, Any]:
    """
    Event hook for auto patch. No-op unless ``wiki_dossier_auto_patch`` is true.
    Safe to call from project update / ingest / DOE / lab / loop paths.
    """
    settings = get_settings()
    if not settings.wiki_enabled:
        return {"ok": False, "skipped": True, "reason": "wiki_disabled", "event": event}
    if not getattr(settings, "wiki_project_dossier_enabled", False):
        return {"ok": False, "skipped": True, "reason": "dossier_disabled", "event": event}
    if not getattr(settings, "wiki_dossier_auto_patch", False):
        return {"ok": True, "skipped": True, "reason": "auto_patch_off", "event": event}

    secs = sections or _EVENT_SECTIONS.get(event) or list(DOSSIER_SECTIONS)
    try:
        out = refresh_dossier(project_id, campaign_id=campaign_id, sections=secs)
        out["event"] = event
        out["skipped"] = False
        return out
    except Exception as exc:
        logger.warning("dossier auto_patch failed project=%s event=%s: %s", project_id, event, exc)
        return {"ok": False, "error": str(exc), "event": event}


def notify_dossier_event_for_campaign(
    campaign_id: int | str | None,
    event: str,
    *,
    sections: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve campaign → project_id then notify. Best-effort; never raises."""
    if campaign_id is None or campaign_id == "":
        return {"ok": False, "skipped": True, "reason": "no_campaign", "event": event}
    try:
        from ...db.campaign_store import get_campaign_store

        camp = get_campaign_store().get_campaign_sync(int(campaign_id))
        pid = getattr(camp, "project_id", None) if camp is not None else None
        if not pid:
            return {"ok": False, "skipped": True, "reason": "no_project", "event": event}
        return notify_dossier_event(
            str(pid),
            event,
            campaign_id=str(campaign_id),
            sections=sections,
        )
    except Exception as exc:
        logger.debug("dossier campaign notify failed: %s", exc)
        return {"ok": False, "skipped": True, "reason": str(exc), "event": event}


def notify_dossier_event_for_experiment(
    experiment_id: int | str | None,
    event: str,
    *,
    sections: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve experiment → project_id then notify. Best-effort; never raises."""
    if experiment_id is None or experiment_id == "":
        return {"ok": False, "skipped": True, "reason": "no_experiment", "event": event}
    try:
        from ...db.database import default_session_factory
        from ...db.models import ExperimentRow

        with default_session_factory()() as session:
            exp = session.get(ExperimentRow, int(experiment_id))
            pid = (getattr(exp, "project_id", None) or "").strip() if exp is not None else ""
        if not pid:
            return {"ok": False, "skipped": True, "reason": "no_project", "event": event}
        return notify_dossier_event(pid, event, sections=sections)
    except Exception as exc:
        logger.debug("dossier experiment notify failed: %s", exc)
        return {"ok": False, "skipped": True, "reason": str(exc), "event": event}
