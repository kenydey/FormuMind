"""One policy for "do the weights add up to 100 %".

The question used to be asked in five places with four different answers:
``chemistry.validate_formulation`` warned beyond ±0.5, the v11 ``dimension_closure`` layer
and ``formulation_gate.validate_formulations`` beyond ±5, ``validate_recommended_formulas``
beyond ±8, and the feasibility gate never asked at all. The same 68 % recipe produced three
differently-worded warnings in one response, was judged "feasible", and — because weights
are deliberately not renormalised (strict grounding drops unsupported components rather
than rescaling the rest) — ranked purely on its predicted properties, i.e. exactly like a
complete recipe.

The policy, with ``deviation = |Σ weight_pct − 100|``:

=========== ================ ======================================================
level        deviation        meaning
=========== ================ ======================================================
``ok``       ≤ 0.5            rounding noise; nothing to say
``warn``     ≤ 5              say so (and discount the score a little)
``error``    > 5              incomplete / over-full recipe: say so, discount the
                              score, and the feasibility gate rejects it
=========== ================ ======================================================

The score discount is ``1 % per percentage point beyond the tolerance, capped at 20 %``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

OK_TOLERANCE = 0.5
WARN_TOLERANCE = 5.0
PENALTY_PER_POINT = 0.01
MAX_PENALTY = 0.20

# Every closure message contains this phrase, so callers that merge warning lists can
# recognise (and de-duplicate) them regardless of the formulation-name prefix.
CLOSURE_MARK = "weight percentages sum to"


@dataclass(frozen=True)
class Closure:
    total: float
    deviation: float
    level: str  # "ok" | "warn" | "error"

    @property
    def ok(self) -> bool:
        return self.level == "ok"

    @property
    def score_factor(self) -> float:
        """Multiplier in ``[1 − MAX_PENALTY, 1]`` for a score computed on this recipe."""
        excess = max(0.0, self.deviation - OK_TOLERANCE)
        return 1.0 - min(MAX_PENALTY, excess * PENALTY_PER_POINT)


def total_of(weights: Iterable[float | None]) -> float:
    return float(sum(w for w in weights if w is not None))


def assess(total: float) -> Closure:
    deviation = abs(float(total) - 100.0)
    if deviation <= OK_TOLERANCE:
        level = "ok"
    elif deviation <= WARN_TOLERANCE:
        level = "warn"
    else:
        level = "error"
    return Closure(total=float(total), deviation=deviation, level=level)


def warning_text(total: float, name: str | None = None) -> str | None:
    """The one warning a closure problem produces; ``None`` when the weights close."""
    closure = assess(total)
    if closure.ok:
        return None
    # Sentence case when it opens the message ("Weight percentages sum to …", the wording the
    # physical-constraint layer has always used); mid-sentence after a formulation name.
    prefix = f"{name}: " if name else ""
    phrase = CLOSURE_MARK if name else CLOSURE_MARK[:1].upper() + CLOSURE_MARK[1:]
    text = f"{prefix}{phrase} {closure.total:.1f}% (expected ~100%)"
    if closure.level == "error":
        text += " — " + ("incomplete" if closure.total < 100.0 else "over-full") + " recipe"
    discount = round((1.0 - closure.score_factor) * 100.0, 1)
    if discount > 0:
        text += f"; score discounted {discount:g}%"
    return text


def is_closure_warning(text: str) -> bool:
    return CLOSURE_MARK in (text or "").lower()


def discount_score(score: float, total: float) -> float:
    """Apply the closure discount so that a *worse* recipe always ends up with a *lower* score.

    Multiplying a negative score by a factor below one would raise it, so the discount is
    taken off the magnitude: ``score − |score|·(1 − factor)``.
    """
    factor = assess(total).score_factor
    if factor >= 1.0:
        return float(score)
    return float(score) - abs(float(score)) * (1.0 - factor)
