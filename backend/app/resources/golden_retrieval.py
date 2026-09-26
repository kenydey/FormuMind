"""Golden retrieval dataset — shared by CI golden_eval and KB query-test golden batch.

Pure data file — easy to extend by adding entries to ``golden_questions``.
Each entry has a question, expected keyword hits, and a category label.

P1 #27: expanded to 50+ questions (was 7) covering coating R&D themes.
"""

from __future__ import annotations

# ── Golden questions ──────────────────────────────────────────────────────────
# Format:
#   question          — Chinese search query (simulates a real R&D question)
#   expected_keywords — at least 1 of these must appear in top-3 retrieval results
#   min_relevance_category — human-readable category label (for reporting)

golden_questions: list[dict] = [
    {"question": "环氧树脂防腐机理是什么", "expected_keywords": ["环氧", "防腐", "固化", "成膜"], "min_relevance_category": "epoxy"},
    {"question": "磷酸锌在防腐蚀涂料中的作用", "expected_keywords": ["磷酸锌", "防锈", "颜料", "盐雾"], "min_relevance_category": "anticorrosion"},
    {"question": "防腐蚀涂料固化温度范围", "expected_keywords": ["固化", "温度", "°C", "80"], "min_relevance_category": "coating_process"},
    {"question": "盐雾试验标准及评价方法", "expected_keywords": ["盐雾", "试验", "小时", "中性"], "min_relevance_category": "testing"},
    {"question": "表面处理前除油脱脂工艺", "expected_keywords": ["除油", "脱脂", "表面", "清洁"], "min_relevance_category": "pretreatment"},
    {"question": "聚氨酯涂料与环氧涂料性能对比", "expected_keywords": ["聚氨酯", "环氧", "涂料", "耐候"], "min_relevance_category": "coating_comparison"},
    {"question": "涂膜厚度对防腐性能的影响", "expected_keywords": ["涂膜", "厚度", "防腐", "微米"], "min_relevance_category": "film_properties"},
    {"question": "环氧底漆附着力如何提高", "expected_keywords": ["环氧", "附着力", "底漆", "基材"], "min_relevance_category": "epoxy"},
    {"question": "胺类固化剂用量对交联密度的影响", "expected_keywords": ["胺", "固化剂", "交联", "环氧"], "min_relevance_category": "epoxy"},
    {"question": "无铬钝化膜的耐蚀机理", "expected_keywords": ["钝化", "无铬", "耐蚀", "膜"], "min_relevance_category": "passivation"},
    {"question": "硅烷偶联剂在前处理中的作用", "expected_keywords": ["硅烷", "偶联", "前处理", "附着力"], "min_relevance_category": "pretreatment"},
    {"question": "锌粉富锌底漆的阴极保护原理", "expected_keywords": ["锌粉", "富锌", "阴极", "底漆"], "min_relevance_category": "anticorrosion"},
    {"question": "中性盐雾NSS与铜加速盐雾CASS区别", "expected_keywords": ["盐雾", "NSS", "CASS", "试验"], "min_relevance_category": "testing"},
    {"question": "GB/T 1771盐雾试验操作要点", "expected_keywords": ["盐雾", "GB/T", "1771", "试验"], "min_relevance_category": "testing"},
    {"question": "涂膜孔隙率如何降低", "expected_keywords": ["孔隙", "涂膜", "致密", "交联"], "min_relevance_category": "film_properties"},
    {"question": "水性环氧涂料VOC控制方法", "expected_keywords": ["水性", "环氧", "VOC", "涂料"], "min_relevance_category": "waterborne"},
    {"question": "丙烯酸面漆耐紫外老化性能", "expected_keywords": ["丙烯酸", "紫外", "老化", "耐候"], "min_relevance_category": "weathering"},
    {"question": "喷砂Sa2.5表面清洁度要求", "expected_keywords": ["喷砂", "Sa2.5", "清洁", "表面"], "min_relevance_category": "pretreatment"},
    {"question": "磷化膜厚度与耐蚀性关系", "expected_keywords": ["磷化", "厚度", "耐蚀", "膜"], "min_relevance_category": "pretreatment"},
    {"question": "钼酸盐缓蚀剂替代铬酸盐可行性", "expected_keywords": ["钼酸", "缓蚀", "铬酸", "替代"], "min_relevance_category": "inhibitors"},
    {"question": "稀土铈盐钝化工艺参数", "expected_keywords": ["铈", "稀土", "钝化", "工艺"], "min_relevance_category": "passivation"},
    {"question": "环氧玻璃鳞片涂料抗渗透性能", "expected_keywords": ["玻璃鳞片", "环氧", "渗透", "涂料"], "min_relevance_category": "epoxy"},
    {"question": "固化时间过短导致涂膜发软的原因", "expected_keywords": ["固化", "时间", "涂膜", "交联"], "min_relevance_category": "coating_process"},
    {"question": "湿度对聚氨酯固化的影响", "expected_keywords": ["湿度", "聚氨酯", "固化", "涂料"], "min_relevance_category": "coating_process"},
    {"question": "附着力划格试验ISO 2409解读", "expected_keywords": ["附着力", "划格", "ISO", "试验"], "min_relevance_category": "testing"},
    {"question": "电化学阻抗谱评价涂层防护", "expected_keywords": ["阻抗", "电化学", "涂层", "防护"], "min_relevance_category": "testing"},
    {"question": "纳米二氧化硅改性环氧耐磨性", "expected_keywords": ["纳米", "二氧化硅", "环氧", "耐磨"], "min_relevance_category": "nanomod"},
    {"question": "铝粉颜料浮型与非浮型差异", "expected_keywords": ["铝粉", "颜料", "浮型", "涂料"], "min_relevance_category": "pigments"},
    {"question": "防锈颜料磷酸铝锌的应用", "expected_keywords": ["磷酸", "防锈", "颜料", "锌"], "min_relevance_category": "anticorrosion"},
    {"question": "重防腐涂层配套体系设计原则", "expected_keywords": ["重防腐", "配套", "底漆", "面漆"], "min_relevance_category": "system_design"},
    {"question": "海洋大气环境下涂层选型", "expected_keywords": ["海洋", "大气", "涂层", "防腐"], "min_relevance_category": "environment"},
    {"question": "工业大气SO2对涂膜老化影响", "expected_keywords": ["SO2", "工业", "老化", "涂膜"], "min_relevance_category": "environment"},
    {"question": "阴极保护与有机涂层联合防护", "expected_keywords": ["阴极保护", "涂层", "联合", "防护"], "min_relevance_category": "system_design"},
    {"question": "环氧云铁中间漆屏蔽作用", "expected_keywords": ["云铁", "环氧", "中间漆", "屏蔽"], "min_relevance_category": "epoxy"},
    {"question": "氟碳面漆耐候年限评估", "expected_keywords": ["氟碳", "面漆", "耐候", "年限"], "min_relevance_category": "weathering"},
    {"question": "粉末涂料与液体环氧对比", "expected_keywords": ["粉末", "环氧", "涂料", "固化"], "min_relevance_category": "coating_comparison"},
    {"question": "低温固化环氧体系研究进展", "expected_keywords": ["低温", "固化", "环氧", "体系"], "min_relevance_category": "epoxy"},
    {"question": "附着力促进剂用量窗口", "expected_keywords": ["附着力", "促进剂", "用量", "涂料"], "min_relevance_category": "additives"},
    {"question": "消泡剂对涂膜外观缺陷的影响", "expected_keywords": ["消泡", "涂膜", "外观", "缺陷"], "min_relevance_category": "additives"},
    {"question": "流平剂改善橘皮的机理", "expected_keywords": ["流平", "橘皮", "涂膜", "外观"], "min_relevance_category": "additives"},
    {"question": "基材残油导致起泡剥落案例", "expected_keywords": ["残油", "起泡", "剥落", "基材"], "min_relevance_category": "failure"},
    {"question": "涂膜鼓泡与渗透压关系", "expected_keywords": ["鼓泡", "渗透", "涂膜", "水"], "min_relevance_category": "failure"},
    {"question": "闪锈抑制剂在水性底漆中的应用", "expected_keywords": ["闪锈", "水性", "底漆", "抑制剂"], "min_relevance_category": "waterborne"},
    {"question": "高固体分环氧降低溶剂排放", "expected_keywords": ["高固体", "环氧", "溶剂", "VOC"], "min_relevance_category": "waterborne"},
    {"question": "热浸锌表面涂装前处理要点", "expected_keywords": ["热浸锌", "前处理", "涂装", "表面"], "min_relevance_category": "pretreatment"},
    {"question": "不锈钢钝化液配方注意事项", "expected_keywords": ["不锈钢", "钝化", "配方", "耐蚀"], "min_relevance_category": "passivation"},
    {"question": "铜基材有机缓蚀剂选择", "expected_keywords": ["铜", "缓蚀", "有机", "基材"], "min_relevance_category": "inhibitors"},
    {"question": "盐雾720小时失效形貌分析", "expected_keywords": ["盐雾", "720", "失效", "形貌"], "min_relevance_category": "testing"},
    {"question": "涂层干膜厚度DFT检测频次", "expected_keywords": ["干膜", "厚度", "DFT", "检测"], "min_relevance_category": "film_properties"},
    {"question": "多道涂层层间附着力控制", "expected_keywords": ["层间", "附着力", "涂层", "多道"], "min_relevance_category": "system_design"},
    {"question": "环氧胺当量比计量方法", "expected_keywords": ["胺当量", "环氧", "计量", "固化剂"], "min_relevance_category": "epoxy"},
    {"question": "颜基比对防腐颜料效率影响", "expected_keywords": ["颜基比", "颜料", "防腐", "效率"], "min_relevance_category": "pigments"},
    {"question": "施工粘度与稀释剂选择", "expected_keywords": ["粘度", "稀释剂", "施工", "涂料"], "min_relevance_category": "coating_process"},
]

# ── Sample documents for CI ingest ────────────────────────────────────────────
# Injected into the KB before golden-eval so hybrid_search has content.
# Keep keyword coverage broad so 50+ questions can hit top-3.

sample_documents: list[dict] = [
    {
        "title": "环氧树脂防腐机理研究",
        "text": (
            "环氧树脂作为主要成膜物质，具有优异的附着力和耐化学性。"
            "其防腐机理主要依靠固化后形成致密的交联网络结构，"
            "有效阻隔水、氧和腐蚀性离子的渗透。"
            "通过添加胺类固化剂，环氧树脂在室温下即可固化，"
            "形成高交联密度的防腐涂层。胺当量计量控制固化剂用量窗口。"
            "环氧底漆可提高对基材附着力；玻璃鳞片环氧可降低渗透。"
            "低温固化环氧体系与高固体分环氧有助于降低VOC与溶剂排放。"
        ),
    },
    {
        "title": "磷酸锌防锈颜料应用",
        "text": (
            "磷酸锌是一种重要的无毒防锈颜料，广泛应用于防腐蚀涂料中。"
            "其在涂层中缓慢水解生成磷酸根离子和锌离子，"
            "与金属基材反应形成钝化保护膜，显著提升盐雾耐受时间。"
            "配合环氧树脂使用可达到500小时以上的中性盐雾试验要求。"
            "磷酸铝锌等防锈颜料可调节颜基比以提升防腐效率。"
            "锌粉富锌底漆提供阴极保护；铝粉颜料有浮型与非浮型之分。"
        ),
    },
    {
        "title": "防腐蚀涂料工艺参数指南",
        "text": (
            "固化温度控制在80-120°C范围内可获得最佳防腐性能。"
            "涂膜厚度建议控制在25-35微米之间，干膜厚度DFT需按频次检测。"
            "表面处理前需进行除油脱脂工艺，确保基材清洁度达到Sa2.5级喷砂要求。"
            "盐雾试验按GB/T 1771标准执行，中性盐雾试验(NSS)是常用评价方法；"
            "铜加速盐雾CASS用于装饰性镀层加速评价。湿度影响聚氨酯固化。"
            "固化时间过短会导致涂膜交联不足而发软；施工粘度与稀释剂需匹配。"
        ),
    },
    {
        "title": "聚氨酯与环氧涂料性能对比",
        "text": (
            "聚氨酯涂料具有优异的耐候性和柔韧性，适用于户外暴露环境。"
            "环氧涂料附着力强、耐化学性好，但在紫外线照射下易粉化。"
            "二者常配合使用：环氧底漆提供防腐和附着力，聚氨酯面漆提供耐候保护。"
            "重防腐配套体系常含环氧云铁中间漆屏蔽层与氟碳面漆耐候层。"
            "粉末涂料与液体环氧在固化工艺上存在差异。"
        ),
    },
    {
        "title": "前处理与钝化技术综述",
        "text": (
            "硅烷偶联剂可在前处理中提升涂层附着力。"
            "磷化膜厚度与耐蚀性相关；热浸锌表面涂装前处理需去锌盐。"
            "无铬钝化膜耐蚀机理依赖致密氧化膜；稀土铈盐钝化有工艺窗口。"
            "不锈钢钝化液配方需控制氧化剂浓度以保证耐蚀。"
            "钼酸盐缓蚀剂可作为铬酸盐替代方向；铜基材宜选有机缓蚀剂。"
        ),
    },
    {
        "title": "涂层测试与失效分析",
        "text": (
            "附着力划格试验参照ISO 2409解读等级。"
            "电化学阻抗谱可用于评价涂层防护性能。"
            "盐雾720小时失效形貌常表现为起泡、剥落与锈蚀扩展。"
            "基材残油会导致起泡剥落；涂膜鼓泡与渗透压及水渗透相关。"
            "多道涂层层间附着力需控制重涂间隔。"
        ),
    },
    {
        "title": "水性与助剂体系",
        "text": (
            "水性环氧涂料VOC控制依赖乳液与成膜助剂。"
            "闪锈抑制剂用于水性底漆抑制早期锈蚀。"
            "消泡剂影响涂膜外观缺陷；流平剂可改善橘皮。"
            "附着力促进剂存在最佳用量窗口。"
            "纳米二氧化硅改性环氧可提升耐磨性。"
            "丙烯酸面漆耐紫外老化；氟碳面漆耐候年限更长。"
        ),
    },
    {
        "title": "服役环境与联合防护",
        "text": (
            "海洋大气环境下涂层选型强调底漆屏蔽与面漆耐候。"
            "工业大气SO2加速涂膜老化。"
            "阴极保护与有机涂层可联合防护钢结构。"
            "重防腐涂层配套体系设计原则是底中面功能分工。"
        ),
    },
]
