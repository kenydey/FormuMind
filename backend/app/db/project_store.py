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


# payload 快照保留上限(每项目) —— 防 history 表无限膨胀
MAX_PAYLOAD_VERSIONS = 50
# chat_history 镜像长度(权威在 chat_messages 表, payload 只存最近镜像供 UI 展示)
CHAT_MIRROR_LIMIT = 30


def _chat_mirror(session, project_id: str) -> list[dict]:
    """重建 chat_history 镜像: 该项目最新 N 条消息(跨会话, 按时间)。

    权威在 chat_messages —— payload.chat_history 只是派生镜像, 任何
    前端全量覆盖都不会丢对话。
    """
    from .models import ChatMessageRow

    rows = (
        session.query(ChatMessageRow)
        .filter(ChatMessageRow.project_id == project_id)
        .order_by(ChatMessageRow.created_at.desc(), ChatMessageRow.seq.desc())
        .limit(CHAT_MIRROR_LIMIT)
        .all()
    )
    out = []
    for r in reversed(rows):
        msg: dict = {"role": r.role, "content": r.content}
        if r.meta_json:
            msg.update({k: v for k, v in r.meta_json.items() if k not in ("role", "content")})
        out.append(msg)
    return out


def _snapshot_history(session, project_id: str, payload: dict, *, cause: str = "update") -> None:
    """归档当前 payload 到版本历史(update 写新值前调用)。"""
    from .models import ProjectPayloadHistoryRow

    last = (
        session.query(ProjectPayloadHistoryRow)
        .filter(ProjectPayloadHistoryRow.project_id == project_id)
        .order_by(ProjectPayloadHistoryRow.version.desc())
        .first()
    )
    version = (last.version if last else 0) + 1
    session.add(
        ProjectPayloadHistoryRow(
            project_id=project_id, version=version, payload=payload, cause=cause
        )
    )
    # 裁剪超过上限的旧版本(保留最近 MAX_PAYLOAD_VERSIONS)
    over = (
        session.query(ProjectPayloadHistoryRow.id)
        .filter(ProjectPayloadHistoryRow.project_id == project_id)
        .order_by(ProjectPayloadHistoryRow.version.desc())
        .offset(MAX_PAYLOAD_VERSIONS)
        .all()
    )
    if over:
        ids = [r[0] for r in over]
        session.query(ProjectPayloadHistoryRow).filter(
            ProjectPayloadHistoryRow.id.in_(ids)
        ).delete(synchronize_session=False)


class ProjectStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_summaries(self) -> list[ProjectSummary]:
        from sqlalchemy import func

        from .models import ChatMessageRow, SourceDocument

        with self._session_factory() as session:
            rows = session.query(ProjectRow).filter(ProjectRow.is_archived.is_(False)).order_by(
                ProjectRow.updated_at.desc()
            ).all()
            # 全局文档(project_id NULL)对任何项目视图可见 —— 与 kb/sources 语义一致
            global_docs = int(
                session.query(func.count(SourceDocument.id))
                .filter(SourceDocument.project_id.is_(None))
                .scalar()
                or 0
            )
            out: list[ProjectSummary] = []
            for row in rows:
                ws = ProjectWorkspace.model_validate(row.payload or {})
                stats = summary_stats(ws)
                chat_count = int(
                    session.query(func.count(ChatMessageRow.id))
                    .filter(ChatMessageRow.project_id == row.id)
                    .scalar()
                    or 0
                )
                # source_count(2026-09-05): 语义 = 本项目知识库文档数
                # (知识库已归属项目; payload.sources 仅为检索证据, 不再是「资料」计数)
                project_docs = int(
                    session.query(func.count(SourceDocument.id))
                    .filter(SourceDocument.project_id == row.id)
                    .scalar()
                    or 0
                )
                stats["source_count"] = project_docs + global_docs
                out.append(
                    ProjectSummary(
                        id=row.id,
                        title=row.title,
                        headline=row.headline,
                        domain=row.domain,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                        chat_count=chat_count,
                        **{k: v for k, v in stats.items() if k != "chat_count"},
                    )
                )
            return out

    def get(self, project_id: str) -> ProjectDetail | None:
        with self._session_factory() as session:
            row = session.get(ProjectRow, project_id)
            if row is None or row.is_archived:
                return None
            payload = dict(row.payload or {})
            # chat_history 镜像实时重建(权威在 chat_messages)
            payload["chat_history"] = _chat_mirror(session, project_id)
            ws = ProjectWorkspace.model_validate(payload)
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
        workspace: dict,
        *,
        title: str | None = None,
        cause: str = "update",
    ) -> ProjectDetail | None:
        """Presence-based merge update (2026-09-05): 只写请求出现的键。

        防空覆盖保护:
        - ``chat_history`` 请求值忽略 —— 由 chat_messages 表重建镜像(权威在表);
        - ``sources`` DB 有值而请求为空 → 拒绝覆盖, 记 warning 保留现值。
        写前把旧 payload 快照到 project_payload_history(可回滚)。
        """
        import logging

        log = logging.getLogger(__name__)
        with commit_session(self._session_factory) as session:
            row = session.get(ProjectRow, project_id)
            if row is None or row.is_archived:
                return None
            cur = dict(row.payload or {})
            # 1) 快照旧 payload(可回滚)
            _snapshot_history(session, project_id, cur, cause=cause)
            # 2) presence-based 合并(请求出现的键才写)
            for key, value in (workspace or {}).items():
                if key == "chat_history":
                    # 镜像由消息表权威重建 —— 不接受前端覆盖
                    continue
                if key == "sources" and not value and cur.get("sources"):
                    log.warning(
                        "project %s: 拒绝 sources 非空→空覆盖(保留 %d 条)",
                        project_id,
                        len(cur["sources"]),
                    )
                    continue
                cur[key] = value
            # 3) chat_history 镜像 = chat_messages 重建(该项目最新 N 条)
            cur["chat_history"] = _chat_mirror(session, project_id)
            # 4) 校验 + 派生列
            ws = ProjectWorkspace.model_validate(cur)
            merged = ws.model_dump(mode="json")
            row.payload = merged
            row.title = title or derive_title(ws)
            row.headline = derive_headline(ws)
            if ws.requirement:
                row.domain = ws.requirement.domain.value
            row.updated_at = _utcnow()
            session.flush()
            session.refresh(row)
            return ProjectDetail(
                id=row.id,
                title=row.title,
                headline=row.headline,
                domain=row.domain,
                created_at=row.created_at,
                updated_at=row.updated_at,
                workspace=ws,
            )

    def list_payload_history(self, project_id: str, limit: int = 20) -> list[dict]:
        """审计: 该项目 payload 版本历史(不含完整 payload, 只元数据+尺寸)。"""
        from .models import ProjectPayloadHistoryRow

        with self._session_factory() as session:
            rows = (
                session.query(ProjectPayloadHistoryRow)
                .filter(ProjectPayloadHistoryRow.project_id == project_id)
                .order_by(ProjectPayloadHistoryRow.version.desc())
                .limit(limit)
                .all()
            )
            out = []
            for r in rows:
                try:
                    size = len(r.payload or {})
                except Exception:
                    size = 0
                out.append(
                    {
                        "version": r.version,
                        "cause": r.cause,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "fields": sorted((r.payload or {}).keys()),
                        "chat_count": len((r.payload or {}).get("chat_history") or []),
                        "source_count": len((r.payload or {}).get("sources") or []),
                    }
                )
            return out

    def rollback_payload(self, project_id: str, version: int) -> ProjectDetail | None:
        """回滚 payload 到指定版本(先快照当前值, 再恢复目标版本)。"""
        from .models import ProjectPayloadHistoryRow

        with commit_session(self._session_factory) as session:
            row = session.get(ProjectRow, project_id)
            if row is None or row.is_archived:
                return None
            target = (
                session.query(ProjectPayloadHistoryRow)
                .filter(
                    ProjectPayloadHistoryRow.project_id == project_id,
                    ProjectPayloadHistoryRow.version == version,
                )
                .first()
            )
            if target is None:
                raise ValueError(f"version {version} not found")
            cur = dict(row.payload or {})
            _snapshot_history(session, project_id, cur, cause=f"rollback-to-{version}")
            restored = dict(target.payload or {})
            restored["chat_history"] = _chat_mirror(session, project_id)
            ws = ProjectWorkspace.model_validate(restored)
            row.payload = ws.model_dump(mode="json")
            row.title = derive_title(ws)
            row.headline = derive_headline(ws)
            if ws.requirement:
                row.domain = ws.requirement.domain.value
            row.updated_at = _utcnow()
            session.flush()
            session.refresh(row)
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
