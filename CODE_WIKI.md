# FormuMind Code Wiki

## 1. 项目概述

FormuMind 是一个面向金属表面处理领域的 **AI辅助配方研发平台**，涵盖防腐蚀涂料、脱脂剂和表面处理剂三大产品线。

### 核心闭环流程

```
需求 → 专利/文献检索 → RAG-grounded研究 → 推荐配方 → DOE计划 → 固化/界面模拟
                                             ↓                                 │
                                        贝叶斯闭环优化 ←─── DOE实验结果（训练数据驱动模型）
```

### 设计理念

项目采用 **Adapter + Fallback** 架构，每个外部引擎都有确定性离线回退方案：
- 无GPU、API密钥或C++工具链也能完整运行
- 安装可选依赖后自动启用真实引擎

---

## 2. 架构层次

| 层级 | 技术栈 | 说明 |
|------|--------|------|
| **Frontend** | Vite + React + TypeScript + Tailwind + Zustand | 三面板深色工业UI |
| **Gateway** | FastAPI | Research/DOE/Optimize/Tasks 路由 |
| **Async** | Celery + Redis | 优化和入库任务，进程内回退 |
| **Domain** | Pure Python | Schema、知识库、DOE引擎、化学计量 |
| **Services** | Adapter + Fallback | LLM、文献、RAG、预测器、优化器、模拟器 |

---

## 3. 目录结构

```
backend/
├── app/
│   ├── api/              # REST API路由
│   ├── agents/           # 专家智能体（化学家、检查员、监督者）
│   ├── db/               # 数据库模型和存储
│   ├── domain/           # 领域模型和业务逻辑
│   ├── middleware/       # 中间件（认证、限流）
│   ├── pipeline/         # 端到端工作流编排
│   ├── resources/        # 静态资源
│   ├── services/         # 核心服务层
│   └── worker/           # Celery异步任务
├── scripts/              # CI和工具脚本
├── tests/                # 测试套件（1010+测试用例）
└── Dockerfile            # 后端Docker镜像

frontend/
├── src/
│   ├── components/       # React组件
│   ├── constants/        # 常量定义
│   ├── hooks/            # 自定义Hooks
│   ├── store/            # Zustand状态管理
│   └── utils/            # 工具函数
└── Dockerfile            # 前端Docker镜像

docs/                     # 用户文档
deploy/                   # 部署配置
scripts/                  # 部署脚本
```

---

## 4. 核心领域模型

### 4.1 产品领域

