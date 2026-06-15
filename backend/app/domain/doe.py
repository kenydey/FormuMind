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


def full_factorial(k: int, levels: int = 2) -> np.ndarray:
    """Coded full factorial for ``k`` factors at ``levels`` levels."""
    if levels == 2:
        grid = np.array(list(itertools.product([-1.0, 1.0], repeat=k)))
    else:
        pts = np.linspace(-1.0, 1.0, levels)
        grid = np.array(list(itertools.product(pts, repeat=k)))
    return grid


def fractional_factorial(k: int) -> np.ndarray:
    """A resolution-aware 2^(k-p) fraction.

    Uses the largest base 2^m with m < k and defines the extra factors as
    products of base columns (standard generator construction).
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
    n = next(m for m in sorted(generators) if m - 1 >= k)
    row = generators[n]
    design = [row]
    for _ in range(n - 2):
        row = [row[-1]] + row[:-1]
        design.append(row)
    design.append([-1] * (n - 1))
    return np.array(design, dtype=float)[:, :k]


def central_composite(k: int, alpha: str = "rotatable") -> np.ndarray:
    """Central composite design: factorial + axial (star) + centre points."""
    factorial = full_factorial(k) if k <= 4 else fractional_factorial(k)
    a = float(len(factorial)) ** 0.25 if alpha == "rotatable" else 1.0
    axial = []
    for i in range(k):
        for sign in (-a, a):
            pt = [0.0] * k
            pt[i] = sign
            axial.append(pt)
    centre = [[0.0] * k] * 3
    return np.vstack([factorial, np.array(axial), np.array(centre)])


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
    "ccd": lambda k, n: central_composite(k),
    "lhs": lambda k, n: latin_hypercube(k, n or max(2 * k + 1, 8)),
}


def decode(coded: float, factor: DOEFactor) -> float:
    """Map a coded level in [-1, 1] to the factor's natural range."""
    mid = (factor.high + factor.low) / 2.0
    half = (factor.high - factor.low) / 2.0
    return round(mid + coded * half, 4)


def build_plan(factors: list[DOEFactor], design: str = "full_factorial", n: int | None = None) -> DOEPlan:
    if not factors:
        raise ValueError("At least one factor is required for a DOE plan.")
    if design not in _DESIGNS:
        raise ValueError(f"Unknown design {design!r}; choose from {sorted(_DESIGNS)}")
    k = len(factors)
    matrix = _DESIGNS[design](k, n)
    runs: list[DOERun] = []
    for idx, row in enumerate(matrix, start=1):
        coded = {f.name: round(float(c), 4) for f, c in zip(factors, row)}
        natural = {f.name: decode(float(c), f) for f, c in zip(factors, row)}
        runs.append(DOERun(run_id=idx, coded=coded, natural=natural))
    note = (
        f"{design} design over {k} factors -> {len(runs)} runs. "
        f"Estimated resolution: {'screening' if design in ('fractional_factorial', 'plackett_burman') else 'response-surface' if design == 'ccd' else 'space-filling' if design == 'lhs' else 'full'}."
    )
    return DOEPlan(design=design, factors=factors, runs=runs, notes=note)
