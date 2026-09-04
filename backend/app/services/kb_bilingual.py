"""双语检索统一入口(D2/D3, 2026-09-04)。

所有第一级 KB 检索调用点(kg/retrieval.py、api/chat.py、api/kb.py)经此
路由, 替代直调 ``kb_index.search_chunks``:

- 双语关 / 无法判定语言 → 现状全库单模型(零行为变化);
- 英文问 → 英文子库(MiniLM, lang='en', 乱码/未标 chunk 不参与);
- 中文问 → 中文子库(bge + 查询指令);
  - 含英文术语(如 "salt spray 的耐盐雾等级")→ 同 query 双库同查;
  - 纯中文且 ``kb_query_translate`` 开 → LLM 译英后英文子库二次检索,
    结果并入(去重, 中文优先)——跨语通道;
  - 翻译失败/超时 → 自动降级为仅中文子库(不劣于双语前行为)。

依赖: lang_router(检测/选库)、query_translate(中→英)、kb_index
(分组打分)。chunks 的 lang 由一次性回填脚本标注; embedding_model
列区分 bge/MiniLM 向量(comparable_embedding 防混算)。
"""
from __future__ import annotations

from typing import Any

from ..domain.schemas import Evidence


def search(
    question: str,
    k: int = 6,
    *,
    project_id: str | None = None,
    settings: Any | None = None,
) -> list[Evidence]:
    """双语路由检索, 返回合并去重后的 top-k Evidence。

    任何异常/降级都不抛: 双语相关失败只影响"增益部分", 基础检索保持。
    """
    from ..config import get_settings
    from . import kb_index
    from .lang_router import target_langs

    settings = settings or get_settings()
    try:
        if not settings.kb_bilingual:
            return kb_index.search_chunks(question, k=k, project_id=project_id)
        langs = target_langs(question, bilingual=True)
    except Exception:
        return kb_index.search_chunks(question, k=k, project_id=project_id)

    try:
        if langs is None:
            return kb_index.search_chunks(question, k=k, project_id=project_id)
        if langs == ["zh"] and settings.kb_query_translate:
            return _zh_with_translation(question, k=k, project_id=project_id, settings=settings)
        return kb_index.search_chunks(question, k=k, project_id=project_id, langs=langs)
    except Exception:
        # 双语路径异常 → 降级全查(保行为)
        return kb_index.search_chunks(question, k=k, project_id=project_id)


def _zh_with_translation(
    question: str, *, k: int, project_id: str | None, settings: Any,
) -> list[Evidence]:
    from . import kb_index
    from .query_translate import translate_query_zh_to_en

    hits = kb_index.search_chunks(question, k=k, project_id=project_id, langs=["zh"])
    try:
        tr = translate_query_zh_to_en(question)
    except Exception:
        tr = None
    if not tr:
        return hits  # 翻译失败 → 仅中文子库(严格降级)
    hits_en = kb_index.search_chunks(tr, k=k, project_id=project_id, langs=["en"])
    seen = {h.identifier for h in hits}
    hits = hits + [h for h in hits_en if h.identifier not in seen]
    return hits[:k]
