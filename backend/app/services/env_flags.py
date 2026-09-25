"""Runtime-configurable boolean feature flags (environment variables).

The Settings UI exposes every boolean ``FORMUMIND_*`` feature switch as a
True/False toggle.  Updates are applied in three layers so they both take
effect immediately and survive restarts:

1. ``os.environ`` — the live process environment;
2. ``.env`` upsert (reusing :func:`secrets_store.write_env_updates`);
3. ``get_settings.cache_clear()`` — every subsequent ``get_settings()`` call
   sees the new value (services read settings per call, not at import).

The LLM runtime overlay (``runtime_secrets``) is deliberately left untouched
so an unsaved provider switch in the UI is not reset by a flag change.

Deliberately NOT exposed here (server-environment only):
* ``FORMUMIND_API_AUTH_ENABLED`` — flipping it on without a token would 401
  every request including this API (self-lockout);
* ``FORMUMIND_ENVIRONMENT`` — changes dev/prod semantics wholesale.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnvFlag:
    attr: str          # Settings attribute name
    label: str         # human label (zh)
    description: str   # what it does + prerequisites
    category: str      # grouping key for the UI
    hint: str = ""     # activation caveat（需网络 / 需依赖 / 需重启…）
    # Batch E: product maturity for PathWizard / Settings badges.
    # stable | beta | experimental | disabled
    maturity: str = "stable"

    @property
    def env_key(self) -> str:
        return f"FORMUMIND_{self.attr.upper()}"


CATEGORY_LABELS: dict[str, str] = {
    "retrieval": "检索 · Retrieval",
    "kb": "知识库 · Knowledge Base",
    "chem": "化学引擎 · Chemistry",
    "data": "数据与训练 · Data",
    "infra": "基础设施 · Infra",
}

FLAG_REGISTRY: tuple[EnvFlag, ...] = (
    # ── 检索 ──────────────────────────────────────────────────────────────
    EnvFlag("gpu_enabled", "GPU 加速 ColBERT 检索",
            "启用后使用 PyLate ColBERT 作为知识库检索后端（需 CUDA GPU ≥ 4GB VRAM）。"
            "关闭时使用 BM25 + FAISS 混合检索（纯 CPU，不限硬件，零 AVX2 要求）。",
            "retrieval",
            "切换后需重启生效；GPU/CUDA 不可用时自动退回 CPU 模式"),
    EnvFlag("domain_profile_search", "领域 Profile 检索",
            "按 ProductDomain 套用 DomainSearchProfile（OpenAlex concept / S2 学科 / CPC·IPC / ChemRxiv）。关闭则回退旧全局过滤。",
            "retrieval"),
    EnvFlag("openalex_concept_filter", "OpenAlex Concept 过滤",
            "OpenAlex 请求附加 concepts.id 过滤器（需 domain_profile_search）。",
            "retrieval", "需网络"),
    EnvFlag("patent_cpc_filter", "专利 CPC 过滤",
            "专利查询附加 CPC=(…)；无 CPC 元数据时降权不直接丢弃。",
            "retrieval"),
    EnvFlag("web_search_allow_ddgs", "DuckDuckGo 兜底检索",
            "互联网检索的顺序是 Tavily → SerpAPI → DuckDuckGo。DuckDuckGo 是唯一"
            "不需要 API key 的一档，但结果质量明显弱于前两档。关闭后若没有可用密钥，"
            "互联网检索会诚实地返回空，而不是塞一批低质量结果。"
            "每条结果的来源都标着是哪一档给的，可据此判断要不要关。",
            "retrieval", "关闭后需配置 Tavily 或 SerpAPI 密钥，否则互联网源无结果"),
    EnvFlag("openalex_enabled", "OpenAlex 检索",
            "文献检索包含 OpenAlex（2.5 亿条学术元数据）。", "retrieval", "需网络"),
    EnvFlag("federated_sources_notebooklm", "联邦检索含 NotebookLM",
            "CRAG fallback 联邦检索时把 NotebookLM 加入默认源列表。", "retrieval",
            "需先启用 NotebookLM 资料源"),
    EnvFlag("notebooklm_enabled", "NotebookLM 资料源",
            "把 Google NotebookLM 笔记本作为检索来源。", "retrieval",
            "需 notebooklm extra + 一次性浏览器登录"),
    EnvFlag("deep_hyde_enabled", "深度研究 HyDE",
            "深度研究用 HyDE 生成假设文档增强检索召回（无结果时提升相关性）。",
            "retrieval", "需有效 LLM key"),
    # ── 知识库 ────────────────────────────────────────────────────────────
    EnvFlag("content_filter_enabled", "检索质量过滤",
            "规则层过滤：垃圾域名黑名单、空洞摘要、SimHash 近重复合并。", "kb"),
    EnvFlag("content_filter_llm_judge", "LLM 质量判定",
            "检索结束后对最终榜单做一次 LLM 批量质量审查（每次检索一次调用）。",
            "kb", "需有效 LLM key"),
    EnvFlag("search_rerank_enabled", "检索 LLM 精排",
            "对合并结果前若干条做 LLM 语义重排，其余按规则排序保留（总量上限 300）。",
            "retrieval", "需有效 LLM key"),
    EnvFlag("kb_recommend_use_hybrid", "推荐融合探针 hybrid",
            "推荐/研究 KB 融合走与 Hub 检索探针同栈的 BM25+向量 hybrid（共享 kb_hybrid_alpha）。"
            "关闭则退回旧 search_chunks。",
            "retrieval"),
    EnvFlag("kb_recommend_rerank_enabled", "推荐融合 LLM 精排",
            "仅对推荐/研究融合后的证据池做 LLM 精排；默认关，不改动全局 search_rerank_enabled。",
            "retrieval", "需有效 LLM key"),
    EnvFlag("fulltext_enrich", "检索全文获取",
            "把排名靠前的专利/OA 文献/网页命中升级为全文分块并持久化入知识库。",
            "kb", "需网络；每次深度研究会下载最多 8 篇全文"),
    EnvFlag("patent_prefer_html", "专利用落地页正文",
            "专利全文取 Google Patents 落地页的 abstract/description/claims，而不是先下 PDF。"
            "一次请求约 0.7 秒且完全不需要 OCR；中日文专利还附带英文机器翻译对照。"
            "关闭则优先下 PDF（能拿到图表原件，但慢得多，扫描件还要 OCR）。",
            "kb", "需网络；关闭后扫描版专利每页约 2 秒 OCR"),
    EnvFlag("pdf_download", "旧版专利 PDF 下载",
            "深度研究后下载专利 PDF 替换摘要（已被「检索全文获取」取代，保留兼容）。",
            "kb", "需网络"),
    EnvFlag("pdf_ocr", "扫描件 OCR 解析",
            "PDF 解析（MinerU 层）启用 OCR 管线，可读取扫描/图片型 PDF。", "kb",
            "需 magic-pdf OCR 依赖；解析显著变慢"),
    EnvFlag("rapidocr_enabled", "本地 OCR（扫描件）",
            "扫描件无文字层时用本地 OCR 读出文字，无需 MinerU 配额。"
            "只出文字，表格/图表仍交给 MinerU 或视觉模型。",
            "kb", "需 rapidocr-onnxruntime；CPU 约 1-3 秒/页，模型随包分发"),
    EnvFlag("mineru_enabled", "MinerU 云端解析",
            "本地解析不好的单页（密集表格/公式/图表）升级到 MinerU 云端。", "kb",
            "需 MinerU Token；被升级的页面会上传至 mineru.net（第三方）"),
    EnvFlag("mineru_batch_enabled", "MinerU 批量升级",
            "同一份文档的多个升级页合并为一次 MinerU batch 提交（服务端并行），"
            "替代逐页串行往返，多图表 PDF 显著提速。关闭则退回逐页串行。", "kb",
            "需 MinerU Token；批量提交受 MinerU 并发/配额约束"),
    EnvFlag("pdf_local_ocr", "本地版面解析内置 OCR",
            "pymupdf4llm 在版面解析时对文字稀疏的页面逐页 OCR。它跑在解析级联最前面、"
            "对每一份 PDF 都跑，实测是入库耗时的最大来源；扫描件另有专门的层处理，"
            "所以默认关闭。", "kb",
            "需机器上装有 Tesseract 才会生效；开启后解析显著变慢"),
    EnvFlag("pdf_layout_analysis", "PDF 版面分析",
            "双栏页面按栏读取，避免左右文字交错。关闭可省约一半内存、快 25 倍，"
            "但双栏论文/专利会被读串。", "kb"),
    EnvFlag("pdf_formula_enrichment", "公式转 LaTeX",
            "PDF 解析（Docling 层）把显示公式/反应方程式识别为 LaTeX，切块与渲染保真。",
            "kb", "需 docling；首次使用下载公式模型"),
    EnvFlag("kb_ingest_auto", "检索后台自动入库",
            "检索/深度研究/推荐结束后，后台任务逐篇获取全文并构建知识库，前台实时显示每篇状态。",
            "kb", "需网络；默认入库全部可获取全文的命中（FORMUMIND_KB_INGEST_MAX_DOCS=0 不限制）"),
    EnvFlag("kb_ingest_patent_exempt", "入库专利主题豁免",
            "True=专利跳过主题门（旧行为）。P0 默认 False：专利需 CPC/主题词命中。",
            "kb"),
    EnvFlag("kb_relevance_shadow", "相关性闸影子模式",
            "False（默认，Top-5 #1）= 按 topicality 真闸（阈值 FORMUMIND_KB_INGEST_MIN_RELEVANCE）。"
            "True = 只记录将拒收率、不改入库（校准/回滚）。"
            "看 GET /api/kb/relevance-shadow/stats。",
            "kb", "出问题可临时开回影子"),
    EnvFlag("kb_search_deny_enabled", "检索期负向收缩",
            "True（默认）= 应用域 search_deny + 扩展 negative_terms，压低/拦漂移命中。"
            "False = 关闭检索期负向（入库 keyword_deny / topic_gate 仍生效）。",
            "kb"),
    EnvFlag("workbench_auto_train", "台账自动回灌训练",
            "实验台账 Completed 行保存时自动写入 ModelRegistry 并触发重训。", "data"),
    EnvFlag("auto_loop_on_sync", "台账保存后自动闭环",
            "Completed 行回灌训练后，后台触发 optimize + 下一轮 DOE（/api/loop/iterate）。", "data",
            maturity="beta"),
    EnvFlag("auto_adopt_next_doe_on_loop", "闭环后自动采纳下一轮 DOE",
            "闭环 iterate 成功后自动把 next_doe adopt 进实验台账（createWorkbenchCampaign）。"
            "默认关；与 auto_loop_on_sync 独立——开闭环不等于自动改写台账。",
            "data", "默认勿开；确认台账策略后再开", maturity="experimental"),
    EnvFlag("materials_suppliers_json_dual_write", "材料供应商 JSON 双写",
            "True（默认）= upsert 时同时写 material_suppliers 与 suppliers_json。"
            "False = 只写归一化表并清空 JSON 投影（读路径仍从 link 表回填）。",
            "data", "关前确认无旧客户端只读 JSON"),
    EnvFlag("kg_enabled", "知识图谱 P0",
            "实体索引 + 枚举型 RAG：牌号/CAS/元素完备召回，增强 Chat/CRAG。", "kb"),
    EnvFlag("kg_entities_on_ingest", "入库实体提及",
            "文档切块入库后自动写入 kb_entities / kb_mentions（快，枚举 RAG 根基）。", "kb"),
    EnvFlag("kg_relations_on_ingest", "入库关系提取",
            "入库时抽取实体语义关系写入 kb_entity_links（慢，LLM 关系建议后置）。"
            "关闭时实体/提及仍会写入——Settings「补语义关系」或 POST /api/kg/relations/rebuild "
            "可事后补齐（不依赖本旗标 / kg_relation_extract_enabled；Top-5′ #4）。",
            "kb", maturity="beta"),
    EnvFlag("kg_relation_extract_enabled", "KG 语义关系抽取",
            "link_source 入库同步时抽取 substitutes/synergizes 等。"
            "异步 rebuild 可 force 绕过本旗标。", "kb", maturity="beta"),
    EnvFlag("kg_llm_relation_extract", "KG LLM 关系抽取",
            "规则未命中时用 LLM 补充关系抽取（增加成本）。", "kb", "需有效 LLM key",
            maturity="experimental"),
    EnvFlag("kg_multimodal_fusion_enabled", "多模态图谱融合",
            "专利对比表格图片 → 配方/性能结构化边写入 kb_entity_links。", "kb",
            "需 vision_extract + kg_enabled + LLM key", maturity="experimental"),
    EnvFlag("kg_measured_feedback_enabled", "KG 实测反馈",
            "知识图谱中材料的实测证据（measured 关系）参与配方评分加成。", "kb",
            "需 kg_enabled"),
    EnvFlag("kg_llm_product_hint", "KG LLM 产品提示",
            "规则未命中时用 LLM 补充商业产品/牌号识别（增加成本）。", "kb",
            "需有效 LLM key"),
    EnvFlag("kg_chat_entity_refs_on_evidence", "KG Chat 证据实体引用",
            "Chat 答案的证据块自动附带 KG 实体引用。", "kb", "需 kg_enabled"),
    EnvFlag("kg_contradiction_demote", "KG 矛盾降级",
            "文献与实测冲突的替代候选在 discover 排序中置后。", "kb"),
    EnvFlag("chat_multi_turn_enabled", "Chat 多轮上下文",
            "根据对话 history 改写检索 query，支持指代追问。", "kb"),
    EnvFlag("chat_structured_enabled", "Chat 结构化输出",
            "response_format=structured 时返回 JSON 结构化答案。", "kb"),
    EnvFlag("chat_clarification_enabled", "Chat 软澄清",
            "歧义术语时返回答案 + clarification 选项（非阻塞）。", "kb"),
    EnvFlag("chat_claim_check_enabled", "Chat 论断溯源",
            "对答案 claim 做 chunk 级核验（增加延迟）。", "kb"),
    EnvFlag("chat_rerank_enabled", "Chat LLM 精排",
            "问答检索候选用 LLM 打分精排（无 GPU 时替代 ColBERT 语义排序）。", "kb",
            "需有效 LLM key"),
    EnvFlag("recommend_diversity_enabled", "推荐配方多样性",
            "Top-N 推荐用 MMR 降低成分高度相似的重复方案。", "data"),
    EnvFlag("recommend_tradeoff_enabled", "推荐 Trade-off 分析",
            "返回 Pareto 前沿、对比表与场景推荐。", "data"),
    EnvFlag("loop_convergence_enabled", "闭环 RMSE 收敛判停",
            "RMSE 连续多轮变化低于阈值时跳过寻优/DOE 并提示停止迭代。", "data"),
    EnvFlag("recommend_uncertainty_flag", "推荐不确定性标记",
            "推荐结果附带预测不确定性（标准差）标记，供科研判断参考。", "data"),
    EnvFlag("verification_doe_enabled", "验证 DOE",
            "闭环优化后生成验证性 DOE 实验设计（补充验证轮）。", "data"),
    EnvFlag("kb_v2_enabled", "持久知识库 v2",
            "导入/检索的文档结构感知切块入库，问答与推荐检索覆盖全部累计语料。", "kb"),
    EnvFlag("wiki_enabled", "LLM Wiki 知识层",
            "在 Raw Chunk/KG 之上维护人类可读的规范 Markdown 页（材料/化学品）。"
            "关闭后 Wiki API 与编译全停；不影响现有问答切片检索。", "kb"),
    EnvFlag("wiki_compile_on_ingest", "入库后编译 Wiki",
            "文档切块入库成功后异步合并/更新对应 Wiki 页（牌号、CAS）。",
            "kb", "依赖 wiki_enabled"),
    EnvFlag("wiki_chat_blend", "问答双轨融入 Wiki",
            "Chat 检索同时召回 Wiki 凝练页（W3，默认开）。", "kb", "依赖 wiki_enabled"),
    EnvFlag("wiki_doe_constraints", "DOE 读取 Wiki 约束",
            "DOE/因子建议采用 Wiki 编译的上下限与禁区（W4，默认开）。",
            "kb", "依赖 wiki_enabled"),
    EnvFlag("wiki_lint_on_compile", "编译后 Lint Wiki",
            "入库编译后标记 stale/conflict 等 Flag（W4）。", "kb", "依赖 wiki_enabled"),
    EnvFlag("wiki_neo4j_project", "Wiki 投影到 Neo4j",
            "将 wiki entity_id 薄投影为 Neo4j Compound（默认关）。",
            "kb", "需 neo4j_enabled", maturity="experimental"),
    EnvFlag("wiki_fts_enabled", "Wiki 全文检索 FTS",
            "对 Wiki 标题/path/flags/正文建 SQLite FTS5 索引（P2，默认开，确定性）。",
            "kb", "依赖 wiki_enabled"),
    EnvFlag("wiki_llm_themes_enabled", "Wiki L2 主题长文",
            "允许手动编译 themes/ 体系综述（可选用 LLM 叙述；默认关）。",
            "kb", "依赖 wiki_enabled；需有效 LLM key 才生成叙述段"),
    EnvFlag("wiki_project_dossier_enabled", "项目卷宗 Wiki",
            "按 project_id 维护 themes/project-*.md 八节卷宗 + .data.json（P4，默认开 · Top-5 #3）。",
            "kb", "依赖 wiki_enabled；可关"),
    EnvFlag("wiki_dossier_llm_narrative", "卷宗节叙述 LLM",
            "允许对 Dossier 叙述段调用 LLM；表格数字仍确定性写入（默认关）。",
            "kb", "依赖 wiki_project_dossier_enabled"),
    EnvFlag("wiki_dossier_auto_patch", "卷宗事件自动更新",
            "白名单事件自动 patch 对应节（默认关 · 产品化可开）。"
            "允许：project_updated / literature_ingested / doe_updated / lab_recorded / "
            "loop_updated / optimize_completed / attachment_uploaded；未知事件跳过（不全量 8 节）。"
            "不改 Claims/DOE；表格仍确定性。",
            "kb", "依赖 wiki_project_dossier_enabled；确认白名单后再开"),
    EnvFlag("wiki_dossier_report_enabled", "卷宗 Report 生成",
            "基于 DossierPack 生成 briefing/feasibility 等研发草稿（P5，默认开 · Top-5 #3）。",
            "kb", "依赖 wiki_project_dossier_enabled；draft_not_claims；LLM 润色另受 wiki_dossier_llm_narrative"),
    EnvFlag("wiki_storm_report_enabled", "STORM 长文报告",
            "异步 STORM 风格多章长文（大纲→分章→缝合）；落 reports/*-storm.md；"
            "L2 draft_not_claims，不进 Claims/DOE。默认开（Top-5′ #2）；不改同步短 Report。",
            "kb", "依赖 wiki_enabled + wiki_project_dossier_enabled + wiki_dossier_report_enabled；可关"),
    EnvFlag("wiki_storm_parallel", "STORM 分章有限并行",
            "depends_on 已满足的章节在同一波次用线程池并行起草（非 Celery chord）。"
            "默认关；仍受 wiki_storm_report_enabled 约束。",
            "kb", "依赖 wiki_storm_report_enabled；worker 数见 wiki_storm_parallel_workers",
            maturity="beta"),
    EnvFlag("wiki_page_graph_enabled", "Wiki 页链接图",
            "Hub Wiki「链接图」：按 [[wikilink]] 构图可视化（非配方/材料 KG）。"
            "默认开（Top-5‴ #5）；可关。",
            "kb", "依赖 wiki_enabled；与 Neo4j/材料图谱分离"),
    EnvFlag("wiki_catalog_inject_themes", "主题编译注入 Catalog",
            "L2 theme compile 时注入确定性 wiki catalog 片段作导航锚点（S2，默认关）。"
            "Catalog 本身始终可 GET/rebuild，不依赖本开关。",
            "kb", "依赖 wiki_enabled + wiki_llm_themes_enabled"),
    EnvFlag("wiki_chat_save_draft", "Chat 存为 Wiki 草稿",
            "允许把 Chat / Deep Research 回答写入 queries/ L2 草稿（unreviewed；不进 Claims/DOE）。"
            "默认开（Top-5 #3）；可关。",
            "kb", "依赖 wiki_enabled；需活动 project_id"),
    EnvFlag("wiki_embed_enabled", "Wiki 摘要进入检索栈",
            "将 Wiki 页摘要写入现有 document_chunks（source_kind=wiki）并参与 Chat 双轨检索；"
            "默认开（Top-5‴ #1）；不关闭 Raw chunk RAG，不新建向量库；Claims 仍滤 wiki。",
            "kb", "依赖 wiki_enabled；有 sentence-transformers 时带向量，否则关键词兜底"),
    EnvFlag("prediction_bias_soft_correct", "预测偏差软校准",
            "推荐/评分时用台账 prediction_bias.mean_error 校正 predicted（predicted−mean_error）。"
            "默认关；需 metric n≥prediction_bias_soft_correct_min_n。不改 measured / 台账行。",
            "data", "先看 BiasTrend 再开；校准后榜卡显示「已校准」", maturity="beta"),
    EnvFlag("source_guide_enabled", "导入文档 LLM 摘要",
            "上传/导入文档时用 LLM 提取全局参数空间与摘要（Source Guide）。",
            "kb", "需有效 LLM key"),
    # ── 化学引擎 ──────────────────────────────────────────────────────────
    EnvFlag("chemtools_enabled", "化学工具网关",
            "工具级化学能力：名称→SMILES/CAS、官能团、分子专利预筛、爆炸性筛查。",
            "chem", "需 rdkit/httpx/molbloom；缺库时自动降级"),
    EnvFlag("chat_chem_tools_enabled", "聊天化学 Tool Calling",
            "对话中通过供应商原生 tools 自动调用 chemtools / SureChemBL / RDKit 结构检索 / MolScribe。"
            "仅 OpenAI 兼容供应商生效；关闭后聊天静默直答。",
            "chem", "需 chemtools_enabled；MolScribe/SureChemBL 另受各自开关约束"),
    EnvFlag("external_substitutes", "材料替代联网检索",
            "材料替代弹窗默认按 CAS/SMILES 调用 PubChem 结构相似检索，列出可入库的联网候选。"
            "关闭后仅使用材料库内候选（请求仍可传 include_external，但会被部署开关压制）。",
            "chem", "需网络；失败降级为空列表，不影响库内替代"),
    EnvFlag("surechembl", "SureChEMBL 专利化学（鉴定/替代/检索）",
            "鉴定回退、替代结构相似，以及资料检索 content 通道（source=surechembl）。"
            "不自动入库；与 EPO/Google 专利源并列、互不替换。",
            "chem", "需网络访问 surechembl.org；失败降级为空"),
    EnvFlag("substitute_llm", "材料替代 AI/规则扩召回",
            "材料替代漏斗 L4：库内候选不足（或请求强制）时用化学规则表 + 可选 LLM 扩名。"
            "关闭后跳过 LLM，仅保留规则表种子（仍可被 include_llm=false 完全关闭）。",
            "chem", "LLM 需有效 key；失败降级为空 llm 列表"),
    EnvFlag("chem_extract_enabled", "化学实体抽取",
            "入库切块时识别 CAS/分子式/SMILES/反应方程式，写入 chunk 元数据供化学感知检索。",
            "chem", "纯离线规则层；SMILES 验证需 rdkit"),
    EnvFlag("product_extract_enabled", "商业产品识别",
            "识别商品牌号/供应商（规则+LLM 源摘要），聚合入产品登记簿反哺问答与推荐。",
            "chem"),
    EnvFlag("material_store_enabled", "材料空间（可扩展原料库）",
            "原料库从内置字面量升级为可增长的 materials 表：支持手工录入、"
            "从文献产品登记簿提升，并为逆向设计与材料替代提供候选池。",
            "chem", "关闭后退回内置 32 种原料，行为与升级前一致"),
    EnvFlag("vision_extract_enabled", "图片视觉解析",
            "上传图片经视觉大模型结构化：表格→Markdown、分子结构图→SMILES（RDKit 验证）。",
            "chem", "需具备视觉能力的 LLM（FORMUMIND_VISION_MODEL 可指定专用模型）"),
    EnvFlag("ocsr_enabled", "OCSR 离线结构识别",
            "化学结构图优先离线识别为 SMILES（免 token，省费用），失败自动回退视觉 LLM。"
            "MolScribe（torch-cpu）跑在独立 worker 进程，不占主服务内存。",
            "chem", "需独立 molscribe worker venv + Celery worker（冷启动约 35 秒）"),
    EnvFlag("chemtools_descriptor_features", "v2 分子描述符特征",
            "机器学习特征向量追加 6 个重量加权 RDKit 分子描述符。", "chem",
            "需 rdkit；切换后需重启以重训模型"),
    EnvFlag("enrich_compounds", "PubChem 原料富集",
            "启动时用 PubChemPy 按化学名补全知识库原料的 SMILES/分子量。", "chem",
            "需 intel extra + 网络；重启后生效"),
    # ── 数据与训练 ────────────────────────────────────────────────────────
    EnvFlag("auto_retrain", "实验自动重训",
            "提交新实验数据后自动重训代理模型。", "data"),
    EnvFlag("datalab_required", "Datalab 硬依赖",
            "True = ELN 不可达时硬失败 + /health degraded（Docker ELN 栈推荐）。"
            "False（产品默认 · Top-5″ #2）= 与 campaign/experiment_backend=auto 联用时"
            "不可达回退 sqlite「本地台账」，不标硬降级。",
            "data",
            "ELN 必装部署请开；笔记本/无 Docker 可保持关"),
    # ── 基础设施 ──────────────────────────────────────────────────────────
    EnvFlag("celery_eager", "任务同步执行",
            "后台任务在进程内同步执行（无需 Redis/Celery worker）。"
            "启动期不会同步重放积压 outbox（避免阻塞 :8000）；积压任务需手动重试或改用真实 worker。",
            "infra", "关闭前请确认 Redis 与 worker 已就绪；本地脏 outbox + eager 曾导致中栏 Load failed"),
    EnvFlag("neo4j_enabled", "Neo4j 图存储适配器",
            "启用可选 Neo4j Bolt 适配器（与 SQLite KG 并存）。关闭时所有 Neo4j API 返回空/禁用。",
            "infra",
            "需 neo4j driver + 可达的 FORMUMIND_NEO4J_URI/USER/PASSWORD",
            maturity="experimental"),
    EnvFlag("agent_bus_enabled", "多智能体事件总线",
            "启用 Redis Pub/Sub 事件总线（预留能力；Redis 不可达时静默 no-op）。",
            "infra", "需可达的 Redis"),
)

_FLAG_BY_ATTR = {f.attr: f for f in FLAG_REGISTRY}


def _validate_registry() -> None:
    """Every flag must be a real boolean Settings field (import-time check)."""
    for flag in FLAG_REGISTRY:
        field = Settings.model_fields.get(flag.attr)
        if field is None:
            raise RuntimeError(f"env flag {flag.attr!r} is not a Settings field")
        if not isinstance(field.default, bool):
            raise RuntimeError(f"env flag {flag.attr!r} is not a boolean setting")


_validate_registry()


def list_env_flags() -> list[dict]:
    """Current effective value + default for every exposed flag."""
    settings = get_settings()
    out: list[dict] = []
    for flag in FLAG_REGISTRY:
        default = bool(Settings.model_fields[flag.attr].default)
        out.append(
            {
                "attr": flag.attr,
                "env_key": flag.env_key,
                "label": flag.label,
                "description": flag.description,
                "category": flag.category,
                "category_label": CATEGORY_LABELS.get(flag.category, flag.category),
                "hint": flag.hint,
                "maturity": getattr(flag, "maturity", None) or "stable",
                "value": bool(getattr(settings, flag.attr)),
                "default": default,
            }
        )
    return out


def update_env_flags(updates: dict[str, bool]) -> tuple[list[str], list[str]]:
    """Apply boolean flag updates. Returns (updated_attrs, rejected_attrs).

    Writes the live process env, persists to ``.env`` and clears the settings
    cache so the change is effective for every subsequent request. The LLM
    runtime overlay is preserved.
    """
    from .secrets_store import write_env_updates

    updated: list[str] = []
    rejected: list[str] = []
    env_writes: dict[str, str] = {}

    for attr, raw in updates.items():
        flag = _FLAG_BY_ATTR.get(attr)
        if flag is None or not isinstance(raw, bool):
            rejected.append(attr)
            continue
        value = "true" if raw else "false"
        os.environ[flag.env_key] = value
        env_writes[flag.env_key] = value
        updated.append(attr)

    if updated:
        try:
            write_env_updates(env_writes)
        except OSError as exc:
            # Read-only FS etc. — the live process env still applied.
            logger.warning("env flags: .env persistence failed (%s)", exc)
        get_settings.cache_clear()
        if "neo4j_enabled" in updated:
            try:
                from . import neo4j_kg

                neo4j_kg.close()
            except Exception:
                pass
        logger.info("env flags updated: %s", ", ".join(updated))
    return updated, rejected
