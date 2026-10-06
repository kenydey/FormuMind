"""Design of Experiments (DOE) engine.

Pure-numpy generators for the classical screening and response-surface designs
used in formulation R&D. Each generator returns coded factor levels in
[-1, +1] (or with axial/centre points), which :func:`build_plan` decodes to
natural units against the supplied factor ranges.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

from .schemas import DOEFactor, DOEPlan, DOERun


# Hard ceiling on generated runs. Full factorials grow as levels**k, so an
# unbounded request (e.g. 25 two-level levers = 33M runs) would exhaust memory
# and the DB before any validation ran. Callers get a ValueError (-> HTTP 422).
MAX_DESIGN_RUNS = 4096


def _check_run_budget(n_runs: int, what: str) -> None:
    if n_runs > MAX_DESIGN_RUNS:
        raise ValueError(
            f"{what} would generate {n_runs} runs (limit {MAX_DESIGN_RUNS}); "
            "reduce the number of factors/levels or use design='lhs'/'fractional_factorial'"
        )


def full_factorial(k: int, levels: int | list[int] = 2) -> np.ndarray:
    """Coded full factorial for ``k`` factors.

    ``levels`` is either one level count applied to every factor, or a
    per-factor list for a mixed-level factorial (C-4a: e.g. ``[3, 2]`` for a
    3-level discrete factor crossed with a 2-level continuous factor, so
    every declared discrete level actually appears in the design).
    """
    if isinstance(levels, int):
        counts = [levels] * k
    else:
        counts = list(levels)
        if len(counts) != k:
            raise ValueError(
                f"full_factorial: got {len(counts)} level counts for {k} factors"
            )
    if any(c < 2 for c in counts):
        raise ValueError(f"full_factorial: level counts must be >= 2, got {counts}")
    _check_run_budget(math.prod(counts), f"full factorial over {k} factors")
    if all(c == 2 for c in counts):
        return np.array(list(itertools.product([-1.0, 1.0], repeat=k)))
    cols = [np.linspace(-1.0, 1.0, c) for c in counts]
    return np.array(list(itertools.product(*cols)))


def fractional_factorial(k: int) -> np.ndarray:
    """A 2^(k-1) half-fraction.

    Builds the 2^(k-1) full factorial on the first k-1 factors and defines the
    last factor as the product of all base columns (generator I = ABC...).
    """
    if k <= 3:
        return full_factorial(k)
    base = k - 1
    full = full_factorial(base)
    extra = np.prod(full, axis=1, keepdims=True)  # generator: last = product of all base
    return np.hstack([full, extra])


def plackett_burman(k: int) -> np.ndarray:
    """Plackett-Burman screening design for up to ``k`` factors.

    Builds the next multiple-of-4 run count from a known generating row and
    cyclically rotates it, appending the final all-minus row.
    """
    generators = {
        8: [1, 1, 1, -1, 1, -1, -1],
        12: [1, 1, -1, 1, 1, 1, -1, -1, -1, 1, -1],
        16: [1, 1, 1, 1, -1, 1, -1, 1, 1, -1, -1, 1, -1, -1, -1],
        20: [1, 1, -1, -1, 1, 1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, 1, 1, -1],
    }
    n = next((m for m in sorted(generators) if m - 1 >= k), None)
    if n is None:
        raise ValueError(
            f"plackett_burman supports at most {max(generators) - 1} factors, got {k}"
        )
    row = generators[n]
    design = [row]
    for _ in range(n - 2):
        row = [row[-1]] + row[:-1]
        design.append(row)
    design.append([-1] * (n - 1))
    return np.array(design, dtype=float)[:, :k]


_INSCRIBED = ("inscribed", "cci")


def is_inscribed(alpha: object) -> bool:
    """True for the inscribed central composite design (``"inscribed"`` / ``"cci"``)."""
    return isinstance(alpha, str) and alpha.strip().lower() in _INSCRIBED


def resolve_ccd_alpha(alpha: object, n_factorial: int) -> float:
    """The axial distance of a central composite design, in coded units, *before* any scaling of the whole design.

    ``"face"`` (what every caller gets by default) is 1: the star points sit on the faces of the factorial box, so
    every run stays inside each factor's ``[low, high]`` - the right choice for a formulation, where a star point past
    the box is a negative concentration or a temperature nobody can set. ``"rotatable"`` is ``n_factorial ** 0.25``
    (> 1): equal prediction variance in every direction, bought with star points outside the box. ``"inscribed"`` is the
    same distance, and :func:`central_composite` then shrinks the whole design by ``1 / alpha``: still rotatable, star
    points on the faces, factorial points inside - the way to keep both properties. A bare number is taken as alpha
    itself. Anything else is a ValueError (the API turns it into a 422).
    """
    if alpha is None or (isinstance(alpha, str) and alpha.strip().lower() in ("", "face", "faced")):
        return 1.0
    if isinstance(alpha, str) and (alpha.strip().lower() == "rotatable" or is_inscribed(alpha)):
        return float(n_factorial) ** 0.25
    try:
        if isinstance(alpha, bool):
            raise TypeError("a bool is not a distance")
        value = float(alpha)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(
            f"unknown ccd_alpha {alpha!r}: use 'face', 'rotatable', 'inscribed' or a number > 0"
        ) from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"ccd_alpha must be a finite number > 0, got {alpha!r}")
    return value


def central_composite(k: int, alpha: str | float = "rotatable") -> np.ndarray:
    """Central composite design: factorial + axial (star) + centre points.

    ``alpha`` is anything :func:`resolve_ccd_alpha` accepts; :func:`build_plan` asks for ``"face"`` unless told otherwise.
    ``"inscribed"`` returns the rotatable design scaled by ``1 / alpha``: star points at +-1, factorial points at
    +-``1 / alpha``.
    """
    if k > 4:
        _check_run_budget(2 ** (k - 1), f"central composite over {k} factors")
    factorial = full_factorial(k) if k <= 4 else fractional_factorial(k)
    a = resolve_ccd_alpha(alpha, len(factorial))
    axial = []
    for i in range(k):
        for sign in (-a, a):
            pt = [0.0] * k
            pt[i] = sign
            axial.append(pt)
    centre = [[0.0] * k for _ in range(3)]
    design = np.vstack([factorial, np.array(axial), np.array(centre)])
    return design / a if is_inscribed(alpha) else design


def latin_hypercube(k: int, n: int, seed: int = 0) -> np.ndarray:
    """Latin hypercube sample mapped to coded [-1, 1] space."""
    rng = np.random.default_rng(seed)
    cut = np.linspace(0.0, 1.0, n + 1)
    samples = np.empty((n, k))
    for j in range(k):
        u = rng.uniform(size=n)
        points = cut[:n] + u * (cut[1] - cut[0])
        rng.shuffle(points)
        samples[:, j] = points
    return samples * 2.0 - 1.0  # -> [-1, 1]


_DESIGNS = {
    "full_factorial": lambda k, n: full_factorial(k),
    "fractional_factorial": lambda k, n: fractional_factorial(k),
    "plackett_burman": lambda k, n: plackett_burman(k),
    "ccd": lambda k, n: central_composite(k, "face"),
    "lhs": lambda k, n: latin_hypercube(k, n or max(2 * k + 1, 8)),
}


def decode(coded: float, factor: DOEFactor) -> float | str:
    """Map a coded level in [-1, 1] to the factor's natural range.

    Discrete factors (C-4a) map the coded value onto the level index:
    -1 → levels[0], +1 → levels[-1], linear in between.
    """
    if factor.kind == "discrete":
        levels = factor.levels or []
        if not levels:
            raise ValueError(f"Discrete factor {factor.name!r} has no levels")
        idx = int(round((coded + 1.0) / 2.0 * (len(levels) - 1)))
        idx = max(0, min(len(levels) - 1, idx))
        return levels[idx]
    mid = (factor.high + factor.low) / 2.0
    half = (factor.high - factor.low) / 2.0
    return round(mid + coded * half, 4)


def _level_counts(factors: list[DOEFactor]) -> list[int]:
    """Per-factor level counts for a mixed-level full factorial (C-4a).

    Discrete factors contribute their declared level count so every level
    appears in the design matrix; continuous factors stay at 2 levels.
    """
    return [
        len(f.levels) if (f.kind == "discrete" and f.levels) else 2
        for f in factors
    ]


def build_plan(
    factors: list[DOEFactor],
    design: str = "full_factorial",
    n: int | None = None,
    *,
    ccd_alpha: str | float | None = None,
) -> DOEPlan:
    """Build a design over *factors*.

    ``ccd_alpha`` only matters for ``design="ccd"``: ``"face"`` (the default, every run inside ``[low, high]``),
    ``"inscribed"`` (rotatable and every run inside, factorial points pulled in), ``"rotatable"`` or a number - see
    :func:`resolve_ccd_alpha`. Runs that land outside a factor's range (star points of a rotatable or large-alpha CCD)
    are kept but marked ``infeasible`` with the reason, never clipped: clipping would collapse them onto other runs and
    silently change the design.
    """
    if not factors:
        raise ValueError("At least one factor is required for a DOE plan.")
    if design not in _DESIGNS:
        raise ValueError(f"Unknown design {design!r}; choose from {sorted(_DESIGNS)}")
    k = len(factors)
    _discrete = [f.name for f in factors if f.kind == "discrete"]
    if _discrete and design != "full_factorial":
        # v9: ccd/fractional/pb 的 coded 点经 decode clamp 到水平索引后会
        # 静默退化（星点坍缩成重复 run、中间水平永不出现），fail-closed
        # 而非给出"看起来正常、实际退化"的方案。lhs 保留但记 warning。
        if design == "lhs":
            note_extra = (
                f" WARNING: discrete factors {_discrete} mapped to nearest levels; "
                "level coverage is not guaranteed for lhs."
            )
        else:
            raise ValueError(
                f"design {design!r} does not support discrete factors {_discrete}; "
                "use design='full_factorial' (engine='native') or engine='baybe'"
            )
    else:
        note_extra = ""
    if design == "full_factorial":
        # C-4a: align the design with each discrete factor's level count.
        matrix = full_factorial(k, levels=_level_counts(factors))
    elif design == "ccd":
        matrix = central_composite(k, "face" if ccd_alpha in (None, "") else ccd_alpha)
    else:
        matrix = _DESIGNS[design](k, n)
    runs: list[DOERun] = []
    outside_runs = 0
    for idx, row in enumerate(matrix, start=1):
        coded: dict[str, float] = {}
        natural: dict[str, float | str] = {}
        outside: list[str] = []
        for f, c in zip(factors, row):
            c = float(c)
            coded[f.name] = round(c, 4)
            natural[f.name] = decode(c, f)
            if f.kind != "discrete" and abs(c) > 1.0 + 1e-9:
                outside.append(f"{f.name}={natural[f.name]} 不在 [{f.low:g}, {f.high:g}] 内")
        run = DOERun(run_id=idx, coded=coded, natural=natural)
        if outside:
            run.infeasible = True
            run.infeasible_reason = "星点超出因子范围：" + "；".join(outside)
            outside_runs += 1
        runs.append(run)
    if design == "ccd":
        alpha_used = float(np.max(np.abs(matrix)))
        if is_inscribed(ccd_alpha):
            inner = float(np.min(np.abs(matrix[np.abs(matrix) > 1e-12])))
            note_extra += (
                f" Inscribed (rotatable alpha={1.0 / inner:.4g} scaled by 1/alpha): star points on the faces, "
                f"factorial points at +-{inner:.4g}; every run inside [low, high]."
            )
        else:
            note_extra += (
                f" Axial distance alpha={alpha_used:.4g}"
                + (" (face-centred: every run inside [low, high])." if alpha_used <= 1.0 + 1e-9 else " (star points outside the factor box).")
            )
    if outside_runs:
        # Clipping the star points would collapse them onto other runs, so they stay as they are and are flagged:
        # the caller must widen the physical limits, drop those runs, or ask for ccd_alpha='face'.
        note_extra += (
            f" WARNING: {outside_runs} of {len(runs)} runs lie outside the factor [low, high] range "
            "(e.g. a negative concentration) and are marked infeasible; use ccd_alpha='face' or 'inscribed' to keep every run inside."
        )
    note = (
        f"{design} design over {k} factors -> {len(runs)} runs. "
        f"Estimated resolution: {'screening' if design in ('fractional_factorial', 'plackett_burman') else 'response-surface' if design == 'ccd' else 'space-filling' if design == 'lhs' else 'full'}."
        f"{note_extra}"
    )
    return DOEPlan(design=design, factors=factors, runs=runs, notes=note)
