"""Chemical lookup API — CAS / name cross-reference, ChemCrow profile,
and structure-image recognition (image → SMILES → MolJSON → similar hits)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from ..domain.schemas import MaterialSpec
from ..services.chemical_lookup import lookup_chemical
from ..services.chemtools import availability, chemical_profile, enrich_material_specs
from ..services.structure_recognize import recognize_structure_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chemistry"])


class EnrichMaterialsRequest(BaseModel):
    materials: list[MaterialSpec] = Field(default_factory=list)


class EnrichMaterialsResponse(BaseModel):
    materials: list[MaterialSpec]
    warnings: list[str] = Field(default_factory=list)


@router.get("/chemical/lookup")
def chemical_lookup(q: str = Query(..., min_length=1, description="中文名/英文名/CAS No.")) -> dict:
    """Look up chemical metadata by name or CAS (PubChem + catalog, 24h cache)."""
    return lookup_chemical(q)


@router.get("/chemical/profile")
def chemical_profile_endpoint(
    q: str = Query(..., min_length=1, description="中文名/英文名/CAS No."),
) -> dict:
    """Full chemical dossier: lookup + functional groups + molecular patent
    pre-screen + controlled/explosive safety flags (ChemCrow tool gateway).

    Superset of ``/chemical/lookup``; ChemCrow-backed fields degrade to
    neutral values when the intel extra is not installed.
    """
    return chemical_profile(q)


@router.get("/chemical/tools")
def chemical_tools_status() -> dict:
    """Availability report for the ChemCrow tool gateway (per capability)."""
    return availability()


@router.post("/chemical/enrich-materials", response_model=EnrichMaterialsResponse)
def enrich_materials_endpoint(req: EnrichMaterialsRequest) -> EnrichMaterialsResponse:
    """Fill missing SMILES on a material list (catalog → ChemCrow) and run a
    controlled-chemical screen. Warnings are advisory — never hard blocks."""
    warnings = enrich_material_specs(req.materials)
    return EnrichMaterialsResponse(materials=req.materials, warnings=warnings)


@router.post("/chemical/structure")
async def structure_image_endpoint(
    image: UploadFile = File(..., description="结构图（PNG/JPG/WebP，≤10MB）"),
    threshold: float = Form(0.6, ge=0.0, le=1.0, description="相似度阈值"),
    top_k: int = Form(5, ge=1, le=20, description="相似材料返回数"),
) -> dict:
    """识别上传的结构图 → SMILES → MolJSON → 相似材料命中。

    供「图片创建项目 / 图片提问」共用。识别失败不抛错——返回
    ``recognized=False`` 与 warning，由前端决定降级为纯文字流程。
    """
    content = await image.read()
    try:
        return recognize_structure_image(
            content,
            filename=image.filename or "structure.png",
            threshold=threshold,
            top_k=top_k,
        )
    except Exception as exc:  # defensive: pipeline never raises, but be safe
        logger.exception("structure endpoint failed")
        return {
            "recognized": False,
            "smiles": None,
            "moljson": None,
            "hits": [],
            "image_sha": "",
            "cached": False,
            "warnings": [f"识别服务异常：{exc}"],
            "error": str(exc),
        }
