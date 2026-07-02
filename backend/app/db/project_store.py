"""SQLite-backed project workspace store."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from ..domain.project_workspace import (
    ProjectDetail,
    ProjectSummary,
    ProjectWorkspace,
    default_requirement,
    derive_headline,
    derive_title,
    summary_stats,
    workspace_from_legacy,
)
from ..domain.project_workspace import LegacySnapshotPayload
from .models import ProjectRow
from .session_utils import commit_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_summaries(self) -> list[ProjectSummary]:
        with self._session_factory() as session:
            rows = session.query(ProjectRow).filter(ProjectRow.is_archived.is_(False)).order_by(
                ProjectRow.updated_at.desc()
            ).all()
            out: list[ProjectSummary] = []
            for row in rows:
                ws = ProjectWorkspace.model_validate(row.payload or {})
                stats = summary_stats(ws)
                out.append(
                    ProjectSummary(
                        id=row.id,
                        title=row.title,
                        headline=row.headline,
                        domain=row.domain,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                        **stats,
                    )
                )
            return out

    def get(self, project_id: str) -> ProjectDetail | None:
        with self._session_factory() as session:
            row = session.get(ProjectRow, project_id)
            if row is None or row.is_archived:
                return None
            ws = ProjectWorkspace.model_validate(row.payload or {})
            return ProjectDetail(
                id=row.id,
                title=row.title,
                headline=row.headline,
                domain=row.domain,
                created_at=row.created_at,
                updated_at=row.updated_at,
                workspace=ws,
            )

    def create(
        self,
        *,
        title: str = "",
        requirement=None,
    ) -> ProjectDetail:
        req = requirement or default_requirement()
        ws = ProjectWorkspace(requirement=req)
        if title:
            ws.search_query = title
        return self._insert(ws, title=title or derive_title(ws))

    def _insert(self, workspace: ProjectWorkspace, *, title: str) -> ProjectDetail:
        pid = str(uuid.uuid4())
        now = _utcnow()
        domain = workspace.requirement.domain.value if workspace.requirement else ""
        row = ProjectRow(
            id=pid,
            title=title or derive_title(workspace),
            headline=derive_headline(workspace),
            domain=domain,
            payload=workspace.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
        with commit_session(self._session_factory) as session:
            session.add(row)
            session.flush()
            session.refresh(row)
        return ProjectDetail(
            id=row.id,
            title=row.title,
            headline=row.headline,
            domain=row.domain,
            created_at=row.created_at,
            updated_at=row.updated_at,
            workspace=workspace,
        )

    def update(
        self,
        project_id: str,
        workspace: ProjectWorkspace,
        *,
        title: str | None = None,
    ) -> ProjectDetail | None:
        with commit_session(self._session_factory) as session:
            row = session.get(ProjectRow, project_id)
            if row is None or row.is_archived:
                return None
            row.payload = workspace.model_dump(mode="json")
            row.title = title or derive_title(workspace)
            row.headline = derive_headline(workspace)
            if workspace.requirement:
                row.domain = workspace.requirement.domain.value
            row.updated_at = _utcnow()
            session.flush()
            session.refresh(row)
            ws = ProjectWorkspace.model_validate(row.payload or {})
            return ProjectDetail(
                id=row.id,
                title=row.title,
                headline=row.headline,
                domain=row.domain,
                created_at=row.created_at,
                updated_at=row.updated_at,
                workspace=ws,
            )

    def delete(self, project_id: str) -> bool:
        with commit_session(self._session_factory) as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                return False
            row.is_archived = True
            row.updated_at = _utcnow()
            return True

    def migrate_legacy(self, snapshots: list[LegacySnapshotPayload]) -> list[ProjectSummary]:
        created: list[ProjectSummary] = []
        for snap in snapshots:
            ws = workspace_from_legacy(snap)
            detail = self._insert(ws, title=snap.headline or derive_title(ws))
            created.append(
                ProjectSummary(
                    id=detail.id,
                    title=detail.title,
                    headline=detail.headline,
                    domain=detail.domain,
                    created_at=detail.created_at,
                    updated_at=detail.updated_at,
                    **summary_stats(detail.workspace),
                )
            )
        return created


_store: ProjectStore | None = None


def get_project_store() -> ProjectStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = ProjectStore(default_session_factory())
    return _store
