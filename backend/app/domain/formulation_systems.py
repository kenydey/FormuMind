"""Formulation-system knowledge base for recommend prompt injection.

A product_type free-text keyword (e.g. 「含聚合物/树脂的乳液型镁合金钝化剂」,
「自沉积型」, 「无铬型」, 「C5 级重防腐」) is matched against a curated table of
metal-surface-treatment systems and ISO 12944 corrosion grades. The matched
systems' hard constraints (must-include / must-exclude ingredients, process
constraints, metric ranges) are injected into the recommend prompt so the LLM
produces a physically sensible formula instead of free-associating.

This is the generalised successor to the single hard-coded rule that only knew
「含聚合物/树脂的乳液型」. Data + pure functions only — no I/O, easy to test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FormulationSystem:
    id: str
    name: str
    keywords: tuple[str, ...]
    domain: str  # "surface_treatment" | "anticorrosion_coating" | "degreaser"
    prompt_hint: str = ""
    # Reserved for the phase-2 predictor linkage (system → formula / ranges).
    must_include_roles: tuple[str, ...] = ()
    must_exclude: str = ""
    metric_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class CorrosionGrade:
    id: str
    keywords: tuple[str, ...]
    salt_spray_hours: tuple[float, float]  # (min, max) neutral salt spray
    film_thickness_um: int
    prompt_hint: str


# ── Corrosion grades (ISO 12944-2 / Qualisteelcoat C3-C5) ───────────────────
# ISO 12944 defines environment categories; the NSS-hour figures are the
# industry-conventional equivalents used to specify coating systems.

CORROSION_GRADES: dict[str, CorrosionGrade] = {
    "C1": CorrosionGrade(
        id="C1",
        keywords=("c1级", "c1", "iso 12944 c1", "iso12944c1"),
        salt_spray_hours=(0.0, 120.0),
        film_thickness_um=80,
        prompt_hint=(
            "C1 (very low corrosivity, dry indoor): single thin coat is enough, "
            "NSS <120h."
        ),
    ),
    "C2": CorrosionGrade(
        id="C2",
        keywords=("c2级", "c2", "iso 12944 c2", "iso12944c2"),
        salt_spray_hours=(120.0, 240.0),
        film_thickness_um=80,
        prompt_hint=(
            "C2 (low corrosivity, occasional condensation): single primer coat, "
            "NSS 120-240h."
        ),
    ),
    "C3": CorrosionGrade(
        id="C3",
        keywords=("c3级", "c3-m", "c3m", "c3", "iso 12944 c3", "iso12944c3"),
        salt_spray_hours=(240.0, 480.0),
        film_thickness_um=160,
        prompt_hint=(
            "C3 (medium corrosivity, urban/industrial): zinc-rich primer + "
            "intermediate + topcoat, ~160μm, NSS 240-480h."
        ),
    ),
    "C4": CorrosionGrade(
        id="C4",
        keywords=("c4级", "c4-m", "c4m", "c4", "iso 12944 c4", "iso12944c4"),
        salt_spray_hours=(480.0, 720.0),
        film_thickness_um=200,
        prompt_hint=(
            "C4 (high corrosivity, industrial/coastal): zinc-rich primer + MIO "
            "intermediate + PU topcoat, ~200μm, NSS 480-720h."
        ),
    ),
    "C5": CorrosionGrade(
        id="C5",
        keywords=("c5级", "c5-m", "c5m", "c5", "iso 12944 c5", "iso12944c5", "重防腐", "海洋级"),
        salt_spray_hours=(720.0, 1440.0),
        film_thickness_um=240,
        prompt_hint=(
            "C5 (very high corrosivity, marine): zinc-rich primer + MIO "
            "intermediate + PU/fluorocarbon topcoat (3-4 coats), ~240μm, "
            "NSS 720-1440h."
        ),
    ),
    "CX": CorrosionGrade(
        id="CX",
        keywords=("cx", "c5-i", "c5i", "海上", "极端腐蚀"),
        salt_spray_hours=(1440.0, 3000.0),
        film_thickness_um=280,
        prompt_hint=(
            "CX (extreme, offshore): zinc-rich primer + high-build intermediate "
            "+ fluorocarbon topcoat, >280μm, NSS >1440h."
        ),
    ),
}


# ── Formulation systems ──────────────────────────────────────────────────────

_FORMULATION_SYSTEMS: dict[str, FormulationSystem] = {
    # A. 前处理清洗 (degreaser)
    "alkaline_degreaser": FormulationSystem(
        id="alkaline_degreaser", name="碱性脱脂", domain="degreaser",
        keywords=("碱性脱脂", "脱脂", "degreaser", "除油", "degreasing"),
        prompt_hint=(
            "Alkaline degreaser: NaOH / sodium phosphate / silicate builder + "
            "surfactant, aim for the cleaning_efficiency target."
        ),
    ),
    "acid_pickling": FormulationSystem(
        id="acid_pickling", name="酸洗除锈", domain="degreaser",
        keywords=("酸洗", "pickling", "除锈", "去氧化皮", "酸洗除锈"),
        prompt_hint=(
            "Acid pickling: strong acid (HCl / H2SO4 / H3PO4) + corrosion "
            "inhibitor for rust/scale removal."
        ),
    ),
    "neutral_cleaner": FormulationSystem(
        id="neutral_cleaner", name="中性清洗", domain="degreaser",
        keywords=("中性清洗", "中性脱脂", "neutral cleaner"),
        prompt_hint="Neutral cleaner: pH-neutral surfactant system.",
    ),
    "dewaxing": FormulationSystem(
        id="dewaxing", name="除蜡", domain="degreaser",
        keywords=("除蜡", "dewaxing", "脱蜡"),
        prompt_hint="Dewaxing: solvent / emulsifying dewaxer to remove polishing wax.",
    ),

    # B. 转化膜 (surface_treatment)
    "zinc_phosphate": FormulationSystem(
        id="zinc_phosphate", name="锌系磷化", domain="surface_treatment",
        keywords=("锌系磷化", "锌磷化", "zinc phosphate", "磷化", "phosphate", "phosphating"),
        prompt_hint=(
            "Zinc phosphate conversion coating: zinc phosphate + accelerator "
            "(nitrate/chlorate), free-acid/total-acid control; bare-film NSS 2-24h."
        ),
        metric_ranges={"salt_spray_hours": (2.0, 24.0)},
    ),
    "iron_phosphate": FormulationSystem(
        id="iron_phosphate", name="铁系磷化", domain="surface_treatment",
        keywords=("铁系磷化", "铁磷化", "iron phosphate"),
        prompt_hint=(
            "Iron phosphate conversion: thin amorphous phosphate film 0.2-1 g/m²; "
            "NSS 1-8h."
        ),
        metric_ranges={"salt_spray_hours": (1.0, 8.0)},
    ),
    "manganese_phosphate": FormulationSystem(
        id="manganese_phosphate", name="锰系磷化", domain="surface_treatment",
        keywords=("锰系磷化", "锰磷化", "manganese phosphate"),
        prompt_hint=(
            "Manganese phosphate conversion: wear-resistant thick phosphate; "
            "NSS 8-24h."
        ),
        metric_ranges={"salt_spray_hours": (8.0, 24.0)},
    ),
    "hexavalent_chromate": FormulationSystem(
        id="hexavalent_chromate", name="六价铬钝化", domain="surface_treatment",
        keywords=("六价铬", "铬酸盐", "chromate", "hexavalent"),
        prompt_hint=(
            "Hexavalent chromium passivation (Cr6+): NOTE Cr6+ is REACH-restricted; "
            "NSS 24-100h."
        ),
        must_exclude="",  # Cr6+ itself is the active; flag REACH in hint
        metric_ranges={"salt_spray_hours": (24.0, 100.0)},
    ),
    "trivalent_chromate": FormulationSystem(
        id="trivalent_chromate", name="三价铬钝化", domain="surface_treatment",
        keywords=("三价铬", "trivalent chromium", "trivalent"),
        prompt_hint="Trivalent chromium passivation (Cr3+); NSS 24-72h.",
        metric_ranges={"salt_spray_hours": (24.0, 72.0)},
    ),
    "chrome_free": FormulationSystem(
        id="chrome_free", name="无铬钝化", domain="surface_treatment",
        keywords=("无铬", "chrome-free", "chromium-free", "锆化", "钛化", "无铬钝化"),
        prompt_hint=(
            "Chrome-free passivation: NO chromate; zirconium/titanium/silane/"
            "rare-earth (Ce) conversion coating, NSS 50-200h."
        ),
        must_exclude="chromate (Cr6+)",
        metric_ranges={"salt_spray_hours": (50.0, 200.0)},
    ),
    "silane": FormulationSystem(
        id="silane", name="硅烷化", domain="surface_treatment",
        keywords=("硅烷化", "硅烷", "silane", "silanization"),
        prompt_hint="Silane/siloxane conversion: organosilane coupling agent; NSS 24-100h.",
        metric_ranges={"salt_spray_hours": (24.0, 100.0)},
    ),
    "pickle_phosphating": FormulationSystem(
        id="pickle_phosphating", name="酸洗磷化二合一", domain="surface_treatment",
        keywords=("二合一", "酸洗磷化", "除锈磷化", "pickle phosphating"),
        prompt_hint=(
            "Pickle-phosphating 2-in-1: acid + phosphate combined for simultaneous "
            "rust removal and phosphating."
        ),
    ),
    "passivation_sealing": FormulationSystem(
        id="passivation_sealing", name="钝化封闭二合一", domain="surface_treatment",
        keywords=("钝化封闭", "封闭二合一", "passivation sealing"),
        prompt_hint="Passivation + sealing 2-in-1: combined passivation and sealer.",
    ),

    # C. 铝材处理 (surface_treatment)
    "anodizing": FormulationSystem(
        id="anodizing", name="阳极氧化", domain="surface_treatment",
        keywords=("阳极氧化", "anodizing", "anodising", "氧化膜"),
        prompt_hint=(
            "Anodizing: sulfuric / oxalic / hard anodizing to grow an aluminum "
            "oxide film."
        ),
    ),
    "aluminum_line": FormulationSystem(
        id="aluminum_line", name="铝材前处理专线", domain="surface_treatment",
        keywords=("铝材前处理", "铝型材前处理", "铝合金前处理", "碱蚀", "化抛", "出光"),
        prompt_hint=(
            "Aluminum finishing line: degrease → alkaline etch → chemical polish "
            "→ desmut sequence for aluminum profiles."
        ),
    ),

    # D. 有机涂层 (surface_treatment / anticorrosion_coating)
    "autodeposition": FormulationSystem(
        id="autodeposition", name="自沉积", domain="surface_treatment",
        keywords=("自沉积", "autodeposition", "autophoretic"),
        prompt_hint=(
            "Autodeposition (autophoretic) coating — acidic aqueous emulsion "
            "pH 2-4 (HF or organic acid; HF etches the steel surface):\n"
            "- Resin: epoxy-acrylic emulsion and/or polyurethane emulsion; typical "
            "epoxy:acrylic ratio 50:50 to 70:30 (epoxy for adhesion/corrosion "
            "resistance, acrylic for flexibility/weatherability)\n"
            "- Initiator: oxidizer (H2O2) + Fe3+ (10-100 ppm, generated by HF "
            "etching); excess Fe3+ destabilizes the bath\n"
            "- Reactive monomer: methacrylic acid / acrylate esters for "
            "post-deposition crosslinking\n"
            "- Coalescing agent: ester-alcohol (lower MFFT)\n"
            "- Pigment: carbon black (typical)\n"
            "- Stabilizer: keep emulsion stable in acidic conditions\n"
            "- Cure: 150-170°C × 10-20 min (depends on resin and crosslinker)\n"
            "- NSS 500-1440h"
        ),
        metric_ranges={"salt_spray_hours": (500.0, 1440.0)},
    ),
    "electrocoat": FormulationSystem(
        id="electrocoat", name="电泳", domain="surface_treatment",
        keywords=("电泳", "electrocoat", "e-coat", "ecoat", "阴极电泳", "阳极电泳", "ced"),
        prompt_hint=(
            "Electrocoat (E-coat): waterborne resin emulsion, electrophoretic "
            "deposition, cure 150-200°C; NSS 500-1000h."
        ),
        metric_ranges={"salt_spray_hours": (500.0, 1000.0)},
    ),
    "organic_emulsion": FormulationSystem(
        id="organic_emulsion", name="有机乳液钝化", domain="surface_treatment",
        keywords=("乳液", "emulsion", "聚合物", "polymer", "树脂", "有机无机"),
        prompt_hint=(
            "Organic emulsion passivation: MUST include a polymer resin (acrylic / "
            "epoxy / polyurethane emulsion) as the film-forming binder; NSS 500-1440h."
        ),
        must_include_roles=("resin",),
        metric_ranges={"salt_spray_hours": (500.0, 1440.0)},
    ),

    # E. 防腐涂料 (anticorrosion_coating)
    "zinc_rich_primer": FormulationSystem(
        id="zinc_rich_primer", name="富锌底漆", domain="anticorrosion_coating",
        keywords=("富锌", "锌粉底漆", "zinc rich", "环氧富锌", "无机富锌"),
        prompt_hint="Zinc-rich primer: zinc dust ≥70% (epoxy or inorganic silicate), cathodic protection.",
    ),
    "mio_intermediate": FormulationSystem(
        id="mio_intermediate", name="云铁中间漆", domain="anticorrosion_coating",
        keywords=("云铁", "云母氧化铁", "micaceous iron", "中间漆"),
        prompt_hint="Micaceous iron oxide (MIO) intermediate: lamellar barrier pigment.",
    ),
    "pu_topcoat": FormulationSystem(
        id="pu_topcoat", name="聚氨酯面漆", domain="anticorrosion_coating",
        keywords=("聚氨酯面漆", "pu面漆", "丙烯酸面漆", "氟碳面漆", "topcoat"),
        prompt_hint="Polyurethane / acrylic / fluorocarbon topcoat: weatherable finish.",
    ),
    "waterborne_anticorr": FormulationSystem(
        id="waterborne_anticorr", name="水性防腐涂料", domain="anticorrosion_coating",
        keywords=("水性防腐", "水性涂料", "waterborne coating"),
        prompt_hint="Waterborne anticorrosion coating: waterborne resin system.",
    ),
    "high_build": FormulationSystem(
        id="high_build", name="厚浆型", domain="anticorrosion_coating",
        keywords=("厚浆", "high build", "高膜厚"),
        prompt_hint="High-build coating: single coat >100μm, high-solids or solvent-free.",
    ),
    "solvent_free": FormulationSystem(
        id="solvent_free", name="无溶剂型", domain="anticorrosion_coating",
        keywords=("无溶剂", "solvent-free", "solvent free", "零voc", "100%固含"),
        prompt_hint="Solvent-free coating: 100% solids, zero VOC.",
    ),

    # F. 后处理 (surface_treatment)
    "sealer": FormulationSystem(
        id="sealer", name="封闭剂", domain="surface_treatment",
        keywords=("封闭剂", "封闭", "sealer", "封孔", "后处理"),
        prompt_hint="Sealer: post-treatment to enhance corrosion resistance.",
    ),
    "rust_preventive": FormulationSystem(
        id="rust_preventive", name="防锈剂", domain="surface_treatment",
        keywords=("防锈剂", "防锈", "rust preventive", "防锈油"),
        prompt_hint="Rust preventive: temporary corrosion-protection film.",
    ),
}

# Public alias (frozen mapping for external reads).
FORMULATION_SYSTEMS: dict[str, FormulationSystem] = _FORMULATION_SYSTEMS


_GRADE_SHORT_WORDS = re.compile(r"^[a-z][0-9]$|^cx$")  # c3/c4/c5/cx need word bounds


def _kw_hit(text: str, kw: str) -> bool:
    """Substring match; short grade tokens (c3/c4/c5/cx) use word boundaries."""
    if _GRADE_SHORT_WORDS.fullmatch(kw):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text))
    return kw in text


def match_systems(product_type: str) -> list[FormulationSystem]:
    """Match formulation systems from a product_type keyword.

    Longest keyword wins: a more specific match (e.g. 「铁系磷化」 → iron_phosphate)
    consumes its text span, so the generic keyword it contains (「磷化」 → zinc
    phosphate's generic alias) does not also fire. Multiple non-overlapping
    systems can match simultaneously (e.g. 「无铬乳液型」 → chrome_free + organic_emulsion).
    """
    text = (product_type or "").lower()
    if not text:
        return []

    pairs: list[tuple[str, str]] = []
    for sid, sys in _FORMULATION_SYSTEMS.items():
        for kw in sys.keywords:
            pairs.append((kw.lower(), sid))
    pairs.sort(key=lambda p: -len(p[0]))

    matched: dict[str, FormulationSystem] = {}
    covered: list[tuple[int, int]] = []
    for kw, sid in pairs:
        start = 0
        while True:
            idx = text.find(kw, start)
            if idx == -1:
                break
            end = idx + len(kw)
            if not any(a <= idx and end <= b for a, b in covered):
                matched[sid] = _FORMULATION_SYSTEMS[sid]
                covered.append((idx, end))
                break
            start = idx + 1
    return list(matched.values())


def match_grade(product_type: str) -> CorrosionGrade | None:
    """Match an ISO 12944 corrosion grade (C1-C5/CX) from the keyword."""
    text = (product_type or "").lower()
    if not text:
        return None
    pairs: list[tuple[str, str]] = []
    for gid, g in CORROSION_GRADES.items():
        for kw in g.keywords:
            pairs.append((kw.lower(), gid))
    pairs.sort(key=lambda p: -len(p[0]))
    for kw, gid in pairs:
        if _kw_hit(text, kw):
            return CORROSION_GRADES[gid]
    return None


def normalize_key(text: str) -> str:
    """Normalise a product_type into a stable cache key (lowercase, strip
    whitespace/punctuation, keep CJK + alphanumerics)."""
    import re

    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (text or "").lower())


def build_system_prompt_block(product_type: str) -> str:
    """Render the matched systems' hard constraints as an English prompt block."""
    systems = match_systems(product_type)
    grade = match_grade(product_type)
    if not systems and grade is None:
        return ""

    lines: list[str] = []
    for s in systems:
        lines.append(f"- {s.prompt_hint}")
    if grade is not None:
        lines.append(f"- Corrosion grade {grade.id}: {grade.prompt_hint}")

    if not lines:
        return ""
    return (
        "Formulation-system requirements (HARD constraints — must be satisfied):\n"
        + "\n".join(lines)
        + "\n"
    )
