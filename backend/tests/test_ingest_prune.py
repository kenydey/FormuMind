"""入库收尾自动清理（prune）测试：full_text 清空（保留行）+ MinerU 缓存跳过。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.models import Base, SourceDocument
from app.db.source_store import SourceStore


def test_prune_config_defaults() -> None:
    """两个清理开关默认开启（生产省空间）。"""
    s = Settings()
    assert s.prune_source_fulltext is True
    assert s.prune_mineru_cache is True


def test_clear_full_text_keeps_row_metadata(tmp_path) -> None:
    """清空 full_text 只清字段——行（content_hash 去重 / 列表 / source_guide）保留。"""
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    store = SourceStore(factory)

    source_id = store.create(
        filename="a.pdf",
        title="A",
        source_kind="local",
        full_text="hello world " * 100,
        content_hash="abc123",
        extraction_status="ok",
    )
    with factory() as s:
        assert s.get(SourceDocument, source_id).full_text is not None

    store.clear_full_text(source_id)

    with factory() as s:
        doc = s.get(SourceDocument, source_id)
        assert doc is not None, "行必须保留——去重/列表/source_guide 都依赖它"
        assert doc.full_text is None, "full_text 已清空"
        assert doc.content_hash == "abc123"


def test_clear_full_text_is_idempotent(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    store = SourceStore(factory)
    source_id = store.create(
        filename="b.pdf", title="B", source_kind="local",
        full_text="x", content_hash="h2",
    )
    store.clear_full_text(source_id)
    store.clear_full_text(source_id)  # 幂等，不抛错
