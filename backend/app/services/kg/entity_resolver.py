"""Query → entity resolution and retrieval mode detection."""
from __future__ import annotations

import re

from ...config import Settings, get_settings
from ...db.entity_store import get_entity_store
from ...db.product_store import norm_key
from ...domain.kg_schemas import (
    EntityResolveResponse,
    KGChemicalEntity,
    KGRelationView,
    KGTradeProductEntity,
    RetrievalMode,
)
from ...domain.knowledge import RAW_MATERIALS, TRADE_ALIASES, resolve_material_name
from .element_map import load_element_map

_ENUMERATIVE_RE = re.compile(
    r"所有|全部|列举|有哪些|哪些.*文献|含.*的|牌号.*有哪些|"
    r"盘点|综述|汇总|一览|清单|筛出|什么牌号|哪些厂|哪些家|"
    r"list all|all formulations",
    re.IGNORECASE,
)
# 2026-09-04 (R3): 高歧义词("总结/整理/对比"——"总结这个配方性能"是语义
# 分析非列举)不进正则, 留给 _llm_intent_mode 判; 词表只收强列举词, 避免
# 误伤 semantic 问法。
_LLM_INTENT_MIN_LEN = 12
_LLM_INTENT_MODES = frozenset({"enumerative", "semantic", "hybrid"})


def _llm_intent_mode(query: str) -> str | None:
    """正则未决的长句 → LLM 结构化意图分类(3s 硬超时, 失败静默)。

    Returns one of ``_LLM_INTENT_MODES``, or None when the query is too
    short / LLM 未配置 / 超时 / 回复不可解析 —— 调用方保持现有 auto 判定,
    绝不比现状更差。只在 resolve_query 的正则未决分支触发, 高频简单问法
    零 LLM 开销。
    """
    q = (query or "").strip()
    if len(q) < _LLM_INTENT_MIN_LEN:
        return None
    prompt = (
        "你是配方知识图谱的查询意图分类器。判断用户提问的检索意图, 只返回 JSON 对象: "
        '{"mode": "enumerative" 或 "semantic" 或 "hybrid"}。'
        "enumerative = 要求列举/罗列实体(如 盘点近年综述 / 有哪些牌号 / 所有 XX); "
        "semantic = 按语义检索相关配方或材料(如 XX 怎么改善耐盐雾); "
        "hybrid = 提问含明确物质/牌号且要求关联分析。"
        f"\n问题: {q}"
    )
    try:
        from .. import llm as _llm

        raw = _llm._call_with_deadline(lambda: _llm._call_llm(prompt), 3.0)
        if not raw:
            return None
        import json

        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else None
        mode = str((data or {}).get("mode", "")).strip().lower()
        return mode if mode in _LLM_INTENT_MODES else None
    except Exception:
        return None
_CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")
_ELEMENT_RE = re.compile(r"\b([A-Z][a-z]?)\b")


def detect_mode(query: str) -> RetrievalMode:
    if _ENUMERATIVE_RE.search(query or ""):
        return "enumerative"
    return "auto"


