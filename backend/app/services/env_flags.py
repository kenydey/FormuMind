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
    EnvFlag("arxiv_search_enabled", "arXiv 检索",
            "文献检索包含 arXiv 来源。", "retrieval", "需安装 intel extra + 网络"),
    EnvFlag("arxiv_domain_filter", "arXiv 领域过滤",
            "对 arXiv 结果按材料/化学领域分类过滤，减少无关命中。", "retrieval"),
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
            "检索流终态对合并结果做一次 LLM 语义重排（需有效 LLM key；失败时保持原序）。",
            "retrieval", "需有效 LLM key"),
    EnvFlag("auto_kb_refresh_before_recommend", "推荐前刷新 KB",
            "运行配方推荐前先联邦检索并写入 ColBERT 索引。", "kb", "需网络"),
    EnvFlag("fulltext_enrich", "检索全文获取",
            "把排名靠前的专利/OA 文献/网页命中升级为全文分块并持久化入知识库。",
            "kb", "需网络；每次深度研究会下载最多 8 篇全文"),
    EnvFlag("pdf_download", "旧版专利 PDF 下载",
            "深度研究后下载专利 PDF 替换摘要（已被「检索全文获取」取代，保留兼容）。",
            "kb", "需网络"),
    EnvFlag("pdf_ocr", "扫描件 OCR 解析",
            "PDF 解析（MinerU 层）启用 OCR 管线，可读取扫描/图片型 PDF。", "kb",
            "需 magic-pdf OCR 依赖；解析显著变慢"),
    EnvFlag("pdf_formula_enrichment", "公式转 LaTeX",
            "PDF 解析（Docling 层）把显示公式/反应方程式识别为 LaTeX，切块与渲染保真。",
            "kb", "需 docling；首次使用下载公式模型"),
    EnvFlag("kb_ingest_auto", "检索后台自动入库",
            "检索/深度研究/推荐结束后，后台任务逐篇获取全文并构建知识库，前台实时显示每篇状态。",
            "kb", "需网络；受 kb_ingest_max_docs 限制（默认 12 篇/次）"),
    EnvFlag("workbench_auto_train", "台账自动回灌训练",
            "实验台账 Completed 行保存时自动写入 ModelRegistry 并触发重训。", "data"),
    EnvFlag("auto_loop_on_sync", "台账保存后自动闭环",
            "Completed 行回灌训练后，后台触发 optimize + 下一轮 DOE（/api/loop/iterate）。", "data"),
    EnvFlag("kg_enabled", "知识图谱 P0",
            "实体索引 + 枚举型 RAG：牌号/CAS/元素完备召回，增强 Chat/CRAG。", "kb"),
    EnvFlag("kg_link_on_ingest", "入库实体链接",
            "文档切块入库后自动写入 kb_entities / kb_mentions。", "kb"),
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
    EnvFlag("vision_extract_enabled", "图片视觉解析",
            "上传图片经视觉大模型结构化：表格→Markdown、分子结构图→SMILES（RDKit 验证）。",
            "chem", "需具备视觉能力的 LLM（FORMUMIND_VISION_MODEL 可指定专用模型）"),
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
