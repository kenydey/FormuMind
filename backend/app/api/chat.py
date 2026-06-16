"""POST /api/chat — Q&A grounded in loaded sources."""
from fastapi import APIRouter
from pydantic import BaseModel
from ..domain.schemas import Evidence
from ..services.llm import answer_question

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    sources: list[Evidence] = []
    domain: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Evidence]


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    answer, citations = answer_question(
        question=req.question,
        sources=req.sources,
        domain=req.domain,
    )
    return ChatResponse(answer=answer, citations=citations)
