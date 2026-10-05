"""TDS / performance table normalization — TableAsset → PropertySet.

W3-1 / P1-19. :mod:`table_contract` extracts tables as structured assets; this
module normalises them one step further into machine-readable property sets:

* :class:`Property` — one row: original name, normalised canonical key,
  parsed numeric value, original + normalised unit, raw cell text.
* :class:`PropertySet` — one table's properties plus ``warnings``.

Rules (conservative by design):

* Property names map through a chemistry-first CN/EN dictionary
  (``solids_content``, ``viscosity``, ``ph``, ``density``, ``voc``,
  ``hardness``, ``adhesion``, ``salt_spray``, ``gloss`` …). Unmapped names
  keep ``name_normalized == ""`` and are recorded in ``warnings`` — never
  guessed.
* Units are normalised **within the same dimension only** (symbol-level,
  e.g. ``cP`` → ``mPa.s``, ``℃`` → ``°C``, ``％`` → ``%``). No cross-dimension
  conversion is ever applied, so values are never silently rescaled.
* Numeric parsing: a single leading number (optional ``≥/≤/>`` prefix) parses
  to ``float``; anything ambiguous (ranges like ``2~4``, grades like ``2H``,
  multi-number cells) yields ``value=None`` with ``raw_text`` preserved.
* Only ``kind`` in ``("performance", "tds_sds", "recipe")`` is normalised;
  ``"other"`` tables are skipped with a warning.
* Never raises: per-row and per-table ``try/except`` keep the pipeline
  fail-open; catastrophic input yields an empty set with an error warning.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .table_contract import NAME_COL_HINTS, UNIT_COL_HINTS, VALUE_COL_HINTS, TableAsset

logger = logging.getLogger(__name__)

NORMALIZABLE_KINDS = ("performance", "tds_sds", "recipe")


# ── data model ──────────────────────────────────────────────────────────────


@dataclass
class Property:
    """One normalised property row."""

    name: str  # original property-name cell text
    name_normalized: str = ""  # canonical key ("" when unmapped)
    value: float | None = None  # parsed numeric value (None when not parseable)
    unit: str = ""  # original unit text
    unit_normalized: str = ""  # normalised unit symbol ("" when none)
    raw_text: str = ""  # original value cell text, always preserved

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Property":
        return cls(
            name=str(data.get("name", "")),
            name_normalized=str(data.get("name_normalized", "")),
            value=data.get("value"),
            unit=str(data.get("unit", "")),
            unit_normalized=str(data.get("unit_normalized", "")),
            raw_text=str(data.get("raw_text", "")),
        )


@dataclass
class PropertySet:
    """Normalised properties of one table, with warnings."""

    table_id: str
    source_id: str
    kind: str
    properties: list = field(default_factory=list)  # list[Property]
    warnings: list = field(default_factory=list)  # list[str]

    def to_dict(self) -> dict:
        return {
            "table_id": self.table_id,
            "source_id": self.source_id,
            "kind": self.kind,
            "properties": [
                p.to_dict() if isinstance(p, Property) else dict(p)
                for p in self.properties
            ],
            "warnings": list(self.warnings),
            "normalized_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PropertySet":
        return cls(
            table_id=str(data.get("table_id", "")),
            source_id=str(data.get("source_id", "")),
            kind=str(data.get("kind", "")),
            properties=[
                Property.from_dict(p) for p in (data.get("properties") or [])
                if isinstance(p, dict)
            ],
            warnings=[str(w) for w in (data.get("warnings") or [])],
        )


# ── property-name dictionary (chemistry-first, CN/EN) ───────────────────────

# canonical key → aliases. Matching is case-insensitive for ASCII and ignores
# a single trailing parenthetical (e.g. "黏度(25℃)" → "黏度").
_PROPERTY_ALIASES: dict[str, tuple[str, ...]] = {
    "solids_content": (
        "固含量", "固体含量", "固体分", "不挥发物", "不挥发物含量",
        "solid content", "solids content", "solids", "non-volatile content",
        "nonvolatile matter", "non volatile matter",
    ),
    "solids_volume": ("体积固含量", "体积固体份", "volume solids"),
    "viscosity": ("黏度", "粘度", "viscosity"),
    "density": ("密度", "比重", "density", "specific gravity"),
    "ph": ("ph值", "ph", "酸碱度"),
    "voc": ("voc", "voc含量", "voc content"),
    "hardness": ("硬度", "铅笔硬度", "摆杆硬度", "hardness", "pencil hardness"),
    "adhesion": (
        "附着力", "划格附着力", "划格", "adhesion",
        "cross-cut adhesion", "cross cut adhesion",
    ),
    "impact_resistance": (
        "冲击强度", "耐冲击性", "冲击", "impact resistance", "impact strength",
    ),
    "flexibility": ("柔韧性", "弯曲", "flexibility", "bend test"),
    "gloss": ("光泽", "光泽度", "gloss"),
    "color": ("颜色", "色泽", "color", "colour"),
    "appearance": ("外观", "状态", "appearance"),
    "fineness": ("细度", "研磨细度", "fineness", "fineness of grind"),
    "drying_time_surface": (
        "表干时间", "表干", "touch dry", "touch-dry", "surface dry",
    ),
    "drying_time_through": (
        "实干时间", "实干", "dry through", "hard dry", "through dry",
    ),
    "drying_time": ("干燥时间", "干燥", "drying time"),
    "flash_point": ("闪点", "flash point"),
    "salt_spray": (
        "盐雾试验", "耐盐雾性", "盐雾", "中性盐雾", "salt spray", "salt fog",
        # "耐盐雾性能" is the usual wording in Chinese datasheets and was the one row in a TDS that stayed unmapped.
        "耐盐雾性能", "耐盐雾", "耐盐雾试验", "耐中性盐雾", "盐雾性能", "中性盐雾试验",
        "neutral salt spray", "salt spray resistance", "salt spray test", "nss",
    ),
    "water_resistance": ("耐水性", "water resistance"),
    "alkali_resistance": ("耐碱性", "alkali resistance"),
    "acid_resistance": ("耐酸性", "acid resistance"),
    "chemical_resistance": ("耐化学品性", "耐化学性", "chemical resistance"),
    "humidity_resistance": (
        "耐湿热性", "耐湿热", "humidity resistance", "damp heat",
    ),
    "coverage": ("理论涂布率", "涂布率", "遮盖力", "coverage", "spreading rate"),
    "film_thickness_dry": ("干膜厚度", "膜厚", "dry film thickness", "dft"),
    "film_thickness_wet": ("湿膜厚度", "wet film thickness", "wft"),
    "mixing_ratio": ("混合比", "配比", "mixing ratio"),
    "pot_life": ("适用期", "活化期", "pot life"),
    "shelf_life": ("保质期", "贮存期", "shelf life", "storage life"),
    "thinner_ratio": ("稀释比", "稀释", "thinner", "dilution ratio"),
    "application_temp": ("施工温度", "application temperature"),
    "storage_temp": ("贮存温度", "storage temperature"),
}

_PAREN_SUFFIX_RE = re.compile(r"[（(][^）)]*[）)]\s*$")


def _norm_name_key(text: str) -> str:
    key = (text or "").strip()
    key = _PAREN_SUFFIX_RE.sub("", key).strip()
    key = re.sub(r"\s+", " ", key)
    # Lowercase ASCII letters even inside CJK strings ("pH值" → "ph值").
    return "".join(ch.lower() if ch.isascii() else ch for ch in key)


_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canon, _aliases in _PROPERTY_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL.setdefault(_norm_name_key(_alias), _canon)


def normalize_property_name(name: str) -> str:
    """Map a property-name cell to its canonical key ("" when unmapped)."""
    return _ALIAS_TO_CANONICAL.get(_norm_name_key(name), "")


# ── unit normalization (same dimension, symbol-level only) ──────────────────

# key: lowercased raw symbol → canonical symbol. 1:1 equivalents only
# (e.g. 1 cP == 1 mPa·s); never a rescaling conversion.
_UNIT_MAP: dict[str, str] = {
    # 黏度 viscosity
    "mpa·s": "mPa.s", "mpa.s": "mPa.s", "cp": "mPa.s", "cps": "mPa.s",
    "pa·s": "Pa.s", "pa.s": "Pa.s", "ku": "KU",
    # 密度 density
    "g/cm³": "g/cm3", "g/cm3": "g/cm3",
    "g/ml": "g/mL", "g/cc": "g/mL",
    "kg/m³": "kg/m3", "kg/m3": "kg/m3",
    "kg/l": "kg/L", "g/l": "g/L",
    # 分数 fraction
    "%": "%", "％": "%", "percent": "%", "pct": "%",
    # 时间 time
    "h": "h", "hr": "h", "hrs": "h", "小时": "h",
    "min": "min", "mins": "min", "分钟": "min",
    "s": "s", "sec": "s", "secs": "s", "秒": "s",
    "d": "d", "day": "d", "days": "d", "天": "d",
    # 温度 temperature
    "°c": "°C", "℃": "°C", "°f": "°F",
    "k": "K",
    # 长度/厚度 length
    "μm": "μm", "um": "μm", "微米": "μm",
    "mm": "mm", "毫米": "mm", "cm": "cm", "mil": "mil",
    # 光泽 gloss
    "gu": "GU",
    # 等级 grade
    "级": "级",
}

# Full-width → half-width (digits and common symbols).
_FW_TRANS = str.maketrans("０１２３４５６７８９．，－＋％", "0123456789.,-+%")

_NUM_RE = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_CMP_PREFIX_CHARS = "≥≤><≧≦=＝~～ \t"


def normalize_unit(raw: str) -> str:
    """Normalise a unit symbol; unknown symbols pass through unchanged."""
    key = (raw or "").translate(_FW_TRANS).strip()
    if not key:
        return ""
    return _UNIT_MAP.get(key.lower(), key)


def _split_number_unit(text: str) -> tuple[float | None, str, bool]:
    """Split a value cell into (number, trailing unit text, ambiguous).

    Returns ``ambiguous=True`` when a number is followed by unrecognised
    trailing text (ranges like ``2~4``, grades like ``2H``) — the caller
    should then keep ``value=None`` and preserve the raw text.

    A single ASCII letter glued to the number (``2H``) is always ambiguous:
    it may be a grade, not a unit. Multi-char symbols (``mPa·s``, ``%``)
    and space-separated units (``1000 h``) are unambiguous.
    """
    t = (text or "").translate(_FW_TRANS).strip()
    if not t:
        return None, "", False
    t2 = t.lstrip(_CMP_PREFIX_CHARS)
    m = _NUM_RE.match(t2)
    if not m:
        return None, "", False
    try:
        value = float(m.group(0).replace(",", ""))
    except ValueError:
        return None, "", False
    rest = t2[m.end():].strip()
    if not rest:
        return value, "", False
    glued = not t2[m.end():][:1].isspace()
    if glued and len(rest) == 1 and rest.isascii() and rest.isalpha():
        return None, rest, True  # e.g. "2H" — grade, not a unit
    if rest.lower() in _UNIT_MAP:
        return value, rest, False
    return None, rest, True


# ── column detection ────────────────────────────────────────────────────────

_NAME_COL_HINTS = NAME_COL_HINTS
_VALUE_COL_HINTS = VALUE_COL_HINTS
_UNIT_COL_HINTS = UNIT_COL_HINTS


def _header_hits(header: str, hints: tuple[str, ...]) -> bool:
    h = _norm_name_key(header)
    return any(hint in h for hint in hints)


def _detect_columns(headers: list[str]) -> tuple[int, int, int | None]:
    """Return (name_col, value_col, unit_col|None)."""
    n = len(headers)
    name_col = next(
        (i for i, h in enumerate(headers) if _header_hits(h, _NAME_COL_HINTS)), 0
    )
    value_col = next(
        (i for i, h in enumerate(headers) if _header_hits(h, _VALUE_COL_HINTS)),
        None,
    )
    unit_col = next(
        (i for i, h in enumerate(headers) if _header_hits(h, _UNIT_COL_HINTS)),
        None,
    )
    if value_col is None:
        value_col = 1 if n > 1 else 0
    if value_col == name_col and n > 1:
        value_col = 1 if name_col != 1 else 0
    return name_col, value_col, unit_col


def _unit_from_header(header: str) -> str:
    """Extract a unit from a value-column header like "配比 (phr)"."""
    m = re.search(r"[（(]([^（）()]+)[）)]", header or "")
    if not m:
        return ""
    cand = m.group(1).translate(_FW_TRANS).strip()
    return _UNIT_MAP[cand.lower()] if cand.lower() in _UNIT_MAP else ""


# ── main entry points ───────────────────────────────────────────────────────


def _empty_set(asset: TableAsset, warning: str) -> PropertySet:
    return PropertySet(
        table_id=getattr(asset, "table_id", "") or "",
        source_id=getattr(asset, "source_id", "") or "",
        kind=getattr(asset, "kind", "") or "",
        properties=[],
        warnings=[warning] if warning else [],
    )


def normalize_table(asset: TableAsset) -> PropertySet:
    """Normalise one :class:`TableAsset` into a :class:`PropertySet`.

    Never raises — on any error returns an empty set with an error warning.
    """
    try:
        kind = getattr(asset, "kind", "") or ""
        if kind not in NORMALIZABLE_KINDS:
            return _empty_set(asset, f"skipped: kind={kind!r} is not normalizable")

        headers = [str(h) for h in (getattr(asset, "headers", None) or [])]
        rows = getattr(asset, "rows", None) or []
        if len(headers) < 2:
            return _empty_set(asset, "skipped: fewer than 2 columns")

        name_col, value_col, unit_col = _detect_columns(headers)
        header_unit = _unit_from_header(headers[value_col]) if value_col < len(headers) else ""

        properties: list[Property] = []
        warnings: list[str] = []
        skipped_rows = 0

        for row in rows:
            try:
                cells = [str(c) for c in (row or [])]
                need = max(name_col, value_col, (unit_col or 0))
                if len(cells) <= need:
                    skipped_rows += 1
                    continue
                name = cells[name_col].strip()
                value_text = cells[value_col].strip()
                if not name and not value_text:
                    continue

                canon = normalize_property_name(name)
                if not canon and name:
                    warnings.append(f"unmapped_property: {name}")

                value, inline_unit_raw, ambiguous = _split_number_unit(value_text)
                if ambiguous:
                    warnings.append(f"ambiguous_value: {value_text}")
                    value = None
                    inline_unit_raw = ""  # trailing junk is not a unit

                if unit_col is not None:
                    unit_raw = cells[unit_col].strip()
                elif inline_unit_raw:
                    unit_raw = inline_unit_raw
                else:
                    unit_raw = header_unit

                properties.append(
                    Property(
                        name=name,
                        name_normalized=canon,
                        value=value,
                        unit=unit_raw,
                        unit_normalized=normalize_unit(unit_raw),
                        raw_text=value_text,
                    )
                )
            except Exception:
                logger.exception("table_normalize: skipping bad row (fail-open)")
                skipped_rows += 1

        if skipped_rows:
            warnings.append(f"skipped_rows: {skipped_rows}")
        return PropertySet(
            table_id=getattr(asset, "table_id", "") or "",
            source_id=getattr(asset, "source_id", "") or "",
            kind=kind,
            properties=properties,
            warnings=warnings,
        )
    except Exception:
        logger.exception("table_normalize: normalize_table failed (fail-open)")
        return _empty_set(asset, "error: normalize_table raised")


def normalize_tables(assets: list[TableAsset] | None) -> list[PropertySet]:
    """Normalise a list of assets; fail-open per asset. Never raises."""
    out: list[PropertySet] = []
    for asset in assets or []:
        try:
            out.append(normalize_table(asset))
        except Exception:
            logger.exception("table_normalize: asset failed (fail-open)")
            out.append(_empty_set(asset, "error: normalize_tables raised"))
    return out
