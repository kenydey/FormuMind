"""Material substitution: what could replace this, and what would it cost me?

Two mechanisms existed before this and neither is usable as an answer. The
knowledge graph can surface a ``substitutes`` edge, but only if the semantic
layer is switched on and a regex matched the sentence — and it is
property-blind, so it will offer a replacement that misses your targets
entirely. The chemist agent has a four-entry hardcoded table covering exactly
one scenario (solvent-borne components in a waterborne system).

The differentiator here is the second signal: rather than reporting that two
molecules look alike, rebuild the formulation with the candidate swapped in
and report **what changes on every metric**. "Salt spray −8%, cost −22%,
VOC +5%" is actionable; "Tanimoto 0.81" is not.

Three signals are fused:

1. structural — substitute_group, functional_class, Hansen distance, and
   Tanimoto when RDKit is present
2. predicted property delta — the swap, re-simulated
3. literature evidence — knowledge-graph substitution edges with citations

Signal 2 carries a ``delta_confidence`` because its resolution depends on what
is installed; see ``_delta_confidence``.
"""
from __future__ import annotations

import math

from loguru import logger

from ..domain.genome import FormulationGenome
from ..domain.schemas import Requirement

# Structural score weights. substitute_group is a curated judgement that these
# materials are interchangeable, so it outranks every inferred signal.
_W_GROUP = 0.5
_W_CLASS = 0.25
_W_HANSEN = 0.15
_W_TANIMOTO = 0.10

# Hansen distance (MPa^0.5) beyond which two materials are treated as
# unrelated. ~8 is the usual "outside the solubility sphere" radius.
_HANSEN_LIMIT = 8.0


def hansen_distance(a: dict, b: dict) -> float | None:
    """Hansen solubility distance Ra = sqrt(4Δd² + Δp² + Δh²).

    The dispersion term is weighted ×4 by convention (Hansen's own fit). None
    when either material lacks parameters — most of the catalog does, so
    callers must treat this as an optional signal rather than a default.
    """
    try:
        dd = float(a["hansen_d"]) - float(b["hansen_d"])
        dp = float(a["hansen_p"]) - float(b["hansen_p"])
        dh = float(a["hansen_h"]) - float(b["hansen_h"])
    except (KeyError, TypeError, ValueError):
        return None
    return round(math.sqrt(4.0 * dd * dd + dp * dp + dh * dh), 4)


def _tanimoto(a: dict, b: dict) -> float | None:
    """Morgan/Tanimoto similarity, when the chemistry gateway can provide it."""
    from . import chemtools

    try:
        capable = chemtools.availability()["capabilities"]["mol_similarity"]["available"]
    except Exception:
        return None
    if not capable:
        return None
    smiles_a, smiles_b = a.get("smiles"), b.get("smiles")
    if not smiles_a or not smiles_b:
        return None
    return chemtools.mol_similarity(smiles_a, smiles_b)


def structural_score(a: dict, b: dict) -> tuple[float, dict]:
    """Similarity in [0, 1] plus the per-signal breakdown that produced it.

    Renormalised over the signals actually available, so a material with no
    Hansen data is not penalised relative to one that has it.
    """
    parts: dict[str, float] = {}
    total = 0.0
    weight = 0.0

    if a.get("substitute_group") and b.get("substitute_group"):
        hit = 1.0 if a["substitute_group"] == b["substitute_group"] else 0.0
        parts["substitute_group"] = hit
        total += _W_GROUP * hit
        weight += _W_GROUP

    if a.get("functional_class") and b.get("functional_class"):
        hit = 1.0 if a["functional_class"] == b["functional_class"] else 0.0
        parts["functional_class"] = hit
        total += _W_CLASS * hit
        weight += _W_CLASS

    distance = hansen_distance(a, b)
    if distance is not None:
        closeness = max(0.0, 1.0 - distance / _HANSEN_LIMIT)
        parts["hansen"] = round(closeness, 4)
        parts["hansen_distance"] = distance
        total += _W_HANSEN * closeness
        weight += _W_HANSEN

    tanimoto = _tanimoto(a, b)
    if tanimoto is not None:
        parts["tanimoto"] = tanimoto
        total += _W_TANIMOTO * tanimoto
        weight += _W_TANIMOTO

    return (round(total / weight, 4) if weight else 0.0), parts


