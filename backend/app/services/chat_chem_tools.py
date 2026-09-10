"""Chat-native OpenAI tool schemas + execution over chemtools / SureChemBL / OCSR.

Deterministic chemistry tools for the chat tool loop. No ChemCrow / JSON-in-prompt.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

TOOL_LABELS: dict[str, str] = {
    "recognize_structure": "结构识别中",
    "mol_descriptors": "计算分子描述符",
    "chemical_profile": "查询化学档案",
    "surechembl_lookup": "SureChemBL 鉴定",
    "surechembl_similar": "SureChemBL 相似结构",
    "surechembl_search": "SureChemBL 文献检索",
    "substructure_search": "子结构检索",
    "scaffold_substitutes": "骨架替代",
}


@dataclass
class ChatToolContext:
    allowed_image_refs: frozenset[str]
    structure: dict[str, Any] | None
    settings: Any
    attachment_source_ids: list[str] = field(default_factory=list)


def build_tool_context(
    *,
    structure: dict[str, Any] | None,
    attachment_source_ids: list[str],
    settings: Any,
) -> ChatToolContext:
    refs: set[str] = set()
    if structure:
        sha = structure.get("image_sha")
        if isinstance(sha, str) and sha.strip():
            refs.add(sha.strip())
    for aid in attachment_source_ids or []:
        s = (aid or "").strip()
        if s:
            refs.add(s)
    return ChatToolContext(
        allowed_image_refs=frozenset(refs),
        structure=structure,
        settings=settings,
        attachment_source_ids=list(attachment_source_ids or []),
    )


def should_enable_tools(provider: str, settings: Any) -> bool:
    from .llm import _OPENAI_COMPAT_PROVIDERS

    if not getattr(settings, "chemtools_enabled", False):
        return False
    if not getattr(settings, "chat_chem_tools_enabled", False):
        return False
    return (provider or "").strip().lower() in _OPENAI_COMPAT_PROVIDERS


def _fn(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        },
    }


def openai_tool_schemas(ctx: ChatToolContext) -> list[dict]:
    tools = [
        _fn(
            "name_to_smiles",
            "Resolve a chemical name to SMILES via PubChem.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        _fn(
            "name_to_cas",
            "Resolve a chemical name to a CAS number via PubChem.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        _fn(
            "func_groups",
            "List functional groups present in a SMILES (local RDKit).",
            {"smiles": {"type": "string"}},
            ["smiles"],
        ),
        _fn(
            "mol_descriptors",
            "Compute RDKit descriptors (mol_wt, logp, tpsa, hbd, hba, arom_rings).",
            {"smiles": {"type": "string"}},
            ["smiles"],
        ),
        _fn(
            "mol_similarity",
            "Tanimoto similarity between two SMILES.",
            {
                "smiles_a": {"type": "string"},
                "smiles_b": {"type": "string"},
            },
            ["smiles_a", "smiles_b"],
        ),
        _fn(
            "synthetic_accessibility",
            "Synthetic accessibility score for a SMILES.",
            {"smiles": {"type": "string"}},
            ["smiles"],
        ),
        _fn(
            "patent_check",
            "Molecular patent pre-screen (molbloom) for a SMILES.",
            {"smiles": {"type": "string"}},
            ["smiles"],
        ),
        _fn(
            "explosive_check",
            "Explosive / GHS screen for a CAS number.",
            {"cas": {"type": "string"}},
            ["cas"],
        ),
        _fn(
            "safety_flags",
            "Combined controlled/explosive safety flags for smiles and/or CAS.",
            {
                "smiles": {"type": "string"},
                "cas": {"type": "string"},
            },
        ),
        _fn(
            "chemical_profile",
            "Full chemical dossier: lookup + groups + patent + SA + safety.",
            {"q": {"type": "string", "description": "Name, CAS, or SMILES"}},
            ["q"],
        ),
        _fn(
            "validate_smiles",
            "Validate SMILES with RDKit and return canonical form / MolJSON meta.",
            {"smiles": {"type": "string"}},
            ["smiles"],
        ),
        _fn(
            "substructure_search",
            "Search materials library by SMARTS substructure.",
            {
                "smarts": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            ["smarts"],
        ),
        _fn(
            "scaffold_substitutes",
            "Find materials sharing the Murcko scaffold of a SMILES.",
            {
                "smiles": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            ["smiles"],
        ),
        _fn(
            "surechembl_lookup",
            "Identify a chemical via SureChEMBL (name or SMILES).",
            {"q": {"type": "string"}},
            ["q"],
        ),
        _fn(
            "surechembl_similar",
            "SureChEMBL structure-similar alternatives for a SMILES.",
            {
                "smiles": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            ["smiles"],
        ),
        _fn(
            "surechembl_search",
            "SureChEMBL patent/document keyword search (short result list).",
            {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            ["query"],
        ),
    ]
    if ctx.allowed_image_refs:
        tools.append(
            _fn(
                "recognize_structure",
                "Recognize a structure drawing already uploaded this turn. "
                "image_ref must be one of the allowlisted sha/ids provided in the system message.",
                {"image_ref": {"type": "string"}},
                ["image_ref"],
            )
        )
    return tools


def _ok(**payload: Any) -> dict[str, Any]:
    out = {"ok": True}
    out.update(payload)
    return out


def _fail(hint: str) -> dict[str, Any]:
    return {"ok": False, "hint": hint}


def _load_image_bytes_for_ref(ref: str, ctx: ChatToolContext) -> bytes | None:
    """Resolve allowlisted image_ref to bytes. Paths/URLs must never be accepted."""
    ref = (ref or "").strip()
    if not ref or ref not in ctx.allowed_image_refs:
        return None
    # Reject path-like or URL-like refs even if somehow allowlisted.
    if any(x in ref for x in ("/", "\\", "..", ":", "http")):
        # sha hex is ok (no slash); Windows drive paths have ':' — reject those
        if ":" in ref or "/" in ref or "\\" in ref or ".." in ref or ref.lower().startswith("http"):
            # Allow pure hex sha without path separators
            if not all(c in "0123456789abcdefABCDEF" for c in ref):
                return None

    try:
        from . import structure_recognize as sr

        shared = sr._shared_dir()
        # Uploaded files use struct_{sha[:12]}.png
        candidates = [
            os.path.join(shared, f"struct_{ref[:12]}.png"),
            os.path.join(shared, f"struct_{ref}.png"),
            os.path.join(shared, f"{ref}.png"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    return fh.read()
        # Redis/file cache only stores recognition JSON, not bytes — miss → None
    except Exception as exc:
        logger.debug("load image_ref failed: %s", exc)
    return None


def execute_tool(name: str, args: dict[str, Any], ctx: ChatToolContext) -> dict[str, Any]:
    args = args or {}
    try:
        return _dispatch(name, args, ctx)
    except Exception as exc:
        logger.warning("chat chem tool %s failed: %s", name, exc)
        return _fail(str(exc)[:200])


def _dispatch(name: str, args: dict[str, Any], ctx: ChatToolContext) -> dict[str, Any]:
    from . import chemtools

    if name == "name_to_smiles":
        smiles = chemtools.name_to_smiles(str(args.get("name") or ""))
        return _ok(smiles=smiles) if smiles else _fail("无法解析名称→SMILES")

    if name == "name_to_cas":
        cas = chemtools.name_to_cas(str(args.get("name") or ""))
        return _ok(cas=cas) if cas else _fail("无法解析名称→CAS")

    if name == "func_groups":
        groups = chemtools.func_groups(str(args.get("smiles") or ""))
        return _ok(func_groups=groups)

    if name == "mol_descriptors":
        props = chemtools.mol_descriptors(str(args.get("smiles") or ""))
        return _ok(properties=props) if props else _fail("描述符计算失败（无效 SMILES 或未安装 RDKit）")

    if name == "mol_similarity":
        sim = chemtools.mol_similarity(
            str(args.get("smiles_a") or ""),
            str(args.get("smiles_b") or ""),
        )
        return _ok(similarity=sim) if sim is not None else _fail("相似度计算失败")

    if name == "synthetic_accessibility":
        sa = chemtools.synthetic_accessibility(str(args.get("smiles") or ""))
        return _ok(**sa) if sa else _fail("合成可及性计算失败")

    if name == "patent_check":
        hit = chemtools.patent_check(str(args.get("smiles") or ""))
        return _ok(patented=hit)

    if name == "explosive_check":
        hit = chemtools.explosive_check(str(args.get("cas") or ""))
        return _ok(explosive=hit)

    if name == "safety_flags":
        flags = chemtools.safety_flags(
            args.get("smiles") or None,
            args.get("cas") or None,
        )
        return _ok(safety=flags)

    if name == "chemical_profile":
        profile = chemtools.chemical_profile(str(args.get("q") or ""))
        return _ok(profile=profile)

    if name == "validate_smiles":
        from .moljson import validate_smiles

        result = validate_smiles(str(args.get("smiles") or ""))
        return _ok(**result) if isinstance(result, dict) else _fail("SMILES 校验失败")

    if name == "substructure_search":
        from .structure_search import substructure_hits

        top_k = int(args.get("top_k") or 10)
        top_k = max(1, min(top_k, 20))
        hits = substructure_hits(str(args.get("smarts") or ""), top_k=top_k)
        return _ok(hits=(hits or [])[:top_k])

    if name == "scaffold_substitutes":
        from .structure_search import scaffold_substitutes

        top_k = int(args.get("top_k") or 10)
        top_k = max(1, min(top_k, 20))
        hits = scaffold_substitutes(str(args.get("smiles") or ""), top_k=top_k)
        return _ok(hits=(hits or [])[:top_k])

    if name == "surechembl_lookup":
        from .surechembl_lookup import lookup_surechembl

        hit = lookup_surechembl(str(args.get("q") or ""))
        return _ok(result=hit) if hit else _fail("SureChemBL 未命中或未启用")

    if name == "surechembl_similar":
        from .surechembl_alternatives import fetch_surechembl_alternatives

        top_k = int(args.get("top_k") or 5)
        top_k = max(1, min(top_k, 10))
        smiles = str(args.get("smiles") or "")
        payload = fetch_surechembl_alternatives(
            material=smiles,
            smiles=smiles,
            limit=top_k,
        )
        rows = (payload or {}).get("surechembl") or []
        return _ok(alternatives=rows[:top_k], meta=(payload or {}).get("surechembl_meta"))

    if name == "surechembl_search":
        from .literature import search_surechembl_content

        limit = int(args.get("limit") or 5)
        limit = max(1, min(limit, 5))
        evidence = search_surechembl_content(str(args.get("query") or ""), limit=limit) or []
        slim = []
        for ev in evidence[:limit]:
            slim.append(
                {
                    "title": getattr(ev, "title", None) or (ev.get("title") if isinstance(ev, dict) else None),
                    "identifier": getattr(ev, "identifier", None)
                    or (ev.get("identifier") if isinstance(ev, dict) else None),
                    "url": getattr(ev, "url", None) or (ev.get("url") if isinstance(ev, dict) else None),
                }
            )
        return _ok(results=slim)

    if name == "recognize_structure":
        image_ref = str(args.get("image_ref") or "").strip()
        if not image_ref or image_ref not in ctx.allowed_image_refs:
            return _fail("image_ref 不在本轮白名单内")
        if any(tok in image_ref for tok in ("/", "\\", "..")) or image_ref.lower().startswith("http"):
            return _fail("非法 image_ref（禁止路径/URL）")
        if ":" in image_ref and not all(c in "0123456789abcdefABCDEF" for c in image_ref.replace(":", "")):
            # drive paths like C:/...
            return _fail("非法 image_ref（禁止路径/URL）")
        if ":" in image_ref:
            return _fail("非法 image_ref（禁止路径/URL）")

        data = _load_image_bytes_for_ref(image_ref, ctx)
        if not data:
            # Still allow mock/tests that patch _load_image_bytes_for_ref
            return _fail("无法读取结构图附件")

        from .structure_recognize import recognize_structure_image

        result = recognize_structure_image(data, filename=f"{image_ref[:12]}.png")
        if not result or not result.get("recognized"):
            return _fail(result.get("error") or result.get("warnings") or "结构识别失败")
        return _ok(
            smiles=result.get("smiles"),
            image_sha=result.get("image_sha"),
            hits=(result.get("hits") or [])[:5],
            warnings=result.get("warnings") or [],
        )

    return _fail(f"未知工具: {name}")


def summarize_tool_result(name: str, result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"{name} 失败: {result.get('hint') or 'unknown'}"
    if name == "mol_descriptors":
        props = result.get("properties") or {}
        return f"MW={props.get('mol_wt')}, LogP={props.get('logp')}, TPSA={props.get('tpsa')}"
    if name == "recognize_structure":
        return f"识别 SMILES={result.get('smiles')}"
    if name == "func_groups":
        groups = result.get("func_groups") or []
        return "官能团: " + (", ".join(groups[:8]) if groups else "无")
    if name == "chemical_profile":
        return "已返回化学档案"
    if name == "surechembl_search":
        n = len(result.get("results") or [])
        return f"SureChemBL 命中 {n} 条"
    if name == "surechembl_similar":
        n = len(result.get("alternatives") or [])
        return f"相似结构 {n} 条"
    if name == "name_to_smiles":
        return f"SMILES={result.get('smiles')}"
    return f"{name} 完成"
