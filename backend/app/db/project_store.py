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

    def db_stats(self, project_id: str) -> dict:
        """Real database counts for a project (knowledge docs / campaigns / experiments)."""
        from sqlalchemy import func

        from .models import Campaign, ExperimentRow, SourceDocument

        with self._session_factory() as session:
            doc_count = int(
                session.query(func.count(SourceDocument.id))
                .filter(SourceDocument.project_id == project_id)
                .scalar() or 0
            )
            campaign_count = int(
                session.query(func.count(Campaign.id))
                .filter(Campaign.project_id == project_id)
                .scalar() or 0
            )
            experiment_count = int(
                session.query(func.count(ExperimentRow.id))
                .filter(ExperimentRow.project_id == project_id)
                .scalar() or 0
            )
            return {
                "document_count": doc_count,
                "campaign_count": campaign_count,
                "experiment_count": experiment_count,
            }

    def delete(self, project_id: str, *, knowledge: str = "delete") -> dict | None:
        """Delete a project; ``knowledge`` controls its knowledge base.

        ``knowledge="global"`` keeps the project's source_documents by clearing
        their project_id (they become global KB, visible to all projects);
        ``knowledge="delete"`` removes them (plus chunks/mentions). Business
        data (campaigns / experiments / measurements / attachments) is always
        removed. Returns counts, or None if the project is missing.
        """
        from .models import (
            Campaign,
            DocumentChunk,
            ExperimentAttachment,
            ExperimentRow,
            KGMention,
            MeasurementRow,
            SourceDocument,
        )

        with commit_session(self._session_factory) as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                return None

            # 1. knowledge base
            doc_ids = [
                r[0]
                for r in session.query(SourceDocument.id)
                .filter(SourceDocument.project_id == project_id)
                .all()
            ]
            if knowledge == "global":
                session.query(SourceDocument).filter(
                    SourceDocument.project_id == project_id
                ).update({"project_id": None}, synchronize_session=False)
            else:
                if doc_ids:
                    session.query(KGMention).filter(
                        KGMention.source_id.in_(doc_ids)
                    ).delete(synchronize_session=False)
                    session.query(DocumentChunk).filter(
                        DocumentChunk.source_id.in_(doc_ids)
                    ).delete(synchronize_session=False)
                    session.query(SourceDocument).filter(
                        SourceDocument.id.in_(doc_ids)
                    ).delete(synchronize_session=False)

            # 2. business data
            campaign_ids = [
                r[0]
                for r in session.query(Campaign.id)
                .filter(Campaign.project_id == project_id)
                .all()
            ]
            exp_ids = [
                r[0]
                for r in session.query(ExperimentRow.id)
                .filter(ExperimentRow.project_id == project_id)
                .all()
            ]
            if exp_ids:
                session.query(MeasurementRow).filter(
                    MeasurementRow.experiment_id.in_(exp_ids)
                ).delete(synchronize_session=False)
                session.query(ExperimentAttachment).filter(
                    ExperimentAttachment.experiment_id.in_(exp_ids)
                ).delete(synchronize_session=False)
                session.query(ExperimentRow).filter(
                    ExperimentRow.id.in_(exp_ids)
                ).delete(synchronize_session=False)
            if campaign_ids:
                session.query(Campaign).filter(
                    Campaign.id.in_(campaign_ids)
                ).delete(synchronize_session=False)

            # 3. soft-delete project
            row.is_archived = True
            row.updated_at = _utcnow()

            return {
                "document_count": len(doc_ids),
                "campaign_count": len(campaign_ids),
                "experiment_count": len(exp_ids),
            }

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
