"""DOE factor suggestions — levers + KB parameter space fusion (Sprint 3)."""
from __future__ import annotations

from . import kb_index
from ..domain.project_spec import levers_to_doe_factors, resolve_levers
from ..domain.schemas import DOEFactor, FactorCandidate, Requirement


def suggest_factors(req: Requirement) -> list[FactorCandidate]:
    """Derive factor candidates from requirement levers and persisted KB guides."""
    levers = resolve_levers(req, req.active_formulation)
    factors = levers_to_doe_factors(levers)
    kb_space = kb_index.aggregate_parameter_space()
    hints = kb_index.doe_parameter_hints([f.name for f in factors])

    wiki_bounds: list[dict] = []
    wiki_notes: list[str] = []
    try:
        from .wiki.constraints import wiki_doe_hint_lines, wiki_parameter_bounds

        wiki_bounds = wiki_parameter_bounds()
        wiki_notes = wiki_doe_hint_lines([f.name for f in factors])
    except Exception:
        wiki_bounds = []
        wiki_notes = []

    out: list[FactorCandidate] = []
    for f in factors:
        rationale_parts: list[str] = []
        evidence_ids: list[str] = []
        lo, hi = f.low, f.high
        source_tag = "kb+levers"

        # Fuzzy match KB parameter_space keys to factor names
        for pname, bound in kb_space.items():
            if pname.lower() in f.name.lower() or f.name.lower() in pname.lower():
                if bound.get("min") is not None:
                    lo = min(lo, float(bound["min"]))
                if bound.get("max") is not None:
                    hi = max(hi, float(bound["max"]))
                src_n = int(bound.get("sources") or 0)
                rationale_parts.append(
                    f"KB 文献聚合: {pname} [{bound.get('min')}–{bound.get('max')} {bound.get('unit', '')}]"
                    f" ({src_n} 篇)"
                )
                evidence_ids.append(f"kb:param:{pname}")

        # W4: Wiki bounds tighten the envelope (intersection with current lo/hi).
        for b in wiki_bounds:
            pname = str(b.get("name") or "")
            if not pname:
                continue
            if not (
                pname.lower() in f.name.lower()
                or f.name.lower() in pname.lower()
            ):
                continue
            try:
                if b.get("min") is not None:
                    lo = max(lo, float(b["min"]))
                if b.get("max") is not None:
                    hi = min(hi, float(b["max"]))
            except (TypeError, ValueError):
                continue
            if lo > hi:
                lo, hi = hi, lo
            rationale_parts.append(
                f"Wiki 约束: {pname} [{b.get('min')}–{b.get('max')} {b.get('unit', '')}]"
                f" ({b.get('path')})"
            )
            evidence_ids.append(f"wiki:{b.get('path')}")
            source_tag = "wiki+kb+levers"

        for hint in hints:
            if f.name in hint or any(tok in hint for tok in f.name.split()):
                rationale_parts.append(hint)
        for note in wiki_notes:
            if f.name in note or any(tok in note for tok in f.name.split()):
                if note not in rationale_parts:
                    rationale_parts.append(note)

        if not rationale_parts:
            rationale_parts.append("来自项目需求 levers / 默认配方可调组分")

        out.append(
            FactorCandidate(
                name=f.name,
                low=lo,
                high=hi,
                unit=f.unit or "wt%",
                rationale="; ".join(rationale_parts[:4]),
                evidence_ids=evidence_ids[:6],
                source=source_tag,
            )
        )
    return out


def suggest_factors_as_doe(req: Requirement) -> list[DOEFactor]:
    return [
        DOEFactor(name=c.name, low=c.low, high=c.high, unit=c.unit)
        for c in suggest_factors(req)
    ]
