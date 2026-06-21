"""POST /api/chat — Q&A grounded in loaded sources."""
from fastapi import APIRouter
from pydantic import BaseModel
from ..domain.schemas import Evidence
from ..services.llm import answer_question
from ..services.rag import active_rag_backend

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    sources: list[Evidence] = []
    domain: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Evidence]
    rag_backend: str = "tfidf"  # which retrieval backend served the citations


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    answer, citations = answer_question(
        question=req.question,
        sources=req.sources,
        domain=req.domain,
    )
    return ChatResponse(
        answer=answer, citations=citations, rag_backend=active_rag_backend()
    )
