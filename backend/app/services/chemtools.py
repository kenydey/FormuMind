"""ChemCrow tool gateway — deterministic, tool-level chemistry utilities.

This module is the single integration point between FormuMind pipelines and
ChemCrow's *tools* (not its ReAct agent, which stays confined to Q&A routing
in ``llm.answer_question``).  Tool-level calls are deterministic, mostly
local (RDKit / molbloom) and LLM-free, so they are safe to embed in the
recommend / DOE / optimize pipelines.

Design contract (mirrors the platform's optional-engine conventions):

* every public function degrades to a neutral value (``None`` / ``[]``)
  when ChemCrow is not installed, the gateway is disabled via
  ``FORMUMIND_CHEMTOOLS_ENABLED=false``, or the underlying tool fails;
* pure-structure utilities (functional groups, similarity) fall back to a
  local RDKit implementation when ChemCrow itself is absent but RDKit is
  available — ChemCrow's own implementations are RDKit SMARTS underneath;
* results are memoised in a process-local TTL cache keyed by (tool, arg);
* network-bound tools run under a hard timeout so pipeline latency is bounded.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import time
from typing import Any, Callable, TypeVar

from ..config import get_settings
from .errors import degrade_return, optional_import

logger = logging.getLogger(__name__)

T = TypeVar("T")

_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}
_CACHE_TTL_SEC = 86400  # 24h — same policy as chemical_lookup / compounds
_CACHE_LOCK = threading.Lock()

_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()

_CAS_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")


# ── availability ─────────────────────────────────────────────────────────────


def gateway_enabled() -> bool:
    """True when the gateway switch is on (independent of installed libs)."""
    return bool(get_settings().chemtools_enabled)


def chemcrow_available() -> bool:
    return optional_import("chemcrow")


def rdkit_available() -> bool:
    return optional_import("rdkit")


def availability() -> dict[str, Any]:
    """Per-capability availability report (for /api/chemical/tools and UI)."""
    enabled = gateway_enabled()
    cc = chemcrow_available()
    rd = rdkit_available()
    settings = get_settings()
    serp = bool(settings.serpapi_api_key)

    def cap(available: bool, hint: str | None) -> dict[str, Any]:
        return {"available": bool(enabled and available), "hint": None if (enabled and available) else hint}

    install_hint = "pip install -e '.[intel]' 安装 ChemCrow 工具链"
    rdkit_hint = "pip install rdkit 或安装 intel extra"
    return {
        "enabled": enabled,
        "chemcrow_installed": cc,
        "rdkit_installed": rd,
        "capabilities": {
            "name_to_smiles": cap(cc, install_hint),
            "name_to_cas": cap(cc, install_hint),
            "func_groups": cap(cc or rd, rdkit_hint),
            "mol_similarity": cap(cc or rd, rdkit_hint),
            "patent_check": cap(cc, install_hint + "（molbloom 分子专利布隆过滤器）"),
            "controlled_check": cap(cc, install_hint),
            "explosive_check": cap(cc, install_hint),
            "web_search": {
                "available": bool(enabled and cc and serp),
                "hint": None if (enabled and cc and serp) else "需 ChemCrow + SERPAPI_API_KEY",
            },
        },
    }


# ── plumbing ─────────────────────────────────────────────────────────────────


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="chemtools"
                )
    return _EXECUTOR


def _run_with_timeout(fn: Callable[[], T], default: T) -> T:
    """Run *fn* on the gateway executor with the configured hard timeout."""
    timeout = float(get_settings().chemtools_timeout_s)
    future = _executor().submit(fn)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        logger.warning("chemtools call timed out after %.1fs", timeout)
        return default
    except Exception as exc:
        return degrade_return(logger, exc, "chemtools call failed", default)


def _cached(tool: str, arg: str, compute: Callable[[], T]) -> T:
    key = (tool, arg.strip().lower())
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and time.time() - entry[0] <= _CACHE_TTL_SEC:
            return entry[1]
    value = compute()
    # Don't cache failures — a transient outage shouldn't poison 24h of lookups.
    if value is not None:
        with _CACHE_LOCK:
            _CACHE[key] = (time.time(), value)
    return value


def clear_cache() -> None:
    """Test hook."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _chemcrow_tool(*names: str) -> Any | None:
    """Instantiate the first importable ChemCrow tool among *names*.

    Tool class names have shifted across chemcrow releases, so callers pass
    every known alias (e.g. ``"ControlChemCheck", "ControlledChemicalCheck"``).
    """
    try:
        import chemcrow.tools as cct  # type: ignore
    except Exception as exc:
        return degrade_return(logger, exc, "chemcrow import failed", None)
    for name in names:
        cls = getattr(cct, name, None)
        if cls is not None:
            try:
                return cls()
            except Exception as exc:
                degrade_return(logger, exc, f"chemcrow tool {name} init failed", None)
    return None


def _run_tool(tool: Any, arg: str) -> str | None:
    runner = getattr(tool, "_run", None) or getattr(tool, "run", None)
    if runner is None:
        return None
    out = runner(arg)
    return str(out) if out is not None else None


# ── name resolution (network; ChemCrow only) ─────────────────────────────────

_SMILES_CHARS_RE = re.compile(r"^[A-Za-z0-9@+\-\[\]\(\)=#$/\\%.:*]+$")


def _looks_like_smiles(text: str) -> bool:
    t = text.strip()
    return 1 < len(t) <= 500 and " " not in t and bool(_SMILES_CHARS_RE.match(t))


def name_to_smiles(name: str) -> str | None:
    """Resolve a chemical name to SMILES via ChemCrow Query2SMILES."""
    name = (name or "").strip()
    if not name or not gateway_enabled() or not chemcrow_available():
        return None

    def compute() -> str | None:
        def call() -> str | None:
            tool = _chemcrow_tool("Query2SMILES")
            if tool is None:
                return None
            out = _run_tool(tool, name)
            if out and _looks_like_smiles(out):
                return out.strip()
            return None

        return _run_with_timeout(call, None)

    return _cached("name_to_smiles", name, compute)


def name_to_cas(name: str) -> str | None:
    """Resolve a chemical name to a CAS number via ChemCrow Query2CAS."""
    name = (name or "").strip()
    if not name or not gateway_enabled() or not chemcrow_available():
        return None

    def compute() -> str | None:
        def call() -> str | None:
            tool = _chemcrow_tool("Query2CAS")
            if tool is None:
                return None
            out = _run_tool(tool, name)
            if not out:
                return None
            m = _CAS_RE.search(out)
            return m.group(0) if m else None

        return _run_with_timeout(call, None)

    return _cached("name_to_cas", name, compute)


# ── structure utilities (local; ChemCrow → RDKit fallback) ───────────────────

# Compact SMARTS dictionary focused on coating / surface-treatment chemistry.
# Mirrors the group families ChemCrow's FuncGroups reports, so both paths
# produce comparable labels.
_FUNC_GROUP_SMARTS: dict[str, str] = {
    "epoxide": "[OX2r3]1[#6r3][#6r3]1",
    "isocyanate": "[NX2]=[CX2]=[OX1]",
    "primary amine": "[NX3;H2;!$(NC=O)]",
    "secondary amine": "[NX3;H1;!$(NC=O)]",
    "hydroxyl": "[OX2H]",
    "carboxylic acid": "[CX3](=O)[OX2H1]",
    "ester": "[CX3](=O)[OX2H0][#6]",
    "amide": "[NX3][CX3](=[OX1])",
    "urethane": "[NX3][CX3](=[OX1])[OX2H0]",
    "ether": "[OD2]([#6])[#6]",
    "silane (alkoxysilane)": "[Si]([OX2])[OX2]",
    "phosphate": "[PX4](=O)([OX2])[OX2]",
    "sulfonate": "[SX4](=O)(=O)[OX2]",
    "nitrile": "[NX1]#[CX2]",
    "vinyl": "[CX3]=[CX2]",
    "aromatic ring": "a1aaaaa1",
}


def _rdkit_func_groups(smiles: str) -> list[str] | None:
    try:
        from rdkit import Chem  # type: ignore

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        found = []
        for label, smarts in _FUNC_GROUP_SMARTS.items():
            patt = Chem.MolFromSmarts(smarts)
            if patt is not None and mol.HasSubstructMatch(patt):
                found.append(label)
        return found
    except Exception as exc:
        return degrade_return(logger, exc, "rdkit func_groups failed", None)


def _parse_func_groups_text(text: str) -> list[str]:
    """Parse ChemCrow FuncGroups prose ('This molecule contains X, Y, and Z.')."""
    t = text.strip().rstrip(".")
    lowered = t.lower()
    marker = "contains"
    idx = lowered.rfind(marker)
    if idx >= 0:
        t = t[idx + len(marker):]
    parts = re.split(r",| and ", t)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) < 60]


def func_groups(smiles: str) -> list[str]:
    """Functional groups present in *smiles* (ChemCrow FuncGroups → RDKit)."""
    smiles = (smiles or "").strip()
    if not smiles or not gateway_enabled():
        return []

    def compute() -> list[str] | None:
        if chemcrow_available():
            def call() -> list[str] | None:
                tool = _chemcrow_tool("FuncGroups", "FunctionalGroups")
                if tool is None:
                    return None
                out = _run_tool(tool, smiles)
                return _parse_func_groups_text(out) if out else None

            groups = _run_with_timeout(call, None)
            if groups is not None:
                return groups
        return _rdkit_func_groups(smiles)

    return _cached("func_groups", smiles, compute) or []


