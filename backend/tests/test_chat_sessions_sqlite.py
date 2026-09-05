"""Chat sessions now live in the project SQLite DB (2026-09-05 方案 v2).

Pinned semantics:
- save_chat_session persists session + full messages into chat_sessions/chat_messages
  (SQLite authority), carrying project_id + title;
- load reads SQLite (survives Redis flush — no Redis dependency for correctness);
- list_active_sessions filters by project_id;
- delete removes session + messages;
- session save mirrors the project's chat_history (project_store.get/update
  rebuild the mirror from chat_messages — payload overwrites cannot lose chats).
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.db.database import make_engine, make_session_factory
from app.db.models import Base
from app.db.project_store import ProjectStore
from app.services.session.memory_service import SessionMemoryService

P1 = "1d10717c-80d5-47a5-9ce4-7209081d607c"
P2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest_asyncio.fixture
async def svc(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'sess.db').as_posix()}")
    factory = make_session_factory(engine)
    service = SessionMemoryService.__new__(SessionMemoryService)
    service._session_factory = factory
    service._initialized = True
    service._redis = None
    yield service
    engine.dispose()


HISTORY = [
    {"role": "user", "content": "镁合金钝化耐盐雾 720h?"},
    {"role": "assistant", "content": "植酸/硅烷复合转化膜方案…", "citations": ["src-1"]},
]


@pytest.mark.asyncio
async def test_save_load_roundtrip_with_project_and_title(svc):
    ok = await svc.save_chat_session(
        "chat-1", HISTORY, project_id=P1, title="镁合金钝化耐盐雾"
    )
    assert ok is True
    data = await svc.load_chat_session("chat-1")
    assert data is not None
    assert data["project_id"] == P1
    assert data["title"] == "镁合金钝化耐盐雾"
    assert len(data["history"]) == 2
    # citations(meta) 随消息落库保留
    assert data["history"][1].get("citations") == ["src-1"]
    assert await svc.get_session_history_count("chat-1") == 2


@pytest.mark.asyncio
async def test_messages_survive_restart(svc):
    await svc.save_chat_session("chat-persist", HISTORY, project_id=P1, title="重启存活")
    # 模拟服务重启: 全新实例, 同一 DB
    data = await svc.load_chat_session("chat-persist")
    assert data is not None and len(data["history"]) == 2


@pytest.mark.asyncio
async def test_list_filters_by_project(svc):
    await svc.save_chat_session("chat-a", HISTORY, project_id=P1, title="A")
    await svc.save_chat_session("chat-b", [{"role": "user", "content": "x"}], project_id=P2, title="B")
    await svc.save_chat_session("chat-g", [{"role": "user", "content": "y"}], title="全局(无项目)")
    p1 = await svc.list_active_sessions(project_id=P1)
    assert "chat-a" in p1 and "chat-b" not in p1
    p2 = await svc.list_active_sessions(project_id=P2)
    assert p2 == ["chat-b"]
    # 项目过滤不含无归属会话(全局会话仅在全量列出时可见)
    assert "chat-g" not in p1


@pytest.mark.asyncio
async def test_delete_removes_session_and_messages(svc):
    await svc.save_chat_session("chat-del", HISTORY, project_id=P1)
    assert await svc.delete_chat_session("chat-del") is True
    assert await svc.load_chat_session("chat-del") is None
    assert await svc.list_active_sessions(project_id=P1) == []
    # 二次删除 = False
    assert await svc.delete_chat_session("chat-del") is False


@pytest.mark.asyncio
async def test_update_rewrites_messages(svc):
    await svc.save_chat_session("chat-up", HISTORY, project_id=P1)
    await svc.save_chat_session(
        "chat-up",
        [{"role": "user", "content": "新问题"}],
        project_id=P1,
        title="更新后",
    )
    data = await svc.load_chat_session("chat-up")
    assert len(data["history"]) == 1
    assert data["history"][0]["content"] == "新问题"
    assert data["title"] == "更新后"


@pytest.mark.asyncio
async def test_project_store_chat_mirror_from_messages(tmp_path):
    """payload.chat_history 镜像 = chat_messages 重建: 空 payload 覆盖不清对话。"""
    engine = make_engine(f"sqlite:///{(tmp_path / 'mirror.db').as_posix()}")
    factory = make_session_factory(engine)
    store = ProjectStore(factory)
    detail = store.create(title="镜像项目")
    pid = detail.id
    # 会话经 session service 写入消息表(独立于 payload)
    service = SessionMemoryService.__new__(SessionMemoryService)
    service._session_factory = factory
    service._initialized = True
    service._redis = None
    await service.save_chat_session("chat-m", HISTORY, project_id=pid, title="T")
    # 前端空 payload 覆盖(PUT 空 workspace) — 合并语义保留其余, 镜像仍来自消息表
    updated = store.update(pid, {"sources": [], "chat_history": []}, cause="empty-overwrite-test")
    assert updated is not None
    mirror = updated.workspace.chat_history
    assert len(mirror) == 2, "空覆盖后 chat_history 镜像仍应从 chat_messages 重建"
    assert mirror[0]["content"] == "镁合金钝化耐盐雾 720h?"
    # get() 也实时重建
    got = store.get(pid)
    assert got is not None and len(got.workspace.chat_history) == 2
    engine.dispose()


def test_update_rejects_sources_nonempty_to_empty(tmp_path):
    """sources 非空→空覆盖被拒绝(保留现值)。"""
    engine = make_engine(f"sqlite:///{(tmp_path / 'src.db').as_posix()}")
    factory = make_session_factory(engine)
    store = ProjectStore(factory)
    detail = store.create(title="源保护")
    pid = detail.id
    store.update(
        pid,
        {
            "sources": [
                {
                    "identifier": "s1",
                    "title": "文献1",
                    "source": "patents",
                    "snippet": "…",
                    "relevance": 0.9,
                    "url": "",
                }
            ]
        },
    )
    updated = store.update(pid, {"sources": [], "chat_history": []})
    assert updated is not None
    assert len(updated.workspace.sources) == 1, "空 sources 覆盖应被拒绝"
    engine.dispose()


def test_payload_history_snapshot_and_rollback(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'hist.db').as_posix()}")
    factory = make_session_factory(engine)
    store = ProjectStore(factory)
    detail = store.create(title="历史项目")
    pid = detail.id
    store.update(pid, {"search_query": "v1"})
    store.update(pid, {"search_query": "v2"})
    versions = store.list_payload_history(pid)
    assert len(versions) >= 2
    # 回滚到 v1 快照(创建后首次 update 前)
    rolled = store.rollback_payload(pid, 1)
    assert rolled is not None
    assert rolled.workspace.search_query != "v2"
    # 回滚本身也产生新快照(可再回滚)
    assert any(v["cause"].startswith("rollback") for v in store.list_payload_history(pid))
    engine.dispose()