def resolve_query(query: str, *, settings: Settings | None = None) -> EntityResolveResponse:
    settings = settings or get_settings()
    q = (query or "").strip()
    mode = detect_mode(q)
    if mode == "auto":
        # R3: 正则未决的长句 → LLM 结构化意图(3s 硬超时); 未决保持现状。
        llm_mode = _llm_intent_mode(q)
        if llm_mode:
            mode = llm_mode
        elif _CAS_RE.search(q) or _looks_like_trade(q):
            mode = "hybrid"
        else:
            mode = "semantic"

    chemicals: list[KGChemicalEntity] = []
    trade_products: list[KGTradeProductEntity] = []
    expanded_ids: list[str] = []
    seen: set[str] = set()

    store = get_entity_store()

    for cas in _CAS_RE.findall(q):
        eid = f"chem:cas:{cas}"
        row = store.get_entity(eid)
        if row:
            chemicals.append(_to_chemical(row))
            seen.add(eid)
        else:
            chemicals.append(
                KGChemicalEntity(
                    id=eid,
                    canonical_name=cas,
                    cas_no=cas,
                    composition_status="resolved",
                )
            )
            seen.add(eid)

    for token in q.replace(",", " ").split():
        token = token.strip()
        if not token or len(token) < 2:
            continue
        nk = norm_key(token, "")
        tp_id = f"tp:{nk}"
        if tp_id not in seen:
            row = store.get_entity(tp_id)
            if row:
                trade_products.append(_to_trade(row))
                seen.add(tp_id)
            elif token in TRADE_ALIASES or _looks_like_trade(token):
                trade_products.append(
                    KGTradeProductEntity(
                        id=tp_id,
                        trade_name=token,
                        composition_status="unknown",
                    )
                )
                seen.add(tp_id)

        catalog = resolve_material_name(token)
        if catalog in RAW_MATERIALS:
            safe = re.sub(r"[^a-zA-Z0-9]+", "_", catalog.lower())[:80]
            cid = f"chem:catalog:{safe}"
            if cid not in seen:
                row = store.get_entity(cid)
                if row:
                    chemicals.append(_to_chemical(row))
                else:
                    spec = RAW_MATERIALS[catalog]
                    chemicals.append(
                        KGChemicalEntity(
                            id=cid,
                            canonical_name=catalog,
                            cas_no=spec.get("cas_no"),
                            formula=spec.get("formula"),
                            linked_catalog_key=catalog,
                            composition_status="resolved",
                        )
                    )
                seen.add(cid)

    _expand_elements(q, store, chemicals, trade_products, seen, settings)

    for row in store.search_entities(q, limit=15):
        if row.id in seen:
            continue
        if row.kind == "trade_product":
            trade_products.append(_to_trade(row))
        elif row.kind in ("chemical", "element"):
            chemicals.append(_to_chemical(row))
        seen.add(row.id)

    expanded_ids = list(seen)
    for eid in list(seen):
        for dst in store.linked_dst_ids(eid):
            if dst not in seen:
                expanded_ids.append(dst)
                row = store.get_entity(dst)
                if row and row.kind == "chemical":
                    chemicals.append(_to_chemical(row))

    trade_only = bool(trade_products) and not chemicals
    interpretation = f"mode={mode}; entities={len(expanded_ids)}"
    if trade_only:
        interpretation += "; trade_only"

    top_relations: list[KGRelationView] = []
    primary_id = (chemicals[0].id if chemicals else None) or (
        trade_products[0].id if trade_products else None
    )
    if primary_id:
        from .graph_query import get_entity_relations

        top_relations = get_entity_relations(primary_id, limit=8)

    return EntityResolveResponse(
        query=q,
        chemicals=chemicals,
        trade_products=trade_products,
        expanded_entity_ids=expanded_ids,
        top_relations=top_relations,
        mode=mode,
        trade_only=trade_only,
        interpretation=interpretation,
    )


def _looks_like_trade(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z]{2,}[- ]?\d{2,4}$", token)) or token in TRADE_ALIASES


def _expand_elements(
    q: str,
    store,
    chemicals: list[KGChemicalEntity],
    trade_products: list[KGTradeProductEntity],
    seen: set[str],
    settings: Settings,
) -> None:
    emap = load_element_map(settings.kg_element_map_path)
    tokens = set(_ELEMENT_RE.findall(q))
    for sym in tokens:
        if sym not in emap and len(sym) > 2:
            continue
        if sym not in emap:
            continue
        elem_id = f"elem:{sym.upper()}"
        if elem_id not in seen:
            seen.add(elem_id)
        entry = emap[sym]
        for cas in entry.get("cas") or []:
            eid = f"chem:cas:{cas}"
            if eid in seen:
                continue
            row = store.get_entity(eid)
            if row:
                chemicals.append(_to_chemical(row))
            else:
                chemicals.append(
                    KGChemicalEntity(id=eid, canonical_name=cas, cas_no=cas, composition_status="resolved")
                )
            seen.add(eid)
        for ck in entry.get("catalog_keys") or []:
            if ck not in RAW_MATERIALS:
                continue
            safe = re.sub(r"[^a-zA-Z0-9]+", "_", ck.lower())[:80]
            cid = f"chem:catalog:{safe}"
            if cid in seen:
                continue
            row = store.get_entity(cid)
            if row:
                chemicals.append(_to_chemical(row))
            else:
                spec = RAW_MATERIALS[ck]
                chemicals.append(
                    KGChemicalEntity(
                        id=cid,
                        canonical_name=ck,
                        cas_no=spec.get("cas_no"),
                        formula=spec.get("formula"),
                        linked_catalog_key=ck,
                        composition_status="resolved",
                    )
                )
            seen.add(cid)


def _to_chemical(row) -> KGChemicalEntity:
    return KGChemicalEntity(
        id=row.id,
        canonical_name=row.canonical_name,
        cas_no=row.cas_no,
        formula=row.formula,
        linked_catalog_key=row.linked_catalog_key,
        composition_status=row.composition_status or "resolved",
        mention_count=int(row.mention_count or 0),
    )


def _to_trade(row) -> KGTradeProductEntity:
    store = get_entity_store()
    linked = store.linked_dst_ids(row.id)
    return KGTradeProductEntity(
        id=row.id,
        trade_name=row.canonical_name,
        grade=row.grade or "",
        supplier=row.supplier or "",
        composition_status=row.composition_status or "unknown",
        proprietary=bool(row.proprietary),
        generic_name_hint=row.generic_name_hint or "",
        linked_chemical_ids=linked,
        mention_count=int(row.mention_count or 0),
    )
