"""LangChain 查询扩展引擎 — 将自然语言主题扩展为中英文关键词与 IPC/CPC 建议。"""
from __future__ import annotations

import json
import logging
import re

from ...config import Settings, get_settings
from .models import ExpandedQuery

logger = logging.getLogger(__name__)

# 中英文分词：英文按词，中文按单字
_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]")

# 涂料 / 聚氨酯领域默认 IPC 建议（离线回退）
_DEFAULT_IPC = ["C09D", "C09D175/04", "C08G18/00", "C23F"]

_EXPAND_PROMPT = """你是一位涂料与多相聚合物研发领域的专利与文献检索专家。
根据用户的自然语言输入，推断检索意图，并扩展为结构化检索词。

用户输入：{user_query}

请严格输出以下 JSON（不要 markdown 代码块，不要额外说明）：
{{
  "intent": "一句话描述用户检索意图（中文）",
  "chinese_keywords": ["中文关键词1", "中文关键词2", ...],
  "english_synonyms": ["english term 1", "academic synonym 2", ...],
  "ipc_cpc_suggestions": ["C09D", "C08G18/00", ...]
}}

要求：
1. chinese_keywords 提取 3-8 个核心中文检索词；
2. english_synonyms 给出 3-8 个英文学术同义词或 IUPAC/行业术语；
3. ipc_cpc_suggestions 给出 2-5 个与主题相关的 IPC/CPC 分类号；
4. 若输入为英文，chinese_keywords 可给出对应中文译名。"""


class QueryExpander:
    """使用 LangChain LLM 将用户自然语言扩展为结构化检索查询。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def expand(self, user_query: str) -> ExpandedQuery:
        """主入口：LLM 扩展 → JSON 解析 → Pydantic 校验；失败时离线回退。"""
        query = (user_query or "").strip()
        if not query:
            return ExpandedQuery(
                intent="空查询",
                chinese_keywords=[],
                english_synonyms=[],
                ipc_cpc_suggestions=_DEFAULT_IPC[:2],
            )

        expanded = self._expand_with_langchain(query)
        if expanded is not None:
            return expanded

        return self._offline_expand(query)

    def _expand_with_langchain(self, user_query: str) -> ExpandedQuery | None:
        """尝试通过 LangChain ChatModel 生成结构化扩展。"""
        api_key = self._settings.get_active_api_key()
        if not api_key:
            return None

        try:
            from langchain_core.prompts import ChatPromptTemplate  # type: ignore
        except ImportError:
            logger.debug("langchain-core not installed; using offline query expansion")
            return None

        llm = self._build_chat_model(api_key)
        if llm is None:
            return None

        try:
            prompt = ChatPromptTemplate.from_template(_EXPAND_PROMPT)
            chain = prompt | llm
            response = chain.invoke({"user_query": user_query})
            raw = response.content if hasattr(response, "content") else str(response)
            return self._parse_expanded(raw, user_query)
        except Exception as exc:
            logger.warning("LangChain query expansion failed: %s", exc)
            return None

    def _build_chat_model(self, api_key: str):
        """根据 llm_provider 构建 LangChain ChatModel。"""
        provider = self._settings.llm_provider
        model = self._settings.llm_model

        try:
            if provider == "anthropic":
                from langchain_anthropic import ChatAnthropic  # type: ignore

                return ChatAnthropic(
                    api_key=api_key,
                    model=model,
                    max_tokens=min(self._settings.llm_max_tokens, 1024),
                    temperature=0.2,
                )

            from langchain_openai import ChatOpenAI  # type: ignore

            kwargs: dict = {
                "api_key": api_key,
                "model": model,
                "max_tokens": min(self._settings.llm_max_tokens, 1024),
                "temperature": 0.2,
            }
            if self._settings.llm_base_url:
                kwargs["base_url"] = self._settings.llm_base_url
            elif provider == "deepseek":
                kwargs["base_url"] = "https://api.deepseek.com"
            elif provider == "groq":
                kwargs["base_url"] = "https://api.groq.com/openai/v1"
            elif provider == "qwen":
                kwargs["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            elif provider == "moonshot":
                kwargs["base_url"] = "https://api.moonshot.cn/v1"
            elif provider == "xai":
                kwargs["base_url"] = "https://api.x.ai/v1"

            return ChatOpenAI(**kwargs)
        except ImportError:
            logger.debug("langchain-openai/anthropic not installed")
            return None
        except Exception as exc:
            logger.warning("Failed to build LangChain chat model: %s", exc)
            return None

    def _parse_expanded(self, raw: str, fallback_query: str) -> ExpandedQuery | None:
        """解析 LLM 返回的 JSON 文本。"""
        text = raw.strip()
        if "```" in text:
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.startswith("json"):
                text = text[4:].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        try:
            return ExpandedQuery(
                intent=str(data.get("intent") or fallback_query),
                chinese_keywords=[str(k) for k in (data.get("chinese_keywords") or []) if k],
                english_synonyms=[str(k) for k in (data.get("english_synonyms") or []) if k],
                ipc_cpc_suggestions=[
                    str(k) for k in (data.get("ipc_cpc_suggestions") or []) if k
                ],
            )
        except Exception:
            return None

    def _offline_expand(self, user_query: str) -> ExpandedQuery:
        """无 LLM 时的启发式扩展：分词提取 + 默认 IPC。"""
        tokens = _TOKEN_RE.findall(user_query.lower())
        chinese = [t for t in tokens if re.search(r"[一-鿿]", t)]
        english = [t for t in tokens if re.match(r"[a-z0-9]+", t)]

        # 去重保序
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
