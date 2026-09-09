"""SureChEMBL P3 APIs — KG ingest + human-reviewed embodiment drafts."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import surechembl_drafts, surechembl_kg

router = APIRouter(prefix="/surechembl", tags=["surechembl"])


class IngestDocumentRequest(BaseModel):
    doc_id: str = Field(min_length=3)
    title: str | None = None
    assignee: str | None = None
    pub_date: str | None = None
    url: str | None = None
    section: str | None = None
    fetch_chemistry: bool = True
    chemistry_limit: int = Field(default=25, ge=1, le=50)


class ExtractDraftRequest(BaseModel):
    doc_id: str = Field(min_length=3)
    title: str | None = None
    assignee: str | None = None
    pub_date: str | None = None
    url: str | None = None
    domain: str = "anticorrosion_coating"
    ingredient_limit: int = Field(default=8, ge=1, le=20)


class ConfirmDraftRequest(BaseModel):
    draft: dict[str, Any]


@router.post("/kg/ingest-document")
def ingest_document(body: IngestDocumentRequest) -> dict:
    """Upsert patent:scpn:* + chem:surechembl:* and appears_in/claimed_in."""
    try:
        return surechembl_kg.ingest_document_graph(
            doc_id=body.doc_id,
            title=body.title,
            assignee=body.assignee,
            pub_date=body.pub_date,
            url=body.url,
            fetch_chemistry=body.fetch_chemistry,
            chemistry_limit=body.chemistry_limit,
            section=body.section,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SureChEMBL KG 入库失败：{exc}") from exc


@router.post("/extract-example-draft")
def extract_example_draft(body: ExtractDraftRequest) -> dict:
    """Build a review-only Formulation draft (no DB writes)."""
    out = surechembl_drafts.extract_example_draft(
        doc_id=body.doc_id,
        title=body.title,
        assignee=body.assignee,
        pub_date=body.pub_date,
        url=body.url,
        domain=body.domain,
        ingredient_limit=body.ingredient_limit,
    )
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out.get("reason") or "无法提取草稿")
    return out


@router.post("/confirm-example-draft")
def confirm_example_draft(body: ConfirmDraftRequest) -> dict:
    """Human confirm: KG + pending materials only (no production formula pool)."""
    out = surechembl_drafts.confirm_example_draft(body.draft)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("reason") or "确认失败")
    return out