def _delta_confidence(a: dict, b: dict) -> str:
    """How much resolution the predicted delta actually has.

    The mechanistic predictor sees material identity through role sums, the
    amine/epoxy ratio, and the price/VOC lookup. Molecular descriptors would
    add real chemical resolution, but they need RDKit and the v2 feature block.
    Without those, swapping two same-role materials moves cost and VOC and
    leaves the performance metrics untouched — so the report must not present
    an unchanged salt-spray figure as evidence the swap is performance-neutral.
    """
    from . import chemtools

    try:
        rdkit_ok = bool(chemtools.availability().get("rdkit_installed"))
    except Exception:
        rdkit_ok = False
    if rdkit_ok:
        return "high"
    if a.get("role") != b.get("role"):
        return "low"
    return "cost_only"


def _metric_deltas(before: dict, after: dict) -> dict[str, dict]:
    """Per-metric change. Metrics present on only one side are reported as
    appearing/disappearing rather than as a delta from zero — the predictor's
    key set is domain-dependent and conditionally sparse."""
    out: dict[str, dict] = {}
    for metric in sorted(set(before) | set(after)):
        old, new = before.get(metric), after.get(metric)
        if old is None or new is None:
            out[metric] = {"before": old, "after": new, "delta": None, "pct": None}
            continue
        delta = new - old
        pct = (delta / old * 100.0) if old else None
        out[metric] = {
            "before": round(old, 4),
            "after": round(new, 4),
            "delta": round(delta, 4),
            "pct": round(pct, 2) if pct is not None else None,
        }
    return out


def _requirement_fit(candidate: dict, req: Requirement | None) -> float:
    """Soft score: how well candidate deltas move toward requirement objectives.

    Higher is better. Missing objectives / deltas → 0 (no penalty).
    """
    if req is None:
        return 0.0
    objectives = list(getattr(req, "objectives", None) or [])
    if not objectives:
        # Domain heuristics when the caller did not declare objectives.
        domain = getattr(getattr(req, "domain", None), "value", None) or str(
            getattr(req, "domain", "") or ""
        )
        if domain == "degreaser":
            objectives = [
                type("O", (), {"metric": "cleaning_efficiency", "direction": "maximize"})(),
                type("O", (), {"metric": "cost_cny_per_kg", "direction": "minimize"})(),
            ]
        else:
            objectives = [
                type("O", (), {"metric": "salt_spray_hours", "direction": "maximize"})(),
                type("O", (), {"metric": "voc_gpl", "direction": "minimize"})(),
                type("O", (), {"metric": "cost_cny_per_kg", "direction": "minimize"})(),
            ]
        voc_limit = getattr(req, "voc_limit_gpl", None)
        if voc_limit is not None:
            # Prefer stronger VOC reduction when a limit is set.
            objectives = [
                type("O", (), {"metric": "voc_gpl", "direction": "minimize"})(),
                *objectives,
            ]

    deltas = candidate.get("deltas") or {}
    score = 0.0
    for obj in objectives:
        metric = getattr(obj, "metric", None) or ""
        direction = (getattr(obj, "direction", None) or "maximize").lower()
        pct = (deltas.get(metric) or {}).get("pct")
        if pct is None:
            continue
        try:
            pct_f = float(pct)
        except (TypeError, ValueError):
            continue
        if direction == "minimize":
            score += -pct_f
        else:
            score += pct_f
    return round(score, 4)


