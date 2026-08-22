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
    EnvFlag("arxiv_search_enabled", "arXiv 检索",
            "文献检索包含 arXiv 来源。", "retrieval", "需安装 intel extra + 网络"),
    EnvFlag("arxiv_domain_filter", "arXiv 领域过滤",
            "对 arXiv 结果按材料/化学领域分类过滤，减少无关命中。", "retrieval"),
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
    # ── 知识库 ────────────────────────────────────────────────────────────
    EnvFlag("content_filter_enabled", "检索质量过滤",
            "规则层过滤：垃圾域名黑名单、空洞摘要、SimHash 近重复合并。", "kb"),
    EnvFlag("content_filter_llm_judge", "LLM 质量判定",
            "检索结束后对最终榜单做一次 LLM 批量质量审查（每次检索一次调用）。",
            "kb", "需有效 LLM key"),
    EnvFlag("search_rerank_enabled", "检索 LLM 精排",
            "对合并结果前若干条做 LLM 语义重排，其余按规则排序保留（总量上限 300）。",
            "retrieval", "需有效 LLM key"),
    EnvFlag("fulltext_enrich", "检索全文获取",
            "把排名靠前的专利/OA 文献/网页命中升级为全文分块并持久化入知识库。",
            "kb", "需网络；每次深度研究会下载最多 8 篇全文"),
    EnvFlag("patent_prefer_html", "专利用落地页正文",
            "专利全文取 Google Patents 落地页的 abstract/description/claims，而不是先下 PDF。"
            "一次请求约 0.7 秒且完全不需要 OCR；中日文专利还附带英文机器翻译对照。"
            "关闭则优先下 PDF（能拿到图表原件，但慢得多，扫描件还要 OCR）。",
            "kb", "需网络；关闭后扫描版专利每页约 2 秒 OCR"),
    EnvFlag("arxiv_prefer_source", "arXiv 用 LaTeX 源码",
            "arXiv 文献下载 LaTeX 源码而不是 PDF：实测同一篇 100 页论文 53 秒 → 1.2 秒，"
            "公式以 LaTeX 保留、章节结构直接变成标题。无源码的投稿自动回落到 PDF。",
            "kb", "需网络"),
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
    EnvFlag("workbench_auto_train", "台账自动回灌训练",
            "实验台账 Completed 行保存时自动写入 ModelRegistry 并触发重训。", "data"),
    EnvFlag("auto_loop_on_sync", "台账保存后自动闭环",
            "Completed 行回灌训练后，后台触发 optimize + 下一轮 DOE（/api/loop/iterate）。", "data"),
    EnvFlag("kg_enabled", "知识图谱 P0",
            "实体索引 + 枚举型 RAG：牌号/CAS/元素完备召回，增强 Chat/CRAG。", "kb"),
    EnvFlag("kg_entities_on_ingest", "入库实体提及",
            "文档切块入库后自动写入 kb_entities / kb_mentions（快，枚举 RAG 根基）。", "kb"),
    EnvFlag("kg_relations_on_ingest", "入库关系提取",
            "入库时抽取实体语义关系写入 kb_entity_links（慢，LLM 关系建议后置）。", "kb"),
    EnvFlag("kg_relation_extract_enabled", "KG 语义关系抽取",
            "link_source 时从 chunk 文本抽取 substitutes/synergizes 等语义关系。", "kb"),
    EnvFlag("kg_llm_relation_extract", "KG LLM 关系抽取",
            "规则未命中时用 LLM 补充关系抽取（增加成本）。", "kb", "需有效 LLM key"),
    EnvFlag("kg_multimodal_fusion_enabled", "多模态图谱融合",
            "专利对比表格图片 → 配方/性能结构化边写入 kb_entity_links。", "kb",
            "需 vision_extract + kg_enabled + LLM key"),
    EnvFlag("chat_multi_turn_enabled", "Chat 多轮上下文",
            "根据对话 history 改写检索 query，支持指代追问。", "kb"),
    EnvFlag("chat_structured_enabled", "Chat 结构化输出",
            "response_format=structured 时返回 JSON 结构化答案。", "kb"),
    EnvFlag("chat_clarification_enabled", "Chat 软澄清",
            "歧义术语时返回答案 + clarification 选项（非阻塞）。", "kb"),
    EnvFlag("chat_claim_check_enabled", "Chat 论断溯源",
            "对答案 claim 做 chunk 级核验（增加延迟）。", "kb"),
    EnvFlag("recommend_diversity_enabled", "推荐配方多样性",
            "Top-N 推荐用 MMR 降低成分高度相似的重复方案。", "data"),
    EnvFlag("recommend_tradeoff_enabled", "推荐 Trade-off 分析",
            "返回 Pareto 前沿、对比表与场景推荐。", "data"),
    EnvFlag("loop_convergence_enabled", "闭环 RMSE 收敛判停",
            "RMSE 连续多轮变化低于阈值时跳过寻优/DOE 并提示停止迭代。", "data"),
    EnvFlag("kb_v2_enabled", "持久知识库 v2",
            "导入/检索的文档结构感知切块入库，问答与推荐检索覆盖全部累计语料。", "kb"),
    EnvFlag("source_guide_enabled", "导入文档 LLM 摘要",
            "上传/导入文档时用 LLM 提取全局参数空间与摘要（Source Guide）。",
            "kb", "需有效 LLM key"),
    # ── 化学引擎 ──────────────────────────────────────────────────────────
    EnvFlag("use_chemcrow", "ChemCrow 智能体问答",
            "化学类问题路由到 ChemCrow 智能体回答。", "chem",
            "需 intel extra + OpenAI 兼容 key"),
    EnvFlag("chemtools_enabled", "ChemCrow 工具网关",
            "工具级化学能力：名称→SMILES/CAS、官能团、分子专利预筛、管制/爆炸性筛查。",
            "chem", "需 chemcrow/rdkit；缺库时自动降级"),
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
    EnvFlag("decimer_enabled", "DECIMER 离线结构识别",
            "化学结构图优先用 DECIMER 离线识别为 SMILES（免 token，省费用），失败自动回退视觉 LLM。"
            "独立 worker 进程（tensorflow-cpu），不占主服务内存。"
            "cpu 模式纯识别；gpu 模式含 decimer-segmentation 结构切分（预留）。",
            "chem", "需独立 decimer worker venv + Celery worker；冷启动约 2 分钟"),
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
            "使用 datalab 后端时，ELN 不可达则硬失败（而非降级 SQLite）。", "data",
            "仅在 campaign/experiment 后端为 datalab 时有意义"),
    # ── 基础设施 ──────────────────────────────────────────────────────────
    EnvFlag("celery_eager", "任务同步执行",
            "后台任务在进程内同步执行（无需 Redis/Celery worker）。关闭需要可达的 Redis。",
            "infra", "关闭前请确认 Redis 与 worker 已就绪"),
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
        logger.info("env flags updated: %s", ", ".join(updated))
    return updated, rejected
