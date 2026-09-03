"""Task 2.7: Golden eval dataset — curated question/answer pairs for RAG quality regression testing.

Pure data file — easy to extend by adding entries to ``golden_questions``.
Each entry has a question, expected keyword hits, and a category label.
"""

from __future__ import annotations

# ── Golden questions ──────────────────────────────────────────────────────────
# Format:
#   question          — Chinese search query (simulates a real R&D question)
#   expected_keywords — at least 1 of these must appear in top-3 retrieval results
#   min_relevance_category — human-readable category label (for reporting)
#
# To extend: append a new dict with the same keys.

golden_questions: list[dict] = [
    {
        "question": "环氧树脂防腐机理是什么",
        "expected_keywords": ["环氧", "防腐", "固化", "成膜"],
        "min_relevance_category": "epoxy",
    },
    {
        "question": "磷酸锌在防腐蚀涂料中的作用",
        "expected_keywords": ["磷酸锌", "防锈", "颜料", "盐雾"],
        "min_relevance_category": "anticorrosion",
    },
    {
        "question": "防腐蚀涂料固化温度范围",
        "expected_keywords": ["固化", "温度", "°C", "80"],
        "min_relevance_category": "coating_process",
    },
    {
        "question": "盐雾试验标准及评价方法",
        "expected_keywords": ["盐雾", "试验", "小时", "中性"],
        "min_relevance_category": "testing",
    },
    {
        "question": "表面处理前除油脱脂工艺",
        "expected_keywords": ["除油", "脱脂", "表面", "清洁"],
        "min_relevance_category": "pretreatment",
    },
    {
        "question": "聚氨酯涂料与环氧涂料性能对比",
        "expected_keywords": ["聚氨酯", "环氧", "涂料", "耐候"],
        "min_relevance_category": "coating_comparison",
    },
    {
        "question": "涂膜厚度对防腐性能的影响",
        "expected_keywords": ["涂膜", "厚度", "防腐", "微米"],
        "min_relevance_category": "film_properties",
    },
]

# ── Sample documents for CI ingest ────────────────────────────────────────────
# These are injected into the KB before golden-eval tests so that
# hybrid_search has relevant content to retrieve.  Each entry is a plain-text
# snippet that covers one or more golden questions.

sample_documents: list[dict] = [
    {
        "title": "环氧树脂防腐机理研究",
        "text": (
            "环氧树脂作为主要成膜物质，具有优异的附着力和耐化学性。"
            "其防腐机理主要依靠固化后形成致密的交联网络结构，"
            "有效阻隔水、氧和腐蚀性离子的渗透。"
            "通过添加胺类固化剂，环氧树脂在室温下即可固化，"
            "形成高交联密度的防腐涂层。"
        ),
    },
    {
        "title": "磷酸锌防锈颜料应用",
        "text": (
            "磷酸锌是一种重要的无毒防锈颜料，广泛应用于防腐蚀涂料中。"
            "其在涂层中缓慢水解生成磷酸根离子和锌离子，"
            "与金属基材反应形成钝化保护膜，显著提升盐雾耐受时间。"
            "配合环氧树脂使用可达到500小时以上的中性盐雾试验要求。"
        ),
    },
    {
        "title": "防腐蚀涂料工艺参数指南",
        "text": (
            "固化温度控制在80-120°C范围内可获得最佳防腐性能。"
            "涂膜厚度建议控制在25-35微米之间。"
            "表面处理前需进行除油脱脂工艺，确保基材清洁度达到Sa2.5级。"
            "盐雾试验按GB/T 1771标准执行，中性盐雾试验(NSS)是常用评价方法。"
        ),
    },
    {
        "title": "聚氨酯与环氧涂料性能对比",
        "text": (
            "聚氨酯涂料具有优异的耐候性和柔韧性，适用于户外暴露环境。"
            "环氧涂料附着力强、耐化学性好，但在紫外线照射下易粉化。"
            "二者常配合使用：环氧底漆提供防腐和附着力，聚氨酯面漆提供耐候保护。"
            "这种复合涂层体系广泛应用于钢结构重防腐领域。"
        ),
    },
]