def _kg_evidence(original: str, candidate: str) -> list[dict]:
    """Literature-backed substitution edges, when the graph is enabled."""
    from .kg.retrieval import kg_enabled

    if not kg_enabled():
        return []
    try:
        import re

        from ..db.entity_store import get_entity_store
        from .kg.graph_query import discover_substitutes

        def _cid(name: str) -> str:
            return f"chem:catalog:{re.sub(r'[^a-zA-Z0-9]+', '_', name.lower())[:80]}"

        store = get_entity_store()
        entity_id = _cid(original)
        if store.get_entity(entity_id) is None:
            return []
        target = _cid(candidate)
        out: list[dict] = []
        for cand in discover_substitutes(entity_id, limit=25).substitutes:
            if cand.entity_id != target:
                continue
            for step in cand.path:
                for ev in step.relation.evidence or []:
                    out.append(
                        {
                            "source_id": ev.source_id,
                            "chunk_id": ev.chunk_id,
                            "sentence": ev.sentence,
                            "confidence": ev.confidence,
                        }
                    )
        return out[:5]
    except Exception as exc:
        logger.debug("substitution: KG evidence unavailable ({})", exc)
        return []


def find_substitutes(
    genome: FormulationGenome,
    slot_index: int,
    req: Requirement | None = None,
    *,
    limit: int = 10,
    include_unavailable: bool = False,
    include_external: bool = True,
    external_limit: int = 8,
    similarity_threshold: int = 85,
    include_literature: bool = True,
    literature_limit: int = 8,
    include_surechembl: bool = True,
    surechembl_limit: int = 8,
    include_llm: bool | None = None,
    llm_limit: int = 5,
) -> dict:
    """Rank replacements for one slot, each with its predicted property delta.

    Layers (four-layer funnel + SureChEMBL patents):
    - L1 catalog candidates with formula Δ (always)
    - L2 literature/KG/kb_products advisory list (``include_literature``)
    - L3 PubChem structure similars (``include_external``)
    - SureChEMBL structure similars + patent docs (``include_surechembl``)
    - L4 rules/LLM when L1 < 3 or ``include_llm`` forced (``include_llm``)
    """
    from ..domain import knowledge
    from ..pipeline import reconstruct
    from ..pipeline.workflow import _score_and_validate, process_for
    from .feasibility import check_formulation

    if not 0 <= slot_index < len(genome.slots):
        raise IndexError(f"slot index {slot_index} out of range")

    slot = genome.slots[slot_index]
    original = slot.material
    original_spec = dict(knowledge.RAW_MATERIALS.get(original) or {})
    original_in_catalog = bool(original_spec) or original in knowledge.RAW_MATERIALS
    target = req if req is not None else genome.domain
    process = process_for(req) if req is not None else {}

    base_form = _score_and_validate(
        reconstruct.formulation_from_genome(target, genome, strict=False),
        process,
        req,
        enrich_network=False,
    )
    base_metrics = dict(base_form.predicted)

    # Recall: same substitute_group, else same role. Group membership is the
    # curated signal, so it takes precedence over the coarser role match.
    # Uncatalogued originals (LLM recommend) have empty group → role pool.
    group = original_spec.get("substitute_group")
    role = original_spec.get("role") or slot.role or "additive"
    pool: list[str] = []
    for name, spec in knowledge.RAW_MATERIALS.items():
        if name == original:
            continue
        if not include_unavailable and spec.get("availability") == "discontinued":
            continue
        if group and spec.get("substitute_group") == group:
            pool.append(name)
        elif not group and spec.get("role") == role:
            pool.append(name)

    candidates: list[dict] = []
    for name in pool:
        spec = dict(knowledge.RAW_MATERIALS.get(name) or {})
        score, breakdown = structural_score(original_spec, spec)
        swapped = genome.with_material(slot_index, name)
        try:
            form = reconstruct.formulation_from_genome(target, swapped, strict=False)
        except Exception:
            continue
        try:
            verdict = check_formulation(form, req)
            form = _score_and_validate(form, process, req, enrich_network=False)
        except Exception as exc:
            logger.debug("substitution candidate {} skipped ({})", name, exc)
            continue
        cand = {
            "material": name,
            "zh_name": spec.get("zh_name"),
            "role": spec.get("role"),
            "functional_class": spec.get("functional_class"),
            "substitute_group": spec.get("substitute_group"),
            "availability": spec.get("availability", "in_stock"),
            "supplier": spec.get("supplier"),
            "structural_score": score,
            "structural_breakdown": breakdown,
            "deltas": _metric_deltas(base_metrics, form.predicted),
            "delta_confidence": _delta_confidence(original_spec, spec),
            "feasible": verdict.feasible,
            "blocking_reasons": [] if verdict.feasible else verdict.reasons,
            "evidence": _kg_evidence(original, name),
            "score_after": form.score,
            "source": "catalog",
        }
        cand["requirement_fit"] = _requirement_fit(cand, req)
        candidates.append(cand)

    # Feasible first; then requirement-fit; then structural / score.
    candidates.sort(
        key=lambda c: (
            not c["feasible"],
            -float(c.get("requirement_fit") or 0.0),
            -c["structural_score"],
            -(c["score_after"] or 0.0),
            c["material"],
        )
    )

    identity: dict
    external: list[dict] = []
    external_meta: dict
    layers_used: list[str] = ["catalog"]

    if include_external:
        from .external_alternatives import fetch_external_alternatives

        ext = fetch_external_alternatives(
            material=original,
            spec=original_spec,
            limit=external_limit,
            threshold=similarity_threshold,
            role_hint=str(role) if role else None,
        )
        identity = ext["identity"]
        external = list(ext.get("external") or [])
        external_meta = dict(ext.get("external_meta") or {})
        if external_meta.get("queried") and external:
            layers_used.append("external")
    else:
        from .external_alternatives import resolve_slot_identity

        identity = resolve_slot_identity(original, original_spec, network=False)
        external_meta = {
            "enabled": False,
            "queried": False,
            "count": 0,
            "skipped_reason": "include_external=false",
            "provider": "pubchem_fastsimilarity_2d",
        }

    literature: list[dict] = []
    literature_meta: dict
    if include_literature:
        try:
            from .literature_alternatives import fetch_literature_alternatives

            lit = fetch_literature_alternatives(
                material=original,
                cas_no=str(identity.get("cas_no") or original_spec.get("cas_no") or ""),
                role_hint=str(role) if role else None,
                limit=literature_limit,
            )
            literature = list(lit.get("literature") or [])
            literature_meta = dict(lit.get("literature_meta") or {})
            if literature:
                layers_used.append("literature")
        except Exception as exc:
            logger.warning("literature substitutes degraded ({})", exc)
            literature = []
            literature_meta = {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": f"literature_error:{exc}",
                "providers": [],
            }
    else:
        literature_meta = {
            "enabled": False,
            "queried": False,
            "count": 0,
            "skipped_reason": "include_literature=false",
            "providers": [],
        }

    surechembl_rows: list[dict] = []
    surechembl_meta: dict
    if include_surechembl:
        try:
            from .surechembl_alternatives import fetch_surechembl_alternatives

            smi = str(identity.get("smiles") or original_spec.get("smiles") or "") or None
            sch = fetch_surechembl_alternatives(
                material=original,
                smiles=smi,
                limit=surechembl_limit,
                threshold=similarity_threshold,
                role_hint=str(role) if role else None,
            )
            surechembl_rows = list(sch.get("surechembl") or [])
            surechembl_meta = dict(sch.get("surechembl_meta") or {})
            # Light dedup vs PubChem by SMILES / InChI key.
            ext_keys = {
                str(r.get("smiles") or "").strip()
                for r in external
                if r.get("smiles")
            } | {
                str(r.get("inchi_key") or "").strip()
                for r in external
                if r.get("inchi_key")
            }
            if ext_keys:
                surechembl_rows = [
                    r
                    for r in surechembl_rows
                    if (str(r.get("smiles") or "").strip() not in ext_keys)
                    and (str(r.get("inchi_key") or "").strip() not in ext_keys)
                ]
                surechembl_meta["count"] = len(surechembl_rows)
            if surechembl_rows:
                layers_used.append("surechembl")
        except Exception as exc:
            logger.warning("surechembl substitutes degraded ({})", exc)
            surechembl_rows = []
            surechembl_meta = {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": f"surechembl_error:{exc}",
                "provider": "surechembl_api",
                "search_hash": None,
            }
    else:
        surechembl_meta = {
            "enabled": False,
            "queried": False,
            "count": 0,
            "skipped_reason": "include_surechembl=false",
            "provider": "surechembl_api",
            "search_hash": None,
        }

    # L4: auto when catalog hits are scarce; explicit true/false overrides.
    catalog_hits = len(candidates[:limit])
    if include_llm is True:
        run_llm, llm_mode = True, "forced"
    elif include_llm is False:
        run_llm, llm_mode = False, "off"
    else:
        run_llm, llm_mode = catalog_hits < 3, "auto"

    llm_rows: list[dict] = []
    llm_meta: dict
    if run_llm:
        try:
            from .llm_alternatives import fetch_llm_alternatives

            known = [c["material"] for c in candidates[:limit]]
            known.extend(r.get("name") or "" for r in literature)
            known.extend(r.get("name") or "" for r in external)
            known.extend(r.get("name") or "" for r in surechembl_rows)
            out = fetch_llm_alternatives(
                material=original,
                role_hint=str(role) if role else None,
                limit=llm_limit,
                known_names=known,
                allow_llm=True,
            )
            llm_rows = list(out.get("llm") or [])
            llm_meta = dict(out.get("llm_meta") or {})
            llm_meta["mode"] = llm_mode
            if llm_rows:
                layers_used.append("llm")
        except Exception as exc:
            logger.warning("llm substitutes degraded ({})", exc)
            llm_rows = []
            llm_meta = {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": f"llm_error:{exc}",
                "mode": llm_mode,
                "providers": [],
            }
    else:
        reason = (
            "include_llm=false"
            if include_llm is False
            else f"auto_skipped:catalog_hits={catalog_hits}>=3"
        )
        llm_meta = {
            "enabled": False,
            "queried": False,
            "count": 0,
            "skipped_reason": reason,
            "mode": llm_mode,
            "providers": [],
        }

    return {
        "original": original,
        "original_in_catalog": original_in_catalog,
        "slot_index": slot_index,
        "role": role,
        "substitute_group": group,
        "base_metrics": {k: round(v, 4) for k, v in base_metrics.items()},
        "candidates": candidates[:limit],
        "total_considered": len(pool),
        "identity": identity,
        "external": external,
        "external_meta": external_meta,
        "literature": literature,
        "literature_meta": literature_meta,
        "surechembl": surechembl_rows,
        "surechembl_meta": surechembl_meta,
        "llm": llm_rows,
        "llm_meta": llm_meta,
        "layers_used": layers_used,
    }


