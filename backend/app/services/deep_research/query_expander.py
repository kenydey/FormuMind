"""查询扩展引擎 — 将自然语言主题扩展为中英文关键词与 IPC/CPC 建议。"""
from __future__ import annotations

import logging
import re

from ...config import Settings, get_settings
from .. import llm
from .models import ExpandedQuery

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]")
_DEFAULT_IPC = ["C09D", "C09D175/04", "C08G18/00", "C23F"]

_EXPAND_PROMPT = """你是一位涂料与多相聚合物研发领域的专利与文献检索专家。
根据用户的自然语言输入，推断检索意图，并扩展为结构化检索词。

用户输入：{user_query}

请严格输出以下 JSON（不要 markdown 代码块，不要额外说明）：
{{
  "intent": "一句话描述用户检索意图（中文）",
  "chinese_keywords": ["中文关键词1", "中文关键词2"],
  "english_synonyms": ["english term 1", "academic synonym 2"],
  "ipc_cpc_suggestions": ["C09D", "C08G18/00"]
}}

要求：
1. chinese_keywords 提取 3-8 个核心中文检索词；
2. english_synonyms 给出 3-8 个英文学术同义词或 IUPAC/行业术语；
3. ipc_cpc_suggestions 给出 2-5 个与主题相关的 IPC/CPC 分类号；
4. 若输入为英文，chinese_keywords 可给出对应中文译名。"""


class QueryExpander:
    """通过统一 llm 服务将用户自然语言扩展为结构化检索查询。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def expand(self, user_query: str) -> ExpandedQuery:
        query = (user_query or "").strip()
        if not query:
            return ExpandedQuery(
                intent="空查询",
                chinese_keywords=[],
                english_synonyms=[],
                ipc_cpc_suggestions=_DEFAULT_IPC[:2],
            )

        expanded = self._expand_with_llm(query)
        if expanded is not None:
            return expanded

        return self._offline_expand(query)

    def _expand_with_llm(self, user_query: str) -> ExpandedQuery | None:
        if not self._settings.get_active_api_key():
            return None

        prompt = _EXPAND_PROMPT.format(user_query=user_query)
        try:
            data = llm.complete_json(prompt)
        except Exception as exc:
            logger.warning("Query expansion LLM call failed: %s", exc)
            return None

        if not isinstance(data, dict):
            return None

        try:
            return ExpandedQuery(
                intent=str(data.get("intent") or user_query),
                chinese_keywords=[str(k) for k in (data.get("chinese_keywords") or []) if k],
                english_synonyms=[str(k) for k in (data.get("english_synonyms") or []) if k],
                ipc_cpc_suggestions=[
                    str(k) for k in (data.get("ipc_cpc_suggestions") or []) if k
                ],
            )
        except Exception:
            return None

    def _offline_expand(self, user_query: str) -> ExpandedQuery:
        tokens = _TOKEN_RE.findall(user_query.lower())
        chinese = [t for t in tokens if re.search(r"[一-鿿]", t)]
        english = [t for t in tokens if re.match(r"[a-z0-9]+", t)]

        def _uniq(items: list[str]) -> list[str]:
            return list(dict.fromkeys(items))

        chinese_kw = _uniq(chinese) or [user_query[:20]]
        english_kw = _uniq(english)

        return ExpandedQuery(
            intent=f"检索与「{user_query[:40]}」相关的专利与文献",
            chinese_keywords=chinese_kw[:8],
            english_synonyms=english_kw[:8],
            ipc_cpc_suggestions=_DEFAULT_IPC[:3],
        )
