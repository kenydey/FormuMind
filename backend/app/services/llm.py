"""LLM service (Anthropic Claude adapter + deterministic offline fallback).

``synthesize_research`` produces the chat-stream narrative and reaction
mechanism shown in the centre panel. With ``ANTHROPIC_API_KEY`` set it calls
Claude grounded on the retrieved evidence; otherwise it composes a structured,
citation-backed narrative from the domain knowledge base so the platform is
fully functional and tests stay deterministic.
"""
from __future__ import annotations

from ..config import get_settings
from ..domain.knowledge import MECHANISMS
from ..domain.schemas import Evidence, Formulation, Requirement


def _try_claude(prompt: str) -> str | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    try:  # pragma: no cover - requires network + key
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    except Exception:
        return None


def _evidence_prompt(req: Requirement, evidence: list[Evidence]) -> str:
    cites = "\n".join(f"- [{e.identifier}] {e.title}: {e.snippet}" for e in evidence)
    return (
        f"You are a coatings/surface-treatment formulation chemist. Requirement: {req.headline()}.\n"
        f"Cure temperature limit {req.cure_temperature_c} C, VOC limit {req.voc_limit_gpl} g/L.\n"
        f"Relevant prior art:\n{cites}\n\n"
        "Explain the protection/cleaning mechanism and justify a starting formulation, citing sources."
    )


def synthesize_research(
    req: Requirement, evidence: list[Evidence], recommended: list[Formulation]
) -> tuple[str, str]:
    """Return (mechanism, chat_markdown)."""
    mechanism = MECHANISMS[req.domain]
    claude = _try_claude(_evidence_prompt(req, evidence))
    if claude:
        return mechanism, claude

    # Deterministic offline narrative.
    lines = [
        f"### Research summary — {req.headline()}",
        "",
        "**Retrieved prior art:**",
    ]
    for e in evidence:
        lines.append(f"- `{e.identifier}` ({e.source}) — {e.title}. {e.snippet}")
    lines += ["", "**Protection / cleaning mechanism:**", mechanism, "", "**Candidate formulations:**"]
    for i, f in enumerate(recommended, start=1):
        comp = ", ".join(f"{ing.name} {ing.weight_pct}%" for ing in f.ingredients)
        lines.append(f"{i}. **{f.name}** — {comp}")
        if f.predicted:
            preds = ", ".join(f"{k}={v}" for k, v in f.predicted.items())
            lines.append(f"   - predicted: {preds}")
    lines += [
        "",
        "_Next: generate a DOE plan on the key levers, then run the closed-loop optimizer to rank the top candidates._",
    ]
    return mechanism, "\n".join(lines)