def scan_supply_risk(
    genomes: dict[str, FormulationGenome] | None = None,
    req: Requirement | None = None,
) -> dict:
    """Which materials are at supply risk, and which formulations they hit.

    This is the trigger the availability field exists for: mark a material
    discontinued and every affected formulation surfaces with replacements
    already ranked, instead of waiting for someone to notice at reorder time.
    """
    from ..domain import knowledge

    at_risk = {
        name: spec.get("availability")
        for name, spec in knowledge.RAW_MATERIALS.items()
        if spec.get("availability") in ("discontinued", "restricted")
    }
    affected: list[dict] = []
    for label, genome in (genomes or {}).items():
        hits = [
            {"slot_index": i, "material": s.material, "availability": at_risk[s.material]}
            for i, s in enumerate(genome.slots)
            if s.material in at_risk
        ]
        if not hits:
            continue
        entry = {"formulation": label, "affected_slots": hits, "suggestions": {}}
        for hit in hits:
            try:
                report = find_substitutes(
                    genome, hit["slot_index"], req, limit=3, include_external=False
                )
                entry["suggestions"][hit["material"]] = [
                    {
                        "material": c["material"],
                        "structural_score": c["structural_score"],
                        "feasible": c["feasible"],
                        "delta_confidence": c["delta_confidence"],
                    }
                    for c in report["candidates"]
                ]
            except Exception as exc:
                logger.debug("supply scan: {} failed ({})", hit["material"], exc)
        affected.append(entry)
    return {"at_risk": at_risk, "affected": affected}
