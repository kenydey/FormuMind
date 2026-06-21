"""Self-driving loop endpoint (v0.6, P0).

POST /api/loop/iterate kicks off one closed-loop turn — optimize with the
latest blended models, then propose the next active-learning DOE batch — and
returns a task handle the client polls via GET /api/tasks/{id}.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..domain.schemas import LoopRequest
from ..worker.tasks import task_manager
from .optimize import TaskHandle

router = APIRouter(prefix="/api", tags=["loop"])


@router.post("/loop/iterate", response_model=TaskHandle)
def iterate_loop(payload: LoopRequest) -> TaskHandle:
    from ..domain.schemas import Requirement

    req = Requirement(**payload.model_dump(exclude={"optimize_iterations", "n_suggest"}))
    task_id = task_manager.submit_loop(
        req, iterations=payload.optimize_iterations, n_suggest=payload.n_suggest
    )
    return TaskHandle(task_id=task_id, poll_url=f"/api/tasks/{task_id}")
