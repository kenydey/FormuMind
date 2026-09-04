"""双语路由(2026-09-04): 查询语言检测 + 子库选择。

纯函数, 零依赖。语言检测按 CJK 字符占比(阈值 0.15 低置信 → None,
由调用方决定降级双查)。查询翻译(query_translate)独立模块, 只在本模块
之上做"中文问 → 英译 → 英文子库"的二次检索, 不在本模块内。

设计要点:
- ``detect_lang`` 只判语言, 不判意图;
- ``target_langs`` 决定检索哪些子库(lang 列过滤), None = 全库(现状);
- bge 查询指令前缀只在 bge 模型上使用(rag.embed_model_name 按 lang 返回)。
"""
from __future__ import annotations

import re

_CJK = re.compile(r"[\u4e00-\u9fff]")
# 中英混合术语问("salt spray 的盐雾等级")按中文处理并追加英文子库。
_TERM_EN_HINT = re.compile(r"[a-zA-Z]{3,}", re.I)


def detect_lang(query: str | None, *, threshold: float = 0.15) -> str | None:
    """→ "zh" | "en" | None(置信不足/空)。

    判定: CJK 字符占(去空白后)比例 ≥ threshold → zh; 否则有足够 ASCII
    字母 → en; 无法判定(纯符号/极短) → None。
    """
    q = (query or "").strip()
    if not q:
        return None
    body = re.sub(r"\s+", "", q)
    if not body:
        return None
    cjk = len(_CJK.findall(body))
    ratio = cjk / len(body)
    if ratio >= threshold:
        return "zh"
    letters = len(re.findall(r"[a-zA-Z]", body))
    if letters >= 3:
        return "en"
    return None


def target_langs(query: str | None, *, bilingual: bool = True) -> list[str] | None:
    """双语路由: 返回本次检索应覆盖的子库语言列表。

    - 双语关 → None(全库, 现状行为);
    - 中文问(含中英术语)→ ["zh", "en"]——D3 翻译路径二次检索英文子库时
      上层传 ["en"], 本函数用于首轮; 
    - 英文问 → ["en"];
    - 无法判定 → None(双库都查, 保召回)。
    """
    if not bilingual:
        return None
    lang = detect_lang(query)
    if lang is None:
        return None
    if lang == "zh":
        # 含英文术语的中文问("salt spray 耐盐雾等级")英文子库同查;
        # 纯中文由上层翻译后二次查 ["en"]。此处保守给 ["zh"] + 术语命中时
        # 追加 "en", 避免纯中文问在 D3 之前漏掉英文库——见 query_translate。
        if _TERM_EN_HINT.search(query or ""):
            return ["zh", "en"]
        return ["zh"]
    return ["en"]


def model_for_lang(lang: str | None) -> str:
    """子库对应嵌入模型(与 rag.embed_model_name 的 lang 分支一致)。

    中文子库 → bge-small-zh-v1.5; 英文/未知 → all-MiniLM-L6-v2。
    """
    if lang == "zh":
        return "BAAI/bge-small-zh-v1.5"
    return "sentence-transformers/all-MiniLM-L6-v2"