[schemas.py](file:///workspace/backend/app/domain/schemas.py#L13-L18)

```python
class ProductDomain(str, Enum):
    anticorrosion_coating = "anticorrosion_coating"  # 防腐蚀涂料
    degreaser = "degreaser"                          # 脱脂剂
    surface_treatment = "surface_treatment"          # 表面处理剂
```

### 4.2 需求模型

[schemas.py](file:///workspace/backend/app/domain/schemas.py#L88-L143)

`Requirement` 是用户输入的核心数据结构，包含：
- **domain/substrate**: 产品领域和基材类型
- **目标指标**: salt_spray_hours, film_weight_gsm, cleaning_efficiency
- **约束条件**: voc_limit_gpl, cure_temperature_c, ph_target
- **objectives**: 多目标优化规格（权重、方向、参考范围）
- **levers**: DOE可调因子（成分百分比或工艺参数）
- **materials**: 项目级原材料列表

### 4.3 配方模型

[schemas.py](file:///workspace/backend/app/domain/schemas.py#L175-L188)

`Formulation` 代表一个完整的配方：
- **ingredients**: 成分列表（名称、角色、重量百分比、SMILES、CAS号）
- **predicted**: 预测性能指标（耐盐雾、成本、VOC等）
- **predicted_std**: 预测不确定性
- **score**: 综合评分
- **warnings**: 安全检查警告

### 4.4 DOE相关模型

[schemas.py](file:///workspace/backend/app/domain/schemas.py#L376-L397)

- `DOEFactor`: 实验因子（名称、范围、单位）
- `DOERun`: 单次实验运行（编码值、自然值）
- `DOEPlan`: 完整实验设计方案

---

## 5. 数据库模型

[models.py](file:///workspace/backend/app/db/models.py)

### 核心表结构

| 表名 | 用途 |
|------|------|
| `experiments` | 实验结果存储（DOE/Lab测量值） |
| `campaigns` | AI优化活动记录 |
| `source_documents` | 已入库的源文档（全文+Source Guide） |
| `document_chunks` | 持久化知识库切块（含向量嵌入） |
| `kb_products` | 商业化学产品注册表 |
| `kb_entities` | 知识图谱实体（化学物质、产品、元素） |
| `kb_entity_links` | 实体间关系链接 |
| `projects` | 项目工作空间 |
| `task_outbox` | 异步任务持久化队列 |
| `doe_plans` | DOE计划持久化 |
| `materials` | 可编辑原材料库（种子库的持久化叠加层，见第21章） |
| `measurements` | 逐条实测值（指标、数值、单位、方法、时间戳） |
| `experiment_attachments` | 实验附件（QC报告原件，按内容哈希去重） |
| `formulation_versions` | 配方版本谱系（父子链 + 快照 + 变更摘要） |

### ORM设计特点

- **JSON列存储**：`factors`、`measured` 使用JSON列，支持动态指标扩展
- **PostgreSQL兼容**：JSON类型自动切换为JSONB
- **双向支持**：实验数据可存储在SQLite或Datalab（企业ELN）

### 外键与完整性

SQLite 默认**不**执行外键约束，且该开关是**每连接**生效的——不打开的话
所有 `ForeignKey` 声明都只是装饰。`db/database.py` 用 connect 监听器对每个
新连接执行 `PRAGMA foreign_keys=ON`。

同一个监听器还执行 `PRAGMA journal_mode=WAL`。默认的 `delete`（回滚日志）
模式下**读写互斥**：任何写操作都会对整库加排他锁，所有读者排在后面。而这个
进程本来就是线程共享一个库文件（TaskManager、进程内 worker、请求线程），
于是一次长时间的入库事务会把无关的读请求全堵住——生产环境里的
「database is locked」就是这么来的。WAL 允许任意多个读者与一个写者并发。

按**每连接**设置而非只设一次，是因为该模式存在于**库文件**里，调用方可能指向
另一个进程以 `delete` 模式建的文件（例如 alembic 自己建的 engine）。失败被容忍：
`:memory:` 与网络文件系统不支持 WAL，那不是拒绝启动的理由。
⚠️ 备份要连 `-wal` / `-shm` 一起拷——尚未 checkpoint 的已提交数据在 `-wal` 里。

但并非所有跨表引用都能变成外键：当 `FORMUMIND_CAMPAIGN_BACKEND=datalab`
时，被引用的行存在于外部 ELN 中，本地建约束会拒绝完全合法的数据。对这类
引用的替代方案是**真的去跑一遍检查**（`db/integrity.py` /
`GET /api/kb/integrity`），否则孤儿行会静默累积，第一个症状是检索悄悄少返回
了一些结果。

---

## 6. API路由模块

### 6.1 路由概览

[main.py](file:///workspace/backend/app/main.py#L145-L168)

| 路由 | 用途 |
|------|------|
| `/api/search` | 多源检索（专利/文献/互联网） |
| `/api/ingest` | 文件上传入库 |
| `/api/chat` | RAG-grounded问答 |
| `/api/research` | CRAG-grounded研究+配方推荐 |
| `/api/research/deep` | 异步深度研究 |
| `/api/formulations/recommend` | LLM配方推荐 |
| `/api/doe` | DOE实验设计生成 |
| `/api/optimize` | 多目标闭环优化 |
| `/api/experiments` | 实验结果回灌；`GET` 列出实验供按 id 引用 |
| `/api/tasks/{id}` | 任务进度查询 |
| `/api/models` | 训练模型列表 |
| `/api/chemical/lookup` | 化学成分查询 |
| `/api/intent/parse` | 自然语言需求解析 |
| `/api/design/inverse` | **逆向设计**：给定目标性能反解配方（异步，202） |
| `/api/materials` | 原材料库读写（`GET` 检索 / `POST` 新增） |
| `/api/materials/substitutes` | **材料替代**：给定配方的某个位点求替代品 |
| `/api/materials/supply-risk` | 断供扫描：列出受停产料影响的配方 |
| `/api/materials/availability` | 标记某材料为 `discontinued` / `restricted` |
| `/api/qc/report` | QC报告上传 → LLM抽取 → 落库为实测值 |
| `/api/qc/experiments/{id}/measurements` | 某实验的逐条实测值 |
| `/api/formulations/versions` | 配方版本谱系：保存 / 检索 / 详情 / 差异 |
| `/api/kb/integrity` | 引用完整性巡检（孤儿行报告） |
| `/api/kg/feedback/stats` | KG 实测回流统计（`measured` 计数 + `by_campaign`） |
| `/api/kg/feedback/report` | KG 审计报表（`measured` + 零增长 `alert` + `recent_bias`） |
| `/api/kg/relations/{id}?extraction_method=measured` | 图谱关系按 `extraction_method` 过滤 |
| `/api/research/rag/status` | RAG 后端 + `prewarm` 状态（`idle/warming/ready/failed`） |
| `/api/research/rag/prewarm` | 手动触发 RAG 预热（`background` 幂等） |
| `/api/tasks/{id}` | 任务进度查询（新增 `stage/elapsed_ms/owner_id`） |
| `/api/tasks/{id}/cancel` | 任务取消（`CANCELLED` 终态 + `owner` 403 鉴权） |
| `/api/tasks/{id}/stream` | SSE 进度流（`CANCELLED` 终态 + 鉴权） |
| `/api/experiments/workbench/{id}/bias-trend` | 预测偏差趋势（`loop_history` 聚合 + 阈值告警） |

### 6.2 认证与安全

[middleware/api_auth.py](file:///workspace/backend/app/middleware/api_auth.py)

- API密钥认证（生产环境默认开启）
- 开发/测试环境自动关闭
- 支持通过 `FORMUMIND_API_AUTH_ENABLED` 环境变量配置

---

## 7. 核心服务层

### 7.1 LLM服务

[llm.py](file:///workspace/backend/app/services/llm.py)

支持 **9种LLM供应商**：
| 供应商 | SDK | 特点 |
|--------|-----|------|
| Anthropic | `anthropic` | Claude系列 |
| OpenAI | `openai` | GPT系列 |
| Google Gemini | `google-genai` | Gemini系列 |
| xAI | `openai` (兼容) | Grok |
| Groq | `openai` (兼容) | Meta Llama |
| DeepSeek | `openai` (兼容) | 中文能力强 |
| Qwen | `openai` (兼容) | 通义千问 |
| Moonshot | `openai` (兼容) | Kimi |
| MiniMax | `openai` (兼容) | 多模态 |

**关键函数**：
- `complete_structured()`: 调用LLM并解析为Pydantic模型
- `recommend_formulations()`: 配方推荐核心引擎
- `answer_question()`: 基于检索证据的问答
- `synthesize_research()`: 研究报告合成

### 7.2 预测服务

[predictor.py](file:///workspace/backend/app/services/predictor.py)

**双层预测架构**：

1. **经验代理模型**（离线可用）：基于领域机理的确定性预测
   - 防腐蚀涂料：缓蚀剂含量、树脂/固化剂配比、交联密度
   - 脱脂剂：表面活性剂、碱性助剂、溶剂含量
   - 表面处理剂：活性成分、促进剂、抑制剂

2. **数据驱动模型**（在线学习）：
   - scikit-learn `RandomForestRegressor`（安装时）
   - numpy Ridge回归（默认）
   - 训练样本≥4个时自动启用

**混合策略**：权重 `w = n / (n + 8)`，随数据增长逐渐过渡到模型预测

**计算指标**：
- 性能指标：salt_spray_hours, cleaning_efficiency
- 成本指标：cost_cny_per_kg
- 环保指标：voc_gpl, sustainability_idx
- 涂料特性：pvc_pct, cpvc_pct, solids_by_volume
- 流变学：tg_celsius, viscosity_relative

### 7.3 优化服务

[optimizer.py](file:///workspace/backend/app/services/optimizer.py)

**多级优化引擎**（自动降级）：

| 优先级 | 引擎 | 依赖 | 特点 |
|--------|------|------|------|
| 1 | BoTorch GP-EI | `botorch` + `gpytorch` | 真实高斯过程，Log-EI采集 |
| 2 | Summit SOBO | `summit` | 贝叶斯/TSEMO优化 |
| 3 | Optuna TPE | `optuna` | CPU多目标优化 |
| 4 | numpy UCB | 内置 | 轻量级贝叶斯风格 |

**统一接口**：`suggest()` → `observe()` → `ranked()`

### 7.4 化学工具服务

[chemtools.py](file:///workspace/backend/app/services/chemtools.py)

提供化学能力网关：
- 名称→SMILES/CAS解析
- 官能团识别
- 分子专利预筛（molbloom）
- 管制化学品筛查
- DOE因子审查

### 7.5 RAG服务

[rag.py](file:///workspace/backend/app/services/rag.py)

**检索后端**（`active_rag_backend()` 决定，`build_store()` 构造）：

| 后端 | 触发条件 | 说明 |
|------|----------|------|
| `pylate` / `colbert` | `gpu_enabled=true` 且 CUDA 可用 | GPU 路径 |
| `bm25_faiss` | **CPU 默认** | BM25 稀疏 + FAISS 稠密混合，零 AVX2 要求 |
| `embedding` | 显式指定 `rag_backend` | 纯向量 |
| `tfidf` | 最后兜底 | `rank_bm25` 缺失时 |

`FORMUMIND_RAG_BACKEND` 可显式覆盖自动检测（`auto` 以外的值优先）。

#### 向量模型与"不可比"护栏

向量模型由 `FORMUMIND_EMBEDDING_MODEL` 指定，留空用默认
`sentence-transformers/all-MiniLM-L6-v2`。

⚠️ **默认值是英文为主的模型**，而本平台检索中文专利——这是当前检索质量的一个已知
短板。中文候选：`BAAI/bge-small-zh-v1.5`、`BAAI/bge-m3`、`moka-ai/m3e-base`。

**换模型会让已有向量全部作废**（不同模型的向量在不同语义空间）。这件事被显式护栏
接住，而不是静默降质：

- `comparable_embedding()` 先校验**维度**再校验 `embedding_model` 名，不可比的切块
  退回关键词打分。此前两处打分写的是 `zip(query_vec, c.embedding)`——`zip` 不是
  比较而是**截断到较短者**，384 维查询对 1536 维存量会算出一个看起来正常、排序却
  全错的数，无异常无日志。
- `/api/kb/stats` 的 `vector_mode` 增加 `stale` 态，给出待重建切块数与重建指引。
- 写入端校验向量条数与切块条数一致，不一致直接丢弃整批（错位绑定会让每一行都带着
  邻居的语义，比没有向量更糟）。

换完模型后需在设置页点**「重建索引」**。

### 7.6 资料获取与入库管线

检索只拿到摘要级命中；把它们升级为全文并持久化，是一条独立的管线。
由 `FORMUMIND_FULLTEXT_ENRICH` 与 `FORMUMIND_KB_INGEST_AUTO` 控制。

#### 按来源分渠道下载

`fulltext_fetcher.classify()` 先分类，再由 `_dispatch_fetch()` 分派：

| 渠道 | 取法 | 要点 |
|------|------|------|
| **专利** | Google Patents 落地页 `/patent/{号}/en` | **HTML 正文优先**（`patent_prefer_html`，默认开） |
| **文献** | arXiv `/e-print/` LaTeX 源码；否则 OpenAlex/arXiv PDF | **源码优先**（`arxiv_prefer_source`，默认开） |
| **网页** | httpx + trafilatura | SSRF 白名单，重定向逐跳复检 |

**专利为什么走 HTML 而不是 PDF**（实测结论）：三个直链 PDF 地址全部失效——
`pdfpiw.uspto.gov` 连接被重置、`patents.google.com/patent/{号}/pdf` 返回的是
`text/html` 落地页、EPO publication-server 返回 2.4 KB 错误页。落地页本身就带着
`itemprop="abstract|description|claims"` 全文（实测 CN 18k / US 22k / EP 54k /
JP 157k 字符），一次请求约 0.7 秒，**完全不需要 OCR**，中日文专利还附带英文机器
翻译对照。真实 PDF 地址在落地页的 `<meta name="citation_pdf_url">`，主机被钉死在
`patentimages.storage.googleapis.com` 并仍走 SSRF 检查。

**arXiv 为什么走源码**（实测结论）：同一篇 100 页论文，PDF 路径下载 1.17 s +
解析 **51.7 s**，源码路径下载 0.94 s + 转换 0.3 s。51.7 秒的大头是 RapidOCR 对
图表密集页启动了 OCR（图多的版面在 triage 里像扫描件）。源码另有两个好处：公式以
LaTeX 保留、`\section{}` 直接变成 Markdown 标题供 `chunk_markdown` 的
`heading_path` 使用。`pylatexenc` 会在真实论文上崩（实测 4 篇崩 1 篇），因此
**逐 section 转换**——崩一段只损失一段，且回落到正则清洗。

#### 解析级联

`parsing.parse_document()` 是唯一入口，按 `FORMUMIND_PDF_PARSER` 决定顺序：

```
hybrid(pymupdf4llm，版面感知) → docling → marker → mineru → rapidocr → markitdown → pypdf
```

（非 PDF 走另一条：`markitdown → docx → text`。）

- **RapidOCR**（`rapidocr_enabled`）：扫描件无文字层时本地读字，约 2 s/页，
  模型随 wheel 分发、不联网、不耗配额。
- **MinerU**（`mineru_enabled`，**默认关**）：把本地解析不好的单页（密集表格 /
  公式 / 图表）升级到云端。
  ⚠️ **被升级的页面会上传到 mineru.net（第三方）并消耗配额**，所以必须显式开启，
  需要 Token + `mineru-open-sdk`。
  开启后图表 block 会路由到**视觉模型**（见 §7.1 的文本/视觉角色分离）；没配视觉
  模型时降级但不丢内容。
- 每篇文档的日志会写出实际胜出的层名；MinerU 真正升级过页面时显示
  `parse=…(hybrid+mineru:3)`——否则无法按文档判断配额买到了什么。

#### 入库

`kb_ingest.ingest_evidence_docs()`：**并发下载、串行入库、两者流水线化**。

- 下载并发由 `kb_ingest_workers`（默认 3）控制，这个数是按**解析内存**
  （~350 MB，走 OCR 时 ~557 MB）定的，不是按 socket。
- 入库串行：写 SQLite。
- 用 `as_completed` 而非 `ex.map`——后者按提交顺序消费，一个慢的队首下载会卡住
  它后面的每一篇，且几百篇全文会同时堆在内存里。
- `kb_ingest_max_docs=0` 表示**不限篇数**（「搜到了但没入库」等同数据丢失）。

每篇入库依次经过：切块（`chunk_markdown`）→ 化学实体抽取 → 向量化 → 写库。

#### 计时仪表

`ingest_timing` 给每篇文档打一行、每批打一行：

```
kb_ingest doc EP2757083A1 [patent] indexed 7400ms
  download=650 parse=41(hybrid) chunk=1 entities=6748 embed=0 chars=57610
kb_ingest batch 9 docs in 17.3s  failed=2 indexed=7
  | download=39% entities=61% chunk=0% embed=0%
kb_ingest batch web_chars n=12 p50=4180 p90=21903 thin(<200)=8% http_errors=403:2
```

设计要点：
- **一篇文档跨线程计时**——下载在 worker 池、入库在主线程，普通的
  `threading.local` 会把一篇拆成互不相干的两半。
- `download` = `fetch` 减去嵌套的 `parse`，否则慢解析会被误读成慢网络。
- 网页渠道的 **HTTP 失败与"内容过薄"分开统计**：一堆 403 不该拿来论证一个
  修不了 403 的 JS 渲染层。

这套仪表的结论直接推翻过两个假设：**向量化占 0%**（所以"用远程模型加速向量化"
没有速度可捞），**化学实体抽取占 39–61%**（且随文本的化学密度而非长度增长）。

---

## 8. 工作流编排

[pipeline/workflow.py](file:///workspace/backend/app/pipeline/workflow.py)

### 8.1 研究流程

```python
def run_research(req: Requirement, ...) -> ResearchResult:
    """CRAG研究图：检索 → 推荐 → 机理解释"""
```

调用 `run_research_graph()` 执行完整的CRAG工作流：
1. 构建研究查询
2. 联邦检索（专利+文献+互联网）
3. ColBERT知识库检索
4. LLM配方推荐
5. 机理合成

### 8.2 DOE构建

```python
def build_doe(req: Requirement, design: str = "full_factorial") -> DOEPlan:
    """生成实验设计方案"""
```

支持的设计类型：
- `full_factorial`: 全因子设计
- `fractional_factorial`: 部分因子设计
- `plackett_burman`: Plackett-Burman设计
- `ccd`: 中心复合设计
- `lhs`: 拉丁超立方设计

### 8.3 优化流程

```python
def run_optimization(req: Requirement, iterations: int) -> OptimizationResult:
    """贝叶斯闭环优化"""
```

优化循环：
1. 解析可调因子（levers）
2. 构建优化器（自动选择最佳引擎）
3. 迭代：suggest → predict → observe → rank
4. 返回Top-N配方排行榜

---

## 9. 专家智能体系统

[agents/](file:///workspace/backend/app/agents/)

### 9.1 智能体协议

[base.py](file:///workspace/backend/app/agents/base.py)

```python
@runtime_checkable
class ExpertAgent(Protocol):
    name: str
    
    def inspect(form: Formulation, requirement: Requirement | None) -> AgentFinding:
        ...
```

### 9.2 智能体类型

| 智能体 | 文件 | 职责 |
|--------|------|------|
| **Chemist** | [chemist.py](file:///workspace/backend/app/agents/chemist.py) | 化学兼容性检查（酸碱冲突、交联密度） |
| **Inspector** | [inspector.py](file:///workspace/backend/app/agents/inspector.py) | 合规性检查（REACH SVHC、VOC限制） |
| **Supervisor** | [supervisor.py](file:///workspace/backend/app/agents/supervisor.py) | 汇总专家意见，生成最终裁决 |

### 9.3 裁决流程

```
配方提交 → Supervisor分发 → Chemist检查 → Inspector检查 → Supervisor汇总 → ReviewVerdict
```

---

## 10. 知识图谱

[services/kg/](file:///workspace/backend/app/services/kg/)

### 10.1 实体类型

| 类型 | 说明 |
|------|------|
| `chemical` | 化学物质（SMILES/CAS/分子式） |
| `trade_product` | 商业产品（牌号/供应商/等级） |
| `element` | 元素（元素周期表） |
| `parameter` | 工艺参数 |

### 10.2 核心组件

| 模块 | 职责 |
|------|------|
| `entity_linker.py` | 实体链接（文本→实体） |
| `entity_normalizer.py` | 实体标准化 |
| `entity_resolver.py` | 实体解析（歧义消除） |
| `relation_extractor.py` | 关系抽取 |
| `graph_query.py` | 图查询接口 |
| `element_map.py` | 元素映射表 |

---

## 11. 数据训练与模型管理

[services/training.py](file:///workspace/backend/app/services/training.py)

### 11.1 ModelRegistry

全局单例注册表，管理训练模型：

**自动触发训练条件**：
- 新实验结果提交时（`auto_retrain=True`）
- 每个指标至少有 `min_train_samples`（默认4）个样本

**模型存储策略**：
- 不存储模型二进制文件
- 启动时从持久化实验数据重建
- 按 `(domain, metric, project_id)` 三元组管理

### 11.2 特征工程

[domain/features.py](file:///workspace/backend/app/domain/features.py)

将配方转换为特征向量：
- 角色基成分向量（树脂/固化剂/缓蚀剂等）
- 工艺参数（固化温度）
- RDKit分子描述符（可选，需安装）

---

## 12. 异步任务系统

[worker/](file:///workspace/backend/app/worker/)

### 12.1 Celery配置

[celery_app.py](file:///workspace/backend/app/worker/celery_app.py)

- 支持Redis broker
- **Eager模式**：Redis不可达时自动同步执行
- 任务进度跟踪（SSE推送）

**单任务时限**（`celery_soft_time_limit_s` / `celery_hard_time_limit_s`，
默认 2 小时 / 3 小时）：

必须满足 **软限 < 硬限 < `task_stream_timeout_s`（6 小时）**。
流要比任务活得久，否则前端会停止观察一个仍在运行的任务——那正是此前反复出现的
假「构建中断」。

这两个值曾硬编码为 600 / 900 秒，与其余各层直接矛盾：前端已无墙钟上限、SSE 截止
6 小时、入库不限篇数，然后 Celery 在第 15 分钟把任务杀掉，前端还在盯着一个已经
不存在的任务。几百篇的知识库构建必然超过 15 分钟。

软限触发时 `SoftTimeLimitExceeded` 被捕获并报出**该调哪个环境变量**，已入库的部分
保留、可再次运行继续。

> `soft_time_limit` 是**每个任务**的，与 `celery -c` 并发数无关——并发只决定一个
> 卡死的任务能占住几个 worker 槽位。

### 12.2 任务类型

[tasks.py](file:///workspace/backend/app/worker/tasks.py)

| 任务 | 用途 |
|------|------|
| `deep_research_task` | 深度研究异步执行 |
| `optimize_task` | 优化任务异步执行 |
| `ingest_task` | 文件入库异步执行 |
| `train_task` | 模型训练异步执行 |

### 12.3 任务Outbox模式

[db/outbox_store.py](file:///workspace/backend/app/db/outbox_store.py)

- 持久化任务队列（幂等性保障）
- `(operation, idempotency_key)` 唯一约束
- 自动恢复停滞任务

---

## 13. 化学计量模块

[domain/chemistry.py](file:///workspace/backend/app/domain/chemistry.py)

### 13.1 分子量计算

```python
def molar_mass(formula: str) -> float:
    """解析化学式并计算分子量"""
```

- 优先使用 `ChemFormula` 库
- 回退到内置公式解析器（支持嵌套括号）

### 13.2 涂料关键参数

| 函数 | 计算内容 |
|------|----------|
| `pvc()` | 颜料体积浓度 |
| `cpvc()` | 临界颜料体积浓度 |
| `solids_by_volume()` | 体积固含 |
| `amine_epoxy_ratio()` | 胺环氧当量比 |

### 13.3 安全检查

| 检查项 | 函数 |
|--------|------|
| 酸碱冲突 | `check_acid_base_conflict()` |
| REACH SVHC | `check_svhc()` |
| VOC分类 | `check_voc_category()` |

---

## 14. 配置系统

[config.py](file:///workspace/backend/app/config.py)

### 14.1 Settings类

所有配置通过环境变量驱动，前缀 `FORMUMIND_`：

**关键配置项**：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `environment` | development | 环境类型 |
| `llm_provider` | anthropic | 当前LLM供应商 |
| `redis_url` | redis://localhost:6379/0 | Redis地址 |
| `db_url` | sqlite:///./data/formumind.db | 数据库URL |
| `min_train_samples` | 4 | 最小训练样本数 |
| `optimize_iterations` | 24 | 优化迭代次数 |
| `top_n_formulas` | 5 | Top-N配方数 |
| `api_auth_enabled` | production时开启 | API认证 |

**资料获取与入库**（见 §7.6）：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `fulltext_enrich` | false | 把摘要级命中升级为全文（生产建议开） |
| `kb_ingest_auto` | true | 检索后台自动入库 |
| `kb_ingest_max_docs` | **0** | 入库篇数上限，0 = 不限 |
| `kb_ingest_workers` | 3 | 下载并发；按解析内存定，非 socket |
| `patent_prefer_html` | true | 专利用落地页正文，不下 PDF |
| `arxiv_prefer_source` | true | arXiv 用 LaTeX 源码，不下 PDF |
| `rapidocr_enabled` | true | 本地 OCR（扫描件），不联网不耗配额 |
| `mineru_enabled` | **false** | 云端解析，⚠️ 上传第三方 + 耗配额 |
| `embedding_model` | 空 | 向量模型，空=英文默认；换了要重建索引 |
| `rag_backend` | auto | 检索后端显式覆盖 |

**任务时限**（见 §12.1，必须软 < 硬 < 流）：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `celery_soft_time_limit_s` | 7200 | 软限，任务可自报并保留已入库部分 |
| `celery_hard_time_limit_s` | 10800 | 硬限，防止卡死任务永久占槽 |
| `task_stream_timeout_s` | 21600 | SSE 截止，必须最大 |

### 14.2 动态密钥管理

[runtime_secrets.py](file:///workspace/backend/app/services/runtime_secrets.py)

- 支持运行时覆盖配置
- 通过Settings UI修改的密钥即时生效
- 持久化到 `.env` 文件

---

## 15. 运行方式

### 15.1 本地开发

```bash
# 后端
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload

# 前端
cd frontend
npm install
npm run dev
```

### 15.2 Docker部署

```bash
cp .env.example .env
docker compose up                    # 基础服务
docker compose --profile heavy up    # 含LAMMPS/HTPolyNet
```

### 15.3 测试

```bash
cd backend
pytest -q                           # 快速测试（430+用例）
pytest -m "not golden_eval"         # 跳过黄金评估测试
```

### 15.4 环境变量

复制 `.env.example` 到 `.env`，配置：
- LLM API密钥（可选）
- 数据库连接（可选）
- Redis地址（可选）

---

## 16. 依赖管理

### 16.1 核心依赖

[pyproject.toml](file:///workspace/backend/pyproject.toml)

```python
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.9",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "celery>=5.4",
    "redis>=5.2",
    "numpy>=1.26",
    "httpx>=0.27",
    "tenacity>=8.2.0",
    "loguru>=0.7",
    "rank-bm25>=0.2",
    "jieba>=0.42",
]
```

### 16.2 可选增强依赖

| 额外依赖 | 安装命令 | 功能 |
|----------|----------|------|
| LLM | `pip install -e ".[llm]"` | Claude/OpenAI/Gemini SDK |
| 科学计算 | `pip install -e ".[science]"` | scipy, scikit-learn, RDKit |
| 优化 | `pip install -e ".[optimize]"` | Optuna |
| 贝叶斯优化 | `pip install -e ".[bo]"` | BoTorch |
| 企业优化 | `pip install -e ".[baybe]"` | BayBE约束贝叶斯学习 |
| 文献检索 | `pip install -e ".[intel]"` | patent_client, arxiv, semanticscholar ⚠️ |

> ⚠️ **`.[intel]` 会静默降级四个钉住的依赖**。`patent-client` 要求
> `httpx<0.28` + `pypdf<5.0`，解析结果还会带下 `arxiv` 与 `ddgs`。实测：
>
> | 包 | 钉住 | 装 `.[intel]` 后 | 影响面 |
> |---|---|---|---|
> | `httpx` | 0.28.1 | 0.27.2 | **全代码库** |
> | `pypdf` | 6.14.2 | 4.3.1 | 解析级联最后一层 |
> | `arxiv` | 4.0.0 | 3.0.0 | 文献检索客户端 |
> | `ddgs` | 9.14.4 | 9.14.3 | 互联网检索 |
>
> **pip 不报错，`pip check` 也说「No broken requirements found」**——降级之后环境
> 内部是自洽的，只是不再是 requirements.txt 描述的那一个。唯一可靠的信号是拿实际
> 版本和 pin 对比，这就是 `scripts/check_pins.py` 做的事。
>
> ⚠️ **但不能直接和 pin 比。** `pip install --dry-run --report` **不会**把
> requirements.txt 当约束，于是 pyproject 的 `>=` 下界会解析到 PyPI 上的最新版；
> 这对每个 extra 都一样，跟装了哪个 extra 无关。照这么比，六个 job 会永远报同样
> 四条「升级」（alembic / fastapi / pydantic-settings / redis）——**一个从第一天
> 起就红的门禁等于没有门禁**。
>
> 所以脚本比的是**对照组**：先解析一次不带任何 extra 的同一个工程（`--baseline`），
> 减掉的正好是这类噪声，剩下的才归因于这个 extra，且**两个方向都算**——extra 把
> 基础包往前拽也会失败，这是「和 pin 比」永远看不到的。
> 两次解析都必须带 `--ignore-installed`：否则 `--dry-run` 会略过环境里已满足的包，
> 两份报告被截断的程度不同（实测 1 个 vs 51 个），对照就失效了。脚本自己查不出
> 这一点（截断的 baseline 和小工程无法区分），所以那个 flag 由
> `backend/tests/test_ci_deps_workflow.py` 钉在工作流文件上。
>
> 这不是疏漏而是**已评审的取舍**：`literature.py` 用 `patent_client` 做**在线
> USPTO/EPO 专利检索**，依赖是真实的。所以只在需要那个功能时才装；
> 专利**全文**走 Google Patents 落地页（见 §7.6），完全不需要它。
> `.github/workflows/ci-deps.yml` 把上表四个版本写成允许清单——**变了就会重新报错**，
> 新增的冲突也一样。
| 文件解析 | `pip install -e ".[file_ingest]"` | PDF/DOCX/XLSX解析 |
| 语义检索 | `pip install -e ".[embedding]"` | sentence-transformers |
| ColBERT | `pip install -e ".[colbert]"` | 精排检索 |

---

## 17. 关键设计模式

### 17.1 Adapter + Fallback

每个外部服务都有适配器层和确定性回退：
```python
# 示例：优化器自动选择
def build_optimizer(factors):
    if _botorch_available():
        return BotorchOptimizer(factors)
    if _summit_available():
        return SummitOptimizer(factors)
    if _optuna_available():
        return OptunaOptimizer(factors)
    return BayesianOptimizer(factors)  # 内置回退
```

### 17.2 渐进式学习

实验数据积累后自动从经验模型过渡到数据驱动模型：
- 权重 `w = n / (n + 8)`
- 数据越多，模型权重越大

### 17.3 多智能体审查

分级专家审查系统：
- 化学专家检查兼容性
- 合规专家检查法规要求
- 监督者汇总裁决

### 17.4 持久化知识库

支持增量入库和检索：
- 文档切块存储（含向量嵌入）
- 实体链接和关系抽取
- 跨项目共享语料

---

## 18. 扩展点

### 18.1 添加新指标

1. 在 `predictor.py` 的 `_predict_mechanistic()` 中添加经验预测逻辑
2. 在 `schemas.py` 的 `ObjectiveSpec` 中添加支持
3. 在 `features.py` 中添加特征提取（如需）

### 18.2 添加新DOE设计

1. 在 `services/engines/` 中添加适配器
2. 在 `doe_registry.py` 中注册
3. 在 `build_doe_plan()` 中添加路由

### 18.3 添加新LLM供应商

1. 在 `llm.py` 的 `PROVIDERS` 列表中添加元数据
2. 如果是OpenAI兼容，自动支持；否则添加专用客户端

### 18.4 添加新智能体

1. 实现 `ExpertAgent` 协议
2. 在 `supervisor.py` 中注册
3. 定义检查规则和建议逻辑

---

## 19. 性能特点

### 19.1 离线运行

- 所有核心功能可离线运行
- 无需GPU或外部API密钥
- 内置经验模型和规则引擎

### 19.2 自动降级

- 缺失依赖时自动回退到轻量级方案
- 无Redis时同步执行任务
- 无LLM时使用规则合成

### 19.3 增量训练

- 新实验数据自动触发训练
- 模型按需重建，不存储二进制
- 支持项目级隔离

### 19.4 异步处理

- 耗时任务异步执行（深度研究、优化、入库）
- SSE实时进度推送
- 任务持久化保障

---

## 20. 测试覆盖

### 20.1 测试分类

| 测试类型 | 数量 | 说明 |
|----------|------|------|
| 后端测试 | 1010+ | 核心功能测试 |
| 前端测试 | 106 | vitest + @testing-library/react |
| 集成测试 | 多模块交互 | API/数据库集成 |
| Golden评估 | 黄金数据集 | QA质量评估（较慢） |

### 20.2 测试命令

```bash
pytest -q                           # 快速测试
pytest -m "not golden_eval"         # 跳过黄金评估
pytest tests/test_api.py            # 单文件测试
pytest --timeout=60                 # 设置超时
```

### 20.3 CI/CD

`.github/workflows/ci.yml`，push 与 pull request 均触发：

| Job | 内容 |
|---|---|
| `backend` | Python 3.11（对齐 `backend/Dockerfile`）→ `pip install -r requirements.txt` + `-e '.[dev]'` → `pytest -m "not golden_eval"` |
| `frontend` | Node 22 → `npm ci` → `tsc --noEmit` → `vitest run` → `npm run build` |

- 黄金评估（`golden_eval`）耗时较长，CI 中跳过，本地按需 `pytest -m golden_eval`。
- **`requirements.txt` 是手工维护的**（注释说明了每条 pin 的理由），也是 Dockerfile 的安装来源；
  **不要用 `pip freeze` 重新生成**，那会冲掉注释并钉死当前环境的全部传递依赖。
- 支持 Docker 构建和部署。

---

## 21. 逆向设计子系统

这是继知识库之后规模最大的一块新增能力。它解决的是一个**结构性**问题，而不是
补三个独立功能，所以值得先讲清楚原来卡在哪里。

### 21.1 原来的瓶颈：拓扑锁死

`reconstruct.formulation_from_factors` 只能对一份**硬编码的基线模板**按 wt%
缩放。也就是说，配方里**有哪些料**是常量，只有**各占多少**是变量。

这一条限制同时解释了三个看起来无关的现象：

| 表面症状 | 实际原因 |
|---|---|
| 没有逆向设计 | 搜索空间里根本没有"选料"这个维度 |
| 不能做材料替代 | 换料就是换拓扑，而拓扑不可变 |
| 帕累托前沿只是展示 | 候选集本就来自同一个模板，前沿上没有真正的多样性 |

所以先解锁拓扑，上面三件事才在同一个地基上变得可做。

### 21.2 地基：材料空间 + 基因组

**`domain/material_catalog.py` — `MaterialCatalog`**

`RAW_MATERIALS` 原本是一个模块级 dict 字面量，全仓有几十处直接读它。为了让
材料库可编辑又不改动所有调用点，`MaterialCatalog` 实现了 `MutableMapping`：
对读者来说它仍然是个 dict，实际内容是**种子库 + 数据库叠加层**的合并快照，
按 `store.generation` 失效缓存。

⚠️ 合并时**必须逐条 `dict(spec)` 复制**。直接引用种子 spec 会让数据库里的值
写进模块字面量——测试之间会互相污染，且污染只在特定执行顺序下出现。

**`domain/genome.py` — `FormulationGenome`**

配方的可搜索表示：一组 `Slot(role, material, weight_pct, unit, locked)`。

- `swappable()` 返回可换料的位点，是**搜索**约束（刻意排除颜料/填料）
- `candidates_for_role()` 按角色 + 载体相容性（水性/溶剂型）召回候选
- `Slot.from_wt_pct` / `to_wt_pct` 必须成对：两者不对称会产生 10× 的单位错误，
  而只断言总和的往返测试**看不出来**

### 21.3 搜索：NSGA-II（`services/inverse_design.py`）

选型依据是三条实测约束，不是偏好：

1. **环境里只有 numpy/pandas** — `baybe / optuna / botorch / torch / rdkit /
   scipy / sklearn` 全部未安装。纯 numpy 实现必须是主力，不是兜底。
2. **仓库里原本没有多目标搜索** — 四个优化器全是单目标标量，多目标一律被
   `multi_objective_score` 加权标量化掉。
3. **评估足够快** — 单候选约 1.1 ms（≈900 次/秒），种群 60 × 40 代 ≈ 2.7 秒。
   预测器本身就是代理模型，不需要再套一层 GP。

**约束支配（constraint-domination）**：可行解永远优于不可行解；两个都不可行时
比违反量总和；都可行时比非支配等级，同级比拥挤度。这是 NSGA-II 处理约束的
标准做法，**避免了任意的惩罚权重**。

**硬约束 vs 软目标**的区分是这一版才有的语义。此前两者混在
`ObjectiveSpec.weight` 里只当评分权重，从不作为搜索必须满足的条件。

⚠️ 两个只有**真跑一遍**才会暴露、任何单元测试都抓不到的问题：

- **搜索会薅代理模型的羊毛**：不加界限时它找出了 5110 小时耐盐雾的"配方"。
  修法是用 DOE lever 的取值范围给每个位点定界（`_slot_bounds`）。
- **种群会塌缩到单一成分集**：拥挤度只作用于**目标空间**，成分完全不同但性能
  相近的个体会被判为"拥挤"而淘汰。加了按拓扑分桶的小生境
  （`_select(per_topology=...)`）之后从 1 个成分集变成 12 个。

### 21.4 材料替代（`services/substitution.py`）

三路信号融合：

1. **结构相似** — `substitute_group` 精确命中最高分，`functional_class` 次之，
   Hansen 距离 `Ra = sqrt(4Δd² + Δp² + Δh²)`，RDKit 可用时再加 Tanimoto
2. **预测性能偏离** — 换料后重建基因组重新预测，输出**每个指标的 Δ**
3. **文献证据** — 知识图谱 `substitutes` 边，附 `{source_id, chunk_id, sentence}`

**必须如实标注的限制**：本环境无 RDKit，`_molecular_features` 返回 `{}`，同角色
换料的性能差异只能通过用量、当量比和查表价格产生。实测换三种环氧硬化剂，
`salt_spray_hours` 三者**完全相同**，只有成本有区分度。所以报告里带
`delta_confidence` 字段（如 `cost_only`），不能让用户误以为性能预测有分辨力。
装上 RDKit 并开启描述符特征后此项才完整。

**召回范围**用 `role + substitute_group`，**不**用 `genome.swappable()`——后者是
搜索约束（排除了颜料/填料），而替代是用户指定的，任何位点都应可查。

### 21.5 顺带修掉的两个隐藏缺陷

- **`kg/graph_query.py` 的 synergizes 死代码比"无用"更糟**：
  `get_links_for_entity` 是 `order_by(confidence.desc()).limit(20)`，
  **SQL LIMIT 先于 Python 的 link_type 过滤**执行。高置信度的 synergy 边会占满
  top-20 名额，把真正的 `substitutes` 边挤出去——一个有 20 条强 synergy 边的
  节点会返回**零个** 2-hop 候选。
- **`chemtools._cached` 把 SMILES 缓存键小写化**：`c1ccccc1`（苯，芳香）与
  `C1CCCCC1`（环己烷，脂环）折叠成同一个键。现在因 RDKit 缺失、`None` 不入缓存
  而处于休眠状态，**装上 RDKit 会立即触发**。

---

## 22. 实测数据与配方谱系

### 22.1 逐条实测值

`measured` 原本是实验行上的一个 JSON 字典，没有单位、方法和时间戳。新增的
`measurements` 表把每个观测拆成一行。`ExperimentRecord.measured` 保留为
`computed_field`，由 `measurements` 派生，因此**所有既有读者不受影响**；
`model_validator(mode="before")` 负责把历史 payload 里的 `measured` 提升成
`Measurement` 列表。

### 22.2 QC报告入库

`POST /api/qc/report` 一条链路：上传原件 → LLM 抽取（`services/qc_report.py`）
→ 落库（`services/qc_ingest.py`）。

`ingest_qc_report_tx` 在**单个事务**里完成，按内容哈希去重，且**先挂附件再写
实测值**——顺序反了的话中途失败会留下一批无出处的数值。

### 22.3 配方版本谱系

`services/formulation_history.py` 提供 `save_version` / `lineage` / `ancestry` /
`diff_versions` / `find_lineages`。`diff_snapshots` 产出结构化差异
（`added` / `removed` / `adjusted` 三类成分变化 + 重命名），`describe_diff` 在
作者没写备注时兜底生成一行摘要，形如
"移除 1 项（聚酰胺固化剂）；新增 1 项（异佛尔酮二胺）；调整 2 项（环氧树脂、二甲苯）"。

---

## 附录：关键文件索引

| 文件/目录 | 路径 | 说明 |
|-----------|------|------|
| 入口 | [main.py](file:///workspace/backend/app/main.py) | FastAPI应用入口 |
| 配置 | [config.py](file:///workspace/backend/app/config.py) | 环境配置 |
| 领域模型 | [domain/schemas.py](file:///workspace/backend/app/domain/schemas.py) | Pydantic模式定义 |
| 化学计量 | [domain/chemistry.py](file:///workspace/backend/app/domain/chemistry.py) | 化学式解析、涂料参数 |
| 知识库 | [domain/knowledge.py](file:///workspace/backend/app/domain/knowledge.py) | 原材料库、机理库 |
| 材料空间 | [domain/material_catalog.py](file:///workspace/backend/app/domain/material_catalog.py) | 种子库+数据库叠加的可编辑材料表 |
| 配方基因组 | [domain/genome.py](file:///workspace/backend/app/domain/genome.py) | 配方的可搜索表示（解锁选料维度） |
| 逆向设计 | [services/inverse_design.py](file:///workspace/backend/app/services/inverse_design.py) | NSGA-II 多目标反解 |
| 材料替代 | [services/substitution.py](file:///workspace/backend/app/services/substitution.py) | 三路信号融合的替代品排序 |
| 配方谱系 | [services/formulation_history.py](file:///workspace/backend/app/services/formulation_history.py) | 版本快照、差异与血缘 |
| 完整性巡检 | [db/integrity.py](file:///workspace/backend/app/db/integrity.py) | 软引用孤儿行检测 |
| 工作流 | [pipeline/workflow.py](file:///workspace/backend/app/pipeline/workflow.py) | 端到端编排 |
| LLM服务 | [services/llm.py](file:///workspace/backend/app/services/llm.py) | 多供应商LLM调用 |
| 预测服务 | [services/predictor.py](file:///workspace/backend/app/services/predictor.py) | 性能预测 |
| 优化服务 | [services/optimizer.py](file:///workspace/backend/app/services/optimizer.py) | 贝叶斯优化 |
| 训练服务 | [services/training.py](file:///workspace/backend/app/services/training.py) | 模型训练 |
| 数据库模型 | [db/models.py](file:///workspace/backend/app/db/models.py) | SQLAlchemy ORM |
| 智能体 | [agents/](file:///workspace/backend/app/agents/) | 专家智能体系统 |
| API路由 | [api/](file:///workspace/backend/app/api/) | REST API端点 |
| 异步任务 | [worker/](file:///workspace/backend/app/worker/) | Celery任务 |