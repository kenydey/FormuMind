"""W6-1 · P1-38: 严谨性 rubric 的 golden 问答集（纯数据）。

每组格式::

    {
        "question": "...",
        "answer": ".....[^1].....[^2]",
        "evidence": [
            {"identifier": "...", "title": "...", "doi": "...",
             "text": "<passage/snippet 原文，含答案数字的来源>", "page": 3},
            ...
        ],
        "key_claims": [
            {"text": "...", "keywords": ["盐雾", "1000小时"]},
            ...
        ],
    }

答案中的 ``[^n]`` 按位置对应 ``evidence[n-1]``；答案数字必须能在所引
evidence 的 text 中找到（直接出现或经明确单位换算）。
"""

from __future__ import annotations

golden_rigor_pairs: list[dict] = [
    {
        "question": "中性盐雾试验如何评价防腐涂料？",
        "answer": (
            "中性盐雾试验（NSS）按 GB/T 1771 在 35°C 下连续喷雾，"
            "以出现锈蚀的时间评价涂层耐蚀性[^1]。"
            "本体系 1000 小时盐雾后划线处单边锈蚀扩展小于 2mm，"
            "满足重防腐要求[^2]。"
        ),
        "evidence": [
            {
                "identifier": "GB/T 1771-2007",
                "title": "色漆和清漆 耐中性盐雾性能的测定",
                "doi": "",
                "text": "中性盐雾试验（NSS）按 GB/T 1771 执行，试验温度 35°C，连续喷雾。",
                "page": 4,
            },
            {
                "identifier": "test-report-2026-041",
                "title": "环氧富锌配套体系盐雾测试报告",
                "doi": "",
                "text": "1000 小时盐雾后，划线处单边锈蚀扩展 1.5mm（重防腐限值 2mm），满足配套要求。",
                "page": 2,
            },
        ],
        "key_claims": [
            {"text": "NSS 按 GB/T 1771 在 35°C 下评价", "keywords": ["盐雾", "GB/T 1771", "35°C"]},
            {"text": "1000 小时盐雾后锈蚀扩展小于 2mm", "keywords": ["1000小时", "锈蚀", "2mm"]},
        ],
    },
    {
        "question": "磷化膜重控制在什么范围？磷化液 pH 呢？",
        "answer": (
            "锌系磷化膜重控制在 2~5g/m²，膜重过低耐蚀不足，过高则涂装附着力下降[^1]。"
            "磷化工作液 pH 维持在 2.8-3.2，pH 偏高会加速沉渣生成[^2]。"
        ),
        "evidence": [
            {
                "identifier": "pretreat-manual-ch3",
                "title": "金属前处理工艺手册（磷化分册）",
                "doi": "",
                "text": "锌系磷化膜重 2~5g/m² 为宜；膜重低于 2g/m² 耐蚀性不足，高于 5g/m² 影响附着力。",
                "page": 31,
            },
            {
                "identifier": "pretreat-manual-ch3",
                "title": "金属前处理工艺手册（磷化分册）",
                "doi": "",
                "text": "磷化工作液 pH 控制在 2.8-3.2；pH 超过 3.5 沉渣量显著增加。",
                "page": 33,
            },
        ],
        "key_claims": [
            {"text": "锌系磷化膜重 2~5g/m²", "keywords": ["磷化", "膜重", "5g"]},
            {"text": "磷化液 pH 2.8-3.2", "keywords": ["pH", "磷化液"]},
        ],
    },
    {
        "question": "环氧涂料固化温度和干膜厚度推荐值？",
        "answer": (
            "该双组分环氧在 80-120°C 下固化 30 分钟可达完全交联[^1]。"
            "干膜厚度（DFT）建议 80~125μm，分两道施工[^2]。"
        ),
        "evidence": [
            {
                "identifier": "tds-epoxy-880",
                "title": "EP-880 双组分环氧底漆技术数据表",
                "doi": "",
                "text": "固化条件：80-120°C/30min；低于 80°C 需延长至 60 分钟。",
                "page": 1,
            },
            {
                "identifier": "tds-epoxy-880",
                "title": "EP-880 双组分环氧底漆技术数据表",
                "doi": "",
                "text": "推荐干膜厚度 DFT 80~125μm，建议分两道喷涂达到。",
                "page": 1,
            },
        ],
        "key_claims": [
            {"text": "80-120°C 固化 30 分钟", "keywords": ["固化", "120°C", "30分钟"]},
            {"text": "DFT 80~125μm 两道施工", "keywords": ["DFT", "125μm"]},
        ],
    },
    {
        "question": "富锌底漆的锌粉含量要求？",
        "answer": (
            "无机富锌底漆干膜中锌粉含量不低于 80%，"
            "才能形成连续导电网络提供阴极保护[^1]。"
            "配套面漆前需充分固化，环境湿度低于 85% 时施工[^2]。"
        ),
        "evidence": [
            {
                "identifier": "ISO 12944-5",
                "title": "色漆和清漆 钢结构防腐涂层体系",
                "doi": "",
                "text": "无机富锌底漆干膜锌粉含量应不低于 80%，以保证阴极保护所需的导电连续性。",
                "page": 12,
            },
            {
                "identifier": "ISO 12944-4",
                "title": "色漆和清漆 表面处理与施工",
                "doi": "",
                "text": "富锌底漆施工时环境相对湿度应低于 85%，底漆需充分固化后再涂面漆。",
                "page": 7,
            },
        ],
        "key_claims": [
            {"text": "干膜锌粉含量不低于 80%", "keywords": ["锌粉", "80%"]},
            {"text": "湿度低于 85% 施工", "keywords": ["湿度", "85%"]},
        ],
    },
    {
        "question": "水性环氧涂料的 VOC 限值？",
        "answer": (
            "水性环氧底漆 VOC 含量应≤100g/L，满足现行环保要求[^1]。"
            "施工粘度控制在 25~35s（涂-4 杯），稀释用水量不超过 10%[^2]。"
        ),
        "evidence": [
            {
                "identifier": "GB 30981-2020",
                "title": "建筑用墙面涂料中有害物质限量",
                "doi": "",
                "text": "水性环氧类底漆 VOC 限值为 100g/L，产品实测 78g/L 符合要求。",
                "page": 3,
            },
            {
                "identifier": "tds-wb-epoxy-210",
                "title": "WB-210 水性环氧底漆施工指南",
                "doi": "",
                "text": "施工粘度 25~35s（涂-4 杯，25°C）；加水稀释不超过 10%。",
                "page": 2,
            },
        ],
        "key_claims": [
            {"text": "VOC≤100g/L", "keywords": ["VOC", "100g"]},
            {"text": "施工粘度 25~35s，加水≤10%", "keywords": ["粘度", "35s", "10%"]},
        ],
    },
    {
        "question": "附着力划格试验如何判定？",
        "answer": (
            "按 ISO 2409 划格试验，0 级为最佳（切口交叉处无剥落）[^1]。"
            "本涂层实测 1 级，剥落面积约 5%，满足配套体系要求[^2]。"
        ),
        "evidence": [
            {
                "identifier": "ISO 2409:2020",
                "title": "色漆和清漆 划格试验",
                "doi": "",
                "text": "0 级：切口交叉处无剥落；1 级：剥落面积不大于 5%。",
                "page": 5,
            },
            {
                "identifier": "test-report-2026-058",
                "title": "配套体系附着力测试记录",
                "doi": "",
                "text": "划格法实测 1 级，剥落面积约 5%，判定满足配套体系附着力要求。",
                "page": 1,
            },
        ],
        "key_claims": [
            {"text": "ISO 2409 0 级最佳", "keywords": ["ISO 2409", "0级"]},
            {"text": "实测 1 级剥落约 5%", "keywords": ["1级", "5%"]},
        ],
    },
    {
        "question": "CASS 试验与中性盐雾的区别？",
        "answer": (
            "铜加速乙酸盐雾（CASS）试验液 pH 为 3.1-3.3，温度 50°C，"
            "腐蚀速率远高于中性盐雾[^1]。"
            "装饰性镀层常用 16 小时 CASS 评价，相当于数百小时 NSS[^2]。"
        ),
        "evidence": [
            {
                "identifier": "GB/T 10125-2021",
                "title": "人造气氛腐蚀试验 盐雾试验",
                "doi": "",
                "text": "CASS 试验：pH 3.1-3.3，温度 50°C±2°C，乙酸+氯化铜加速。",
                "page": 9,
            },
            {
                "identifier": "plating-handbook-ch8",
                "title": "电镀工艺手册（腐蚀试验分册）",
                "doi": "",
                "text": "装饰铬镀层一般采用 16 小时 CASS 试验，其加速倍率约为 NSS 的数十倍。",
                "page": 88,
            },
        ],
        "key_claims": [
            {"text": "CASS pH 3.1-3.3、50°C", "keywords": ["CASS", "pH", "50°C"]},
            {"text": "16 小时 CASS 用于装饰镀层", "keywords": ["16小时", "CASS"]},
        ],
    },
    {
        "question": "喷砂 Sa2.5 的表面粗糙度要求？",
        "answer": (
            "Sa2.5 级喷砂后表面粗糙度 Rz 控制在 40~75μm，"
            "可保证涂层锚纹附着[^1]。"
            "喷砂磨料粒度 0.5-1.5mm，压缩空气压力 0.5-0.7MPa[^2]。"
        ),
        "evidence": [
            {
                "identifier": "ISO 8503-1",
                "title": "钢材表面粗糙度评定",
                "doi": "",
                "text": "Sa2.5 喷砂表面粗糙度 Rz 宜为 40~75μm，锚纹深度满足涂层附着要求。",
                "page": 6,
            },
            {
                "identifier": "blast-sop-014",
                "title": "喷砂作业指导书 SOP-014",
                "doi": "",
                "text": "磨料粒度 0.5-1.5mm；空压机出口压力 0.5-0.7MPa。",
                "page": 2,
            },
        ],
        "key_claims": [
            {"text": "Sa2.5 粗糙度 40~75μm", "keywords": ["Sa2.5", "75μm"]},
            {"text": "磨料 0.5-1.5mm、气压 0.5-0.7MPa", "keywords": ["磨料", "0.7MPa"]},
        ],
    },
    {
        "question": "氟碳面漆耐候年限与重涂周期？",
        "answer": (
            "氟碳面漆在工业大气环境下耐候年限可达 15 年以上[^1]。"
            "配套环氧中间漆干膜 100μm 时，体系首次大修周期约 10 年[^2]。"
        ),
        "evidence": [
            {
                "identifier": "weathering-study-2024",
                "title": "氟碳面漆户外曝晒 10 年跟踪报告",
                "doi": "",
                "text": "氟碳面漆工业大气曝晒 10 年失光率<15%，推算耐候年限 15 年以上。",
                "page": 14,
            },
            {
                "identifier": "ISO 12944-2",
                "title": "色漆和清漆 环境腐蚀性分类",
                "doi": "",
                "text": "C4 环境配套（环氧中间漆干膜 100μm+氟碳面漆）首次大修周期约 10 年。",
                "page": 11,
            },
        ],
        "key_claims": [
            {"text": "氟碳面漆耐候 15 年以上", "keywords": ["氟碳", "15年"]},
            {"text": "体系大修周期约 10 年", "keywords": ["大修", "10年"]},
        ],
    },
    {
        "question": "水性底漆闪锈抑制：体系 pH 控制？",
        "answer": (
            "水性醇酸底漆体系 pH 维持在 8.5~9.5 可有效抑制闪锈[^1]。"
            "闪锈抑制剂添加量为 0.5-1.0%，过量会导致附着力下降[^2]。"
        ),
        "evidence": [
            {
                "identifier": "wb-formulation-note-33",
                "title": "水性底漆闪锈问题技术备忘",
                "doi": "",
                "text": "体系 pH 8.5~9.5 时闪锈最轻；pH 低于 8.0 闪锈显著加重。",
                "page": 3,
            },
            {
                "identifier": "wb-formulation-note-33",
                "title": "水性底漆闪锈问题技术备忘",
                "doi": "",
                "text": "闪锈抑制剂用量 0.5-1.0%；超过 1.5% 层间附着力下降一级。",
                "page": 4,
            },
        ],
        "key_claims": [
            {"text": "体系 pH 8.5~9.5 抑制闪锈", "keywords": ["pH", "闪锈"]},
            {"text": "抑制剂用量 0.5-1.0%", "keywords": ["抑制剂", "1.0%"]},
        ],
    },
    {
        "question": "环氧胺固化剂当量比如何计算？",
        "answer": (
            "胺当量比按环氧当量÷胺氢当量=1:1 计算，"
            "本体系环氧当量 190g/eq，固化剂胺氢当量 60g/eq，"
            "配比为 100:31.6[^1]。偏差超过 ±10% 会显著降低交联密度[^2]。"
        ),
        "evidence": [
            {
                "identifier": "epoxy-formula-guide",
                "title": "环氧配方计算指南",
                "doi": "",
                "text": "胺固化配比=环氧当量/胺氢当量；例：190g/eq÷60g/eq，100 份树脂配 31.6 份固化剂。",
                "page": 21,
            },
            {
                "identifier": "epoxy-formula-guide",
                "title": "环氧配方计算指南",
                "doi": "",
                "text": "当量比偏差超过 ±10%，涂膜交联密度下降，耐化学性劣化。",
                "page": 22,
            },
        ],
        "key_claims": [
            {"text": "当量比 1:1，配比 100:31.6", "keywords": ["当量比", "31.6"]},
            {"text": "偏差超 ±10% 交联密度下降", "keywords": ["10%", "交联"]},
        ],
    },
    {
        "question": "电泳漆膜厚与泳透力关系？",
        "answer": (
            "阴极电泳漆膜厚控制在 18~25μm，泳透力（四板盒法）≥75%[^1]。"
            "槽液温度 28-32°C，固体分 18-22%，电泳时间 2-3 分钟[^2]。"
        ),
        "evidence": [
            {
                "identifier": "ed-coat-spec-102",
                "title": "阴极电泳涂装工艺规范",
                "doi": "",
                "text": "膜厚 18~25μm；四板盒法泳透力≥75% 方为合格。",
                "page": 5,
            },
            {
                "identifier": "ed-coat-spec-102",
                "title": "阴极电泳涂装工艺规范",
                "doi": "",
                "text": "槽液温度 28-32°C，槽液固体分 18-22%，电泳时间 2-3min。",
                "page": 6,
            },
        ],
        "key_claims": [
            {"text": "膜厚 18~25μm，泳透力≥75%", "keywords": ["膜厚", "75%"]},
            {"text": "槽液 28-32°C、电泳 2-3 分钟", "keywords": ["槽液", "32°C", "3分钟"]},
        ],
    },
]

__all__ = ["golden_rigor_pairs"]
