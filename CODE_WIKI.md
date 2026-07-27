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
├── tests/                # 测试套件（430+测试用例）
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

### ORM设计特点

- **JSON列存储**：`factors`、`measured` 使用JSON列，支持动态指标扩展
- **PostgreSQL兼容**：JSON类型自动切换为JSONB
- **双向支持**：实验数据可存储在SQLite或Datalab（企业ELN）

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
| `/api/experiments` | 实验结果回灌 |
| `/api/tasks/{id}` | 任务进度查询 |
| `/api/models` | 训练模型列表 |
| `/api/chemical/lookup` | 化学成分查询 |
| `/api/intent/parse` | 自然语言需求解析 |

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

**三层检索架构**：

| 层级 | 引擎 | 触发条件 |
|------|------|----------|
| 1 | ColBERT | 安装 `ragatouille` |
| 2 | Sentence Transformers | 安装 `sentence-transformers` |
| 3 | TF-IDF | 默认回退 |

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
| 文献检索 | `pip install -e ".[intel]"` | patent_client, arxiv, semanticscholar |
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
| 单元测试 | 430+ | 核心功能测试 |
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

- GitHub Actions自动运行测试
- 黄金评估标记为可选（耗时较长）
- 支持Docker构建和部署

---

## 附录：关键文件索引

| 文件/目录 | 路径 | 说明 |
|-----------|------|------|
| 入口 | [main.py](file:///workspace/backend/app/main.py) | FastAPI应用入口 |
| 配置 | [config.py](file:///workspace/backend/app/config.py) | 环境配置 |
| 领域模型 | [domain/schemas.py](file:///workspace/backend/app/domain/schemas.py) | Pydantic模式定义 |
| 化学计量 | [domain/chemistry.py](file:///workspace/backend/app/domain/chemistry.py) | 化学式解析、涂料参数 |
| 知识库 | [domain/knowledge.py](file:///workspace/backend/app/domain/knowledge.py) | 原材料库、机理库 |
| 工作流 | [pipeline/workflow.py](file:///workspace/backend/app/pipeline/workflow.py) | 端到端编排 |
| LLM服务 | [services/llm.py](file:///workspace/backend/app/services/llm.py) | 多供应商LLM调用 |
| 预测服务 | [services/predictor.py](file:///workspace/backend/app/services/predictor.py) | 性能预测 |
| 优化服务 | [services/optimizer.py](file:///workspace/backend/app/services/optimizer.py) | 贝叶斯优化 |
| 训练服务 | [services/training.py](file:///workspace/backend/app/services/training.py) | 模型训练 |
| 数据库模型 | [db/models.py](file:///workspace/backend/app/db/models.py) | SQLAlchemy ORM |
| 智能体 | [agents/](file:///workspace/backend/app/agents/) | 专家智能体系统 |
| API路由 | [api/](file:///workspace/backend/app/api/) | REST API端点 |
| 异步任务 | [worker/](file:///workspace/backend/app/worker/) | Celery任务 |