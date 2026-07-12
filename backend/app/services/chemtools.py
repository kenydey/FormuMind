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
import json
import logging
import re
import threading
import time
from typing import Any, Callable, TypeVar
from urllib.parse import quote

from ..config import get_settings
from .errors import degrade_return, optional_import

logger = logging.getLogger(__name__)

_PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest"

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


def _httpx_available() -> bool:
    return optional_import("httpx")


def pubchempy_available() -> bool:
    return optional_import("pubchempy")


def molbloom_available() -> bool:
    return optional_import("molbloom")


def pubchem_available() -> bool:
    """A name/structure-resolution backend that does not need ChemCrow.

    ChemCrow's Query2SMILES/Query2CAS/ExplosiveCheck are thin wrappers over the
    public PubChem API; we can reach the same data directly through ``httpx``
    (already a hard dependency) or ``pubchempy``.  Treating either as present
    means these capabilities light up without the heavy ``chemcrow`` install.
    """
    return _httpx_available() or pubchempy_available()


def availability() -> dict[str, Any]:
    """Per-capability availability report (for /api/chemical/tools and UI).

    Each capability is available when *any* backend that can serve it is
    present — ChemCrow is one option, not the only one:

    * name→SMILES / name→CAS  → ChemCrow **or** PubChem (httpx/pubchempy);
    * functional groups / similarity → ChemCrow **or** RDKit (local);
    * molecular patent pre-screen → ChemCrow **or** ``molbloom`` (standalone);
    * explosive screen → ChemCrow **or** PubChem GHS classification;
    * controlled-chemical screen → ChemCrow only (ships the curated dataset);
    * chemistry web search → ChemCrow + SERPAPI_API_KEY.
    """
    enabled = gateway_enabled()
    cc = chemcrow_available()
    rd = rdkit_available()
    pubchem = pubchem_available()
    molbloom = molbloom_available()
    settings = get_settings()
    serp = bool(settings.serpapi_api_key)

    def cap(available: bool, hint: str | None) -> dict[str, Any]:
        return {"available": bool(enabled and available), "hint": None if (enabled and available) else hint}

    intel_hint = "pip install -e '.[intel]' 安装 ChemCrow 工具链"
    pubchem_hint = "需 httpx（已内置）或 pubchempy 以访问 PubChem；离线环境不可用"
    rdkit_hint = "pip install rdkit 或安装 intel extra"
    molbloom_hint = "pip install molbloom 启用分子专利布隆过滤器（或安装 intel extra）"
    return {
        "enabled": enabled,
        "chemcrow_installed": cc,
        "rdkit_installed": rd,
        "pubchem_available": pubchem,
        "molbloom_installed": molbloom,
        "capabilities": {
            "name_to_smiles": cap(cc or pubchem, pubchem_hint),
            "name_to_cas": cap(cc or pubchem, pubchem_hint),
            "func_groups": cap(cc or rd, rdkit_hint),
            "mol_similarity": cap(cc or rd, rdkit_hint),
            "patent_check": cap(cc or molbloom, molbloom_hint),
            "controlled_check": cap(cc, intel_hint + "（含管制品数据集）"),
            "explosive_check": cap(cc or pubchem, pubchem_hint + "；或安装 ChemCrow"),
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


# ── native PubChem backend (no ChemCrow required) ────────────────────────────


def _pubchem_get(path: str) -> Any | None:
    """GET a PubChem REST / PUG-View path and return parsed JSON, else None.

    Single choke-point for every native PubChem call: tests stub this one
    function to keep the network out of unit runs, and any transport / status
    failure degrades uniformly to ``None`` so callers stay neutral offline.
    """
    try:
        import httpx  # type: ignore
    except Exception:
        return None
    url = path if path.startswith("http") else f"{_PUBCHEM_BASE}{path}"
    timeout = float(get_settings().chemtools_timeout_s)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as exc:
        return degrade_return(logger, exc, "pubchem request failed", None)


# PubChem renamed its SMILES properties in 2025: requesting ``SMILES`` returns
# the absolute/isomeric SMILES under key ``SMILES``; ``ConnectivitySMILES`` is
# the former ``CanonicalSMILES``.  One request fetches both; we prefer the
# isomeric form when present.
_PUBCHEM_SMILES_KEYS = ("SMILES", "ConnectivitySMILES", "IsomericSMILES", "CanonicalSMILES")


def _pubchem_name_to_smiles(name: str) -> str | None:
    encoded = quote(name, safe="")
    data = _pubchem_get(
        f"/pug/compound/name/{encoded}/property/SMILES,ConnectivitySMILES/JSON"
    )
    props = ((data or {}).get("PropertyTable") or {}).get("Properties") or []
    if not props:
        return None
    row = props[0]
    for key in _PUBCHEM_SMILES_KEYS:
        smi = (row.get(key) or "").strip()
        if smi and _looks_like_smiles(smi):
            return smi
    return None


def _pubchem_name_to_cas(name: str) -> str | None:
    # PubChem deprecated the RegistryNumber xref endpoint; the canonical CAS now
    # lives among the compound synonyms (first CAS-format entry is the primary).
    encoded = quote(name, safe="")
    data = _pubchem_get(f"/pug/compound/name/{encoded}/synonyms/JSON")
    info = ((data or {}).get("InformationList") or {}).get("Information") or []
    for entry in info:
        for syn in entry.get("Synonym", []) or []:
            m = _CAS_RE.fullmatch(str(syn).strip())
            if m:
                return m.group(0)
    return None


def _pubchem_cid(query: str) -> int | None:
    encoded = quote(query, safe="")
    data = _pubchem_get(f"/pug/compound/name/{encoded}/cids/JSON")
    cids = ((data or {}).get("IdentifierList") or {}).get("CID") or []
    return int(cids[0]) if cids else None


# GHS hazard statement codes for explosives (H200–H208, Section 2.1).
_EXPLOSIVE_HCODES = ("H200", "H201", "H202", "H203", "H204", "H205", "H206", "H207", "H208")


def _pubchem_explosive(cas: str) -> bool | None:
    cid = _pubchem_cid(cas)
    if not cid:
        return None
    data = _pubchem_get(
        f"/pug_view/data/compound/{cid}/JSON?heading=GHS+Classification"
    )
    if not data:
        return None
    blob = json.dumps(data)
    if any(code in blob for code in _EXPLOSIVE_HCODES) or "Explos" in blob:
        return True
    # GHS classification present but no explosive hazard → clear.
    if "GHS" in blob or "Hazard" in blob or "Pictogram" in blob:
        return False
    return None


# ── native molbloom backend (no ChemCrow required) ───────────────────────────


def _molbloom_patented(smiles: str) -> bool | None:
    """molbloom SureChEMBL patent membership. None when molbloom is absent."""
    try:
        import molbloom  # type: ignore
    except Exception:
        return None
    try:
        return bool(
            molbloom.buy(smiles, canonicalize=rdkit_available(), catalog="surechembl")
        )
    except Exception as exc:
        return degrade_return(logger, exc, "molbloom patent check failed", None)


# ── name resolution (network; ChemCrow only) ─────────────────────────────────

_SMILES_CHARS_RE = re.compile(r"^[A-Za-z0-9@+\-\[\]\(\)=#$/\\%.:*]+$")


def _looks_like_smiles(text: str) -> bool:
    t = text.strip()
    return 1 < len(t) <= 500 and " " not in t and bool(_SMILES_CHARS_RE.match(t))


def name_to_smiles(name: str) -> str | None:
    """Resolve a chemical name to SMILES (ChemCrow Query2SMILES → PubChem)."""
    name = (name or "").strip()
    if not name or not gateway_enabled() or not (chemcrow_available() or pubchem_available()):
        return None

    def compute() -> str | None:
        def call() -> str | None:
            if chemcrow_available():
                tool = _chemcrow_tool("Query2SMILES")
                if tool is not None:
                    out = _run_tool(tool, name)
                    if out and _looks_like_smiles(out):
                        return out.strip()
            return _pubchem_name_to_smiles(name)

        return _run_with_timeout(call, None)

    return _cached("name_to_smiles", name, compute)


def name_to_cas(name: str) -> str | None:
    """Resolve a chemical name to a CAS number (ChemCrow Query2CAS → PubChem)."""
    name = (name or "").strip()
    if not name or not gateway_enabled() or not (chemcrow_available() or pubchem_available()):
        return None

    def compute() -> str | None:
        def call() -> str | None:
            if chemcrow_available():
                tool = _chemcrow_tool("Query2CAS")
                if tool is not None:
                    out = _run_tool(tool, name)
                    if out:
                        m = _CAS_RE.search(out)
                        if m:
                            return m.group(0)
            return _pubchem_name_to_cas(name)

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


DESCRIPTOR_NAMES = ("mol_wt", "logp", "tpsa", "hbd", "hba", "arom_rings")


def mol_descriptors(smiles: str) -> dict[str, float] | None:
    """RDKit physicochemical descriptors for one molecule (cached).

    Returns {mol_wt, logp, tpsa, hbd, hba, arom_rings} or None when RDKit is
    unavailable or the SMILES does not parse.  Deterministic and local — used
    by the opt-in v2 feature set in ``domain.features``.
    """
    smiles = (smiles or "").strip()
    if not smiles or not gateway_enabled():
        return None

    def compute() -> dict[str, float] | None:
        try:
            from rdkit import Chem  # type: ignore
            from rdkit.Chem import Descriptors, rdMolDescriptors  # type: ignore

            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return None
            return {
                "mol_wt": round(float(Descriptors.MolWt(mol)), 3),
                "logp": round(float(Descriptors.MolLogP(mol)), 3),
                "tpsa": round(float(Descriptors.TPSA(mol)), 3),
                "hbd": float(rdMolDescriptors.CalcNumHBD(mol)),
                "hba": float(rdMolDescriptors.CalcNumHBA(mol)),
                "arom_rings": float(rdMolDescriptors.CalcNumAromaticRings(mol)),
            }
        except Exception as exc:
            return degrade_return(logger, exc, "rdkit descriptors failed", None)

    return _cached("mol_descriptors", smiles, compute)


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
    """molbloom patent pre-screen. True=likely patented, False=novel, None=unknown.

    Prefers ChemCrow's PatentCheck tool when installed; otherwise calls the
    standalone ``molbloom`` SureChEMBL filter directly (same data source).
    """
    smiles = (smiles or "").strip()
    if not smiles or not gateway_enabled() or not (chemcrow_available() or molbloom_available()):
        return None

    def compute() -> bool | None:
        def call() -> bool | None:
            if chemcrow_available():
                tool = _chemcrow_tool("PatentCheck")
                if tool is not None:
                    out = _run_tool(tool, smiles)
                    if out:
                        lowered = out.lower()
                        if "not patented" in lowered or "novel" in lowered:
                            return False
                        if "patented" in lowered:
                            return True
                    return None
            return _molbloom_patented(smiles)

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
    """GHS explosive screen by CAS. True=explosive hazard, False=clear, None=unknown.

    Prefers ChemCrow's ExplosiveCheck; otherwise reads GHS Section 2.1
    classification straight from PubChem PUG-View (same underlying source).
    """
    cas = (cas or "").strip()
    if (
        not cas
        or not _CAS_RE.fullmatch(cas)
        or not gateway_enabled()
        or not (chemcrow_available() or pubchem_available())
    ):
        return None

    def compute() -> bool | None:
        def call() -> bool | None:
            if chemcrow_available():
                tool = _chemcrow_tool("ExplosiveCheck")
                if tool is not None:
                    out = _run_tool(tool, cas)
                    if out:
                        lowered = out.lower()
                        if "not" in lowered and "explosi" in lowered:
                            return False
                        if "explosi" in lowered:
                            return True
                    return None
            return _pubchem_explosive(cas)

        return _run_with_timeout(call, None)

    return _cached("explosive_check", cas, compute)


def safety_flags(smiles: str | None, cas: str | None = None) -> dict[str, bool | None]:
    """Combined safety screen used by requirement / recommend pipelines."""
    return {
        "controlled": controlled_check(smiles or cas or ""),
        "explosive": explosive_check(cas or ""),
    }


# ── formulation screening (recommend pipeline; advisory only) ────────────────

_SCREEN_MIN_WT_PCT = 0.5


def screen_formulation(form: Any) -> list[str]:
    """Molecular patent + controlled-chemical pre-screen for a Formulation.

    Returns advisory warning strings (never blocks).  Only ingredients with a
    SMILES and a non-negligible weight are screened; every check is cached in
    the gateway, and everything degrades to [] when chemcrow is absent.
    """
    if not (gateway_enabled() and chemcrow_available()):
        return []
    warnings: list[str] = []
    for ing in getattr(form, "ingredients", []) or []:
        smiles = getattr(ing, "smiles", None)
        if not smiles or (getattr(ing, "weight_pct", 0.0) or 0.0) < _SCREEN_MIN_WT_PCT:
            continue
        name = getattr(ing, "name", "") or smiles
        if patent_check(smiles) is True:
            warnings.append(
                f"IP 预筛：{name} 的分子结构已见于专利文献（molbloom），建议开展 FTO 检索"
            )
        if controlled_check(smiles) is True:
            warnings.append(f"合规预筛：{name} 命中管制化学品清单")
    return warnings


def func_group_summary(items: list[tuple[str, str]], max_items: int = 8) -> str:
    """One-line-per-material functional group summary for LLM prompts.

    ``items`` is a list of (name, smiles).  Returns "" when no groups can be
    resolved (offline), so callers can omit the prompt block entirely.
    """
    lines: list[str] = []
    for name, smiles in items[:max_items]:
        if not smiles:
            continue
        groups = func_groups(smiles)
        if groups:
            lines.append(f"- {name}: {', '.join(groups[:6])}")
    return "\n".join(lines)


# ── DOE factor chemistry review ──────────────────────────────────────────────

_EPOXIDE_LABELS = ("epoxide", "epoxy")
_AMINE_LABELS = ("amine",)
_CURE_FACTOR_NAMES = ("cure_temperature_c", "cure_time_min", "bake_temperature_c")


def review_doe_factors(req: Any, plan: Any) -> list[str]:
    """Chemistry sanity review of a DOE plan against the project materials.

    Advisory notes only (appended to plan.notes, never blocking):
    * controlled-chemical hits among project materials;
    * a reactive epoxide + amine pair present while the design has no cure
      factor — the classic missed-interaction in coating DOE.
    Empty when the gateway is disabled or nothing resolves (offline).
    """
    if not gateway_enabled():
        return []
    notes: list[str] = []
    materials = list(getattr(req, "materials", None) or [])
    group_map: dict[str, list[str]] = {}
    for m in materials:
        smiles = getattr(m, "smiles", None)
        if not smiles:
            continue
        name = getattr(m, "name", "") or smiles
        if controlled_check(smiles) is True:
            notes.append(f"化学审查：材料 {name} 命中管制化学品清单，实施 DOE 前请确认合规")
        groups = func_groups(smiles)
        if groups:
            group_map[name] = [g.lower() for g in groups]

    has_epoxide = any(
        any(lbl in g for lbl in _EPOXIDE_LABELS for g in groups)
        for groups in group_map.values()
    )
    has_amine = any(
        any(lbl in g for lbl in _AMINE_LABELS for g in groups)
        for groups in group_map.values()
    )
    if has_epoxide and has_amine:
        factor_names = {
            (getattr(f, "name", "") or "").lower() for f in getattr(plan, "factors", []) or []
        }
        if not factor_names & set(_CURE_FACTOR_NAMES):
            notes.append(
                "化学审查：材料含环氧基与胺基（反应对），当前设计未包含固化温度/时间因子，"
                "建议纳入以捕获固化动力学交互效应"
            )
    return notes


# ── formulation similarity dedup (recommend paths) ───────────────────────────

_DEDUP_THRESHOLD = 0.96


def _ingredient_similarity(a: Any, b: Any) -> float:
    """Structure/name similarity of two ingredients scaled by weight closeness."""
    wa = float(getattr(a, "weight_pct", 0.0) or 0.0)
    wb = float(getattr(b, "weight_pct", 0.0) or 0.0)
    if max(wa, wb) > 0:
        weight_closeness = 1.0 - min(1.0, abs(wa - wb) / max(wa, wb))
    else:
        weight_closeness = 1.0
    if (getattr(a, "name", "") or "").strip().lower() == (getattr(b, "name", "") or "").strip().lower():
        return weight_closeness
    sa, sb = getattr(a, "smiles", None), getattr(b, "smiles", None)
    if sa and sb:
        sim = mol_similarity(sa, sb)
        if sim is not None:
            return sim * weight_closeness
    return 0.0


def formulation_similarity(a: Any, b: Any) -> float:
    """Symmetric weighted similarity of two formulations in [0, 1]."""

    def directed(x: Any, y: Any) -> float:
        total = 0.0
        score = 0.0
        for ing in getattr(x, "ingredients", []) or []:
            w = float(getattr(ing, "weight_pct", 0.0) or 0.0)
            if w <= 0:
                continue
            best = max(
                (_ingredient_similarity(ing, other) for other in getattr(y, "ingredients", []) or []),
                default=0.0,
            )
            total += w
            score += w * best
        return score / total if total > 0 else 0.0

    return min(directed(a, b), directed(b, a))


def _strict_similarity(a: Any, b: Any) -> float:
    """Duplicate detector: the *weakest* ingredient match across both forms.

    A weighted average would mask a deliberate change in a minor component
    (e.g. an inhibitor at 5 wt% varied ±20% is a distinct design), so dedup
    uses the min — every single ingredient must have a near-perfect
    counterpart before two formulations count as duplicates.
    """

    def directed(x: Any, y: Any) -> float:
        worst = 1.0
        seen_any = False
        for ing in getattr(x, "ingredients", []) or []:
            if (getattr(ing, "weight_pct", 0.0) or 0.0) <= 0:
                continue
            seen_any = True
            best = max(
                (_ingredient_similarity(ing, other) for other in getattr(y, "ingredients", []) or []),
                default=0.0,
            )
            worst = min(worst, best)
        return worst if seen_any else 0.0

    return min(directed(a, b), directed(b, a))


def dedupe_similar_formulations(
    forms: list[Any], threshold: float = _DEDUP_THRESHOLD
) -> tuple[list[Any], list[str]]:
    """Drop near-duplicate formulations, keeping the earlier (higher-ranked) one.

    Callers pass score-sorted lists.  Name-identical composition matching works
    offline; structure-level matching engages when RDKit/ChemCrow is present.
    Returns (kept, notes).  No-op when the gateway is disabled or < 2 forms.
    """
    if not gateway_enabled() or len(forms) < 2:
        return forms, []
    kept: list[Any] = []
    notes: list[str] = []
    for form in forms:
        dupe_of = next(
            (k for k in kept if _strict_similarity(form, k) >= threshold), None
        )
        if dupe_of is None:
            kept.append(form)
        else:
            notes.append(
                f"已去重：{getattr(form, 'name', '?')} 与 {getattr(dupe_of, 'name', '?')} "
                f"分子组成高度相似（≥{threshold:.2f}）"
            )
    return kept, notes


# ── requirement material enrichment ──────────────────────────────────────────


def enrich_material_specs(materials: list[Any]) -> list[str]:
    """Fill missing SMILES on ``MaterialSpec`` rows in place; return warnings.

    Resolution order per material: curated catalog (offline, always on) →
    ChemCrow ``Query2SMILES`` (when installed).  Materials that resolve to a
    structure are screened against the controlled-chemical list; hits are
    reported as human-readable warnings, never as hard blocks.
    """
    from ..domain.knowledge import RAW_MATERIALS

    warnings: list[str] = []
    for m in materials or []:
        name = (getattr(m, "name", "") or "").strip()
        if not name:
            continue
        if not getattr(m, "smiles", None):
            spec = RAW_MATERIALS.get(name)
            if spec is None:
                lowered = name.lower()
                spec = next(
                    (row for key, row in RAW_MATERIALS.items() if key.lower() == lowered),
                    None,
                )
            if spec and spec.get("smiles"):
                m.smiles = spec["smiles"]
            else:
                resolved = name_to_smiles(name)
                if resolved:
                    m.smiles = resolved
        smiles = getattr(m, "smiles", None)
        if smiles and controlled_check(smiles) is True:
            warnings.append(f"{name}: 命中管制化学品清单，采购/使用需合规确认")
    return warnings


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

    if gateway_enabled() and (chemcrow_available() or pubchem_available()):
        if not smiles:
            smiles = name_to_smiles(q)
            if smiles:
                base["smiles"] = smiles
                base["found"] = True
                if base.get("source") in ("none", "empty"):
                    base["source"] = "chemcrow" if chemcrow_available() else "pubchem"
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
