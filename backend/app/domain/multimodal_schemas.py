"""Multi-modal KG fusion — vision table extraction and structural link schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class StructuralLinkType(str, Enum):
    HAS_INGREDIENT = "has_ingredient"
    HAS_PERFORMANCE = "has_performance"
    TESTED_ON_SUBSTRATE = "tested_on_substrate"


STRUCTURAL_LINK_TYPES = frozenset(m.value for m in StructuralLinkType)


class VisionSubstrate(BaseModel):
    name: str = ""
    pre_treatment: str = ""


class VisionIngredient(BaseModel):
    name: str = Field(min_length=1)
    concentration: float | None = None
    unit: str = ""
    role: str = ""


class VisionPerformance(BaseModel):
    metric: str = Field(min_length=1)
    value: float | str | None = None
    unit: str = ""
    conditions: str = ""


class VisionFormulation(BaseModel):
    example_id: str = "实施例1"
    substrate: VisionSubstrate = Field(default_factory=VisionSubstrate)
    ingredients: list[VisionIngredient] = Field(default_factory=list)
    performances: list[VisionPerformance] = Field(default_factory=list)


class VisionTableExtraction(BaseModel):
    table_type: Literal["comparison", "single", "unknown"] = "unknown"
    formulations: list[VisionFormulation] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    notes: str = ""


@dataclass(frozen=True)
class FormulationLink:
    src_entity_id: str
    dst_entity_id: str
    link_type: StructuralLinkType
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_sentence: str = ""


@dataclass
class StructuralExtractionResult:
    links: list[FormulationLink] = field(default_factory=list)
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class MultimodalFusionResult:
    vision: VisionTableExtraction | None = None
    extraction: StructuralExtractionResult | None = None
    vision_error: str | None = None
    warnings: list[str] = field(default_factory=list)
    persisted: bool = False
    links_written: int = 0