def mol_similarity(smiles_a: str, smiles_b: str) -> float | None:
    """Tanimoto similarity of two molecules (ChemCrow MolSimilarity → RDKit)."""
    a, b = (smiles_a or "").strip(), (smiles_b or "").strip()
    if not a or not b or not gateway_enabled():
        return None
    if a == b:
        return 1.0

    def compute() -> float | None:
        try:
            from rdkit import Chem, DataStructs  # type: ignore
            from rdkit.Chem import AllChem  # type: ignore

            ma, mb = Chem.MolFromSmiles(a), Chem.MolFromSmiles(b)
            if ma is None or mb is None:
                return None
            fa = AllChem.GetMorganFingerprintAsBitVect(ma, 2, nBits=2048)
            fb = AllChem.GetMorganFingerprintAsBitVect(mb, 2, nBits=2048)
            return round(float(DataStructs.TanimotoSimilarity(fa, fb)), 4)
        except Exception as exc:
            return degrade_return(logger, exc, "rdkit similarity failed", None)

    return _cached("mol_similarity", f"{a}|{b}", compute)


# ── patent / safety screens (ChemCrow only) ──────────────────────────────────


def patent_check(smiles: str) -> bool | None:
    """molbloom patent pre-screen. True=likely patented, False=novel, None=unknown."""
    smiles = (smiles or "").strip()
    if not smiles or not gateway_enabled() or not chemcrow_available():
        return None

    def compute() -> bool | None:
        def call() -> bool | None:
            tool = _chemcrow_tool("PatentCheck")
            if tool is None:
                return None
            out = _run_tool(tool, smiles)
            if not out:
                return None
            lowered = out.lower()
            if "not patented" in lowered or "novel" in lowered:
                return False
            if "patented" in lowered:
                return True
            return None

        return _run_with_timeout(call, None)

    return _cached("patent_check", smiles, compute)


def controlled_check(smiles_or_cas: str) -> bool | None:
    """Controlled-chemical screen. True=on a control list, False=clear, None=unknown."""
    q = (smiles_or_cas or "").strip()
    if not q or not gateway_enabled() or not chemcrow_available():
        return None

    def compute() -> bool | None:
        def call() -> bool | None:
            tool = _chemcrow_tool("ControlChemCheck", "ControlledChemicalCheck")
            if tool is None:
                return None
            out = _run_tool(tool, q)
            if not out:
                return None
            lowered = out.lower()
            if "not found" in lowered or "not a controlled" in lowered or "no known" in lowered:
                return False
            if "controlled" in lowered or "restricted" in lowered or "schedule" in lowered:
                return True
            return None

        return _run_with_timeout(call, None)

    return _cached("controlled_check", q, compute)


def explosive_check(cas: str) -> bool | None:
    """GHS explosive screen by CAS. True=explosive hazard, False=clear, None=unknown."""
    cas = (cas or "").strip()
    if not cas or not _CAS_RE.fullmatch(cas) or not gateway_enabled() or not chemcrow_available():
        return None

    def compute() -> bool | None:
        def call() -> bool | None:
            tool = _chemcrow_tool("ExplosiveCheck")
            if tool is None:
                return None
            out = _run_tool(tool, cas)
            if not out:
                return None
            lowered = out.lower()
            if "not" in lowered and "explosi" in lowered:
                return False
            if "explosi" in lowered:
                return True
            return None

        return _run_with_timeout(call, None)

    return _cached("explosive_check", cas, compute)


def safety_flags(smiles: str | None, cas: str | None = None) -> dict[str, bool | None]:
    """Combined safety screen used by requirement / recommend pipelines."""
    return {
        "controlled": controlled_check(smiles or cas or ""),
        "explosive": explosive_check(cas or ""),
    }


# ── aggregate profile ────────────────────────────────────────────────────────


def chemical_profile(q: str) -> dict[str, Any]:
    """Full chemical dossier for a name/CAS query.

    Extends the classic 3-tier ``lookup_chemical`` result with ChemCrow-backed
    fields: SMILES/CAS gap-fill, functional groups, molecular patent status
    and safety flags.  Shape is a strict superset of the lookup payload so the
    frontend can reuse existing typing.
    """
    from .chemical_lookup import lookup_chemical

    base = dict(lookup_chemical(q))
    smiles = base.get("smiles")
    cas = base.get("cas") or ""

    if gateway_enabled() and chemcrow_available():
        if not smiles:
            smiles = name_to_smiles(q)
            if smiles:
                base["smiles"] = smiles
                base["found"] = True
                if base.get("source") in ("none", "empty"):
                    base["source"] = "chemcrow"
        if not cas:
            resolved = name_to_cas(q)
            if resolved:
                cas = resolved
                base["cas"] = resolved

    base["func_groups"] = func_groups(smiles) if smiles else []
    base["patented"] = patent_check(smiles) if smiles else None
    base["safety"] = safety_flags(smiles, cas)
    base["chemtools"] = {
        "enabled": gateway_enabled(),
        "chemcrow_installed": chemcrow_available(),
    }
    return base
