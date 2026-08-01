# GPU/CPU 自适应 RAG 检索方案 — 实施方案 v2

> **目标**：增加 `FORMUMIND_GPU_ENABLED` 和 `FORMUMIND_FORMULATION_MODE` 两个环境变量，运行时自动选择检索后端（ColBERT GPU / BM25+FAISS CPU），并控制配方推荐的生成策略（LLM+KB 叠加 / LLM-only / KB-only）。

---

## 1. 架构变更总览

```
用户配方推荐请求
         │
         ▼
  FORMUMIND_FORMULATION_MODE
    ┌────────┼────────┐
    │        │        │
  hybrid  llm_only  kb_only
    │        │        │
    ▼        ▼        ▼
  KB检索   跳过KB    KB检索
    │        │        │
    ▼        │        ▼
  LLM合成  LLM生成  返回原始Evidence
  (叠加)   (纯LLM)   (无LLM)
    │        │        │
    └────────┴────────┘
         │
         ▼
      推荐配方
```

### 检索后端（KB 层）— 由 `FORMUMIND_GPU_ENABLED` 控制

```
      FORMUMIND_GPU_ENABLED
        /              \
     true              false
      |                 |
PyLate + ColBERT    BM25 + FAISS
(GPU >= 4GB VRAM)   (CPU, 任何 x86_64)
      |                 |
colbert_store.py    rag.py::BM25FAISSStore
```

### 生成模式（LLM 层）— 由 `FORMUMIND_FORMULATION_MODE` 控制

| 模式 | KB 检索 | LLM 生成 | 说明 |
|------|---------|---------|------|
| `hybrid`（默认） | ✅ | ✅ | **叠加模式**：KB 证据注入 LLM prompt，LLM 综合训练知识 + 检索证据生成配方 |
| `llm_only` | ❌ | ✅ | 纯 LLM 推荐（当前 workaround），保留作为快速/离线后备 |
| `kb_only` | ✅ | ❌ | 仅 KB 检索，返回原始 Evidence 列表，用于 LLM 不可用时的降级方案 |

> **核心设计原则：叠加而非互斥** — `hybrid` 模式下，LLM 的化学知识（训练数据）和 KB 的领域证据（文献/专利）**同时起作用**，LLM 将检索到的证据作为 grounding context 融入推荐，而非二选一。

---

## 2. 配置项定义

### 2.1 `backend/app/config.py`

```python
# ═══ RAG 检索 + 配方推荐配置 ═══

# GPU 加速检索开关
# True → PyLate + ColBERT（需 CUDA GPU ≥ 4GB VRAM）
# False → BM25 + FAISS 混合检索（纯 CPU，零 AVX2 要求）
gpu_enabled: bool = Field(default=False, description="Enable GPU-accelerated ColBERT retrieval")

# 配方推荐生成模式
# "hybrid"   → KB 证据 + LLM 合成（叠加，推荐）
# "llm_only" → 纯 LLM 推荐（快速，离线）
# "kb_only"  → 仅 KB 检索（无 LLM，降级）
formulation_mode: str = Field(default="hybrid", description="Formulation recommendation mode")

# 检索后端显式覆盖（覆盖 gpu_enabled 自动检测）
# "auto" | "pylate" | "bm25_faiss" | "tfidf"
rag_backend: str = Field(default="auto", description="RAG retrieval backend")
```

**环境变量映射**：
```bash
FORMUMIND_GPU_ENABLED=true|false
FORMUMIND_FORMULATION_MODE=hybrid|llm_only|kb_only
FORMUMIND_RAG_BACKEND=auto|pylate|bm25_faiss|tfidf
```

### 2.2 前端设置面板

```
┌─ 检索与推荐设置 ─────────────────────────────┐
│                                                │
│  [GPU 加速检索]  [OFF] ← toggle                │
│    开启后使用 PyLate + ColBERT                  │
│    （需 CUDA GPU ≥ 4GB VRAM）                   │
│    关闭时使用 BM25 + FAISS 混合检索             │
│                                                │
│  配方推荐模式  [hybrid ▼]                       │
│    · hybrid   — KB 证据 + LLM 合成（叠加推荐）  │
│    · llm_only — 纯 LLM 推荐（快速离线）         │
│    · kb_only  — 仅 KB 检索（LLM 降级时）        │
│                                                │
│  检索后端  [auto ▼]                             │
│    · auto        — 自动检测                     │
│    · pylate      — 强制 PyLate ColBERT          │
│    · bm25_faiss  — 强制 BM25 + FAISS           │
│    · tfidf       — 强制 TF-IDF                 │
│                                                │
└────────────────────────────────────────────────┘
```

---

## 3. 实现细节

### 3.1 推荐配方流程重构（`formulations.py`）

```python
def recommend_formulations(body: RecommendFormulationsRequest):
    settings = get_settings()
    mode = settings.formulation_mode
    backend = active_backend(settings)
    
    evidence: list[Evidence] = []
    
    # ── Step 1: KB 检索（hybrid / kb_only 模式） ──
    if mode in ("hybrid", "kb_only"):
        try:
            grounded = resolve_grounded_evidence(req, query,
                pre_index=body.sources or None)
            evidence = grounded.grounded_evidence
        except Exception:
            if mode == "kb_only":
                raise  # kb_only 模式下检索失败则报错
            log.warning("KB retrieval failed, falling back to LLM-only")
            evidence = []
    
    # ── Step 2: LLM 合成（hybrid / llm_only 模式） ──
    if mode in ("hybrid", "llm_only"):
        rec_resp = llm.recommend_formulations(req, objectives, evidence, n=llm_n)
    else:
        # kb_only: 不做 LLM 合成，直接返回 Evidence 作为配方
        rec_resp = _evidence_as_recommendations(evidence, req, objectives)
```

### 3.2 `BM25FAISSStore`（`rag.py` 新增）

```python
class BM25FAISSStore:
    """BM25 + FAISS hybrid retriever — zero-AVX2, any x86_64 CPU.
    
    Hybrid scoring: BM25 (60%) + FAISS cosine similarity (40%).
    Uses jieba for Chinese tokenization, whitespace for English.
    """
    backend: str = "bm25_faiss"
    
    def __init__(self):
        self._docs: list[Evidence] = []
        self._corpus: list[str] = []
        self._bm25 = None
        self._faiss_index = None
    
    def ingest(self, evidence: list[Evidence]) -> None:
        """Tokenize and index documents."""
        from rank_bm25 import BM25Okapi
        import jieba
        
        self._docs = list(evidence)
        self._corpus = [_tokenize(_doc_text(ev)) for ev in evidence]
        self._bm25 = BM25Okapi(self._corpus)
        # FAISS flat index over sentence-transformers or TF-IDF vectors
        ...
    
    def query(self, text: str, k: int = 5) -> list[Evidence]:
        """BM25 (60%) + FAISS (40%) hybrid scoring."""
        tokens = _tokenize(text)
        bm25_scores = self._bm25.get_scores(tokens)
        ...
```

### 3.3 `active_backend()` 修改（`colbert_store.py`）

```python
def colbert_available_gpu(settings) -> bool:
    """PyLate + CUDA torch available?"""
    if not settings.gpu_enabled:
        return False
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        import pylate
        return True
    except ImportError:
        return False

def active_backend(settings) -> str:
    if settings.rag_backend not in ("auto", ""):
        return settings.rag_backend          # 手动覆盖优先
    
    if settings.gpu_enabled and colbert_available_gpu(settings):
        return "pylate"                       # GPU: PyLate ColBERT
    
    return "bm25_faiss"                       # CPU: BM25 + FAISS
```

### 3.4 新增 API 端点（`research.py`）

```python
@router.get("/research/rag/status")
def rag_status() -> dict:
    """当前 RAG 后端 + 推荐模式状态。"""
    s = get_settings()
    return {
        "backend": active_backend(s),
        "formulation_mode": s.formulation_mode,
        "gpu_enabled": s.gpu_enabled,
        "gpu_available": colbert_available_gpu(s),
    }
```

---

## 4. 依赖管理

### `pyproject.toml`

```toml
[project.optional-dependencies]
# GPU ColBERT（仅 GPU 环境安装）
colbert = ["ragatouille>=0.0.8", "torch>=2.2"]

# CPU 轻量检索（零新增依赖，rank_bm25 + faiss-cpu 已在 heavy 中）
rag-cpu = []
```

### Docker — CPU 部署镜像

```dockerfile
# 不装 pylate，自动走 BM25+FAISS
RUN uv pip install -e ".[...,rag-cpu]"
```

### Docker — GPU 部署镜像（可选）

```dockerfile
# 安装 CUDA torch + pylate
RUN uv pip install -e ".[...,colbert]" && pip install pylate
```

---

## 5. 决策矩阵

| 环境 | `GPU_ENABLED` | `FORMULATION_MODE` | 检索后端 | 生成方式 | 质量 |
|------|:---:|:---:|---|---|---|
| **当前 VPS**（Xeon E5 v2） | `false` | `hybrid` | BM25+FAISS | LLM + KB | ⭐⭐⭐ |
| **当前 VPS** — 快速预览 | `false` | `llm_only` | 跳过 | 纯 LLM | ⭐⭐ |
| **当前 VPS** — LLM 故障 | `false` | `kb_only` | BM25+FAISS | 仅 KB | ⭐⭐ |
| **GPU VPS**（A10/L4） | `true` | `hybrid` | PyLate ColBERT | LLM + KB | ⭐⭐⭐⭐ |
| **GPU VPS** — 快速预览 | `true` | `llm_only` | 跳过 | 纯 LLM | ⭐⭐ |
| **本地工作站**（RTX 4090） | `true` | `hybrid` | PyLate ColBERT | LLM + KB | ⭐⭐⭐⭐ |

---

## 6. 文件变更清单

| # | 文件 | 变更 | 行数 |
|---|------|------|------|
| 1 | `config.py` | 新增 `gpu_enabled`, `formulation_mode` | +8 |
| 2 | `rag.py` | 新增 `BM25FAISSStore` 类 | +80 |
| 3 | `rag.py` | 修改 `build_store()` 分支 | +5 |
| 4 | `colbert_store.py` | 新增 `colbert_available_gpu()` | +12 |
| 5 | `colbert_store.py` | 重写 `active_backend()` | ~20 改 |
| 6 | `formulations.py` | 重构 `recommend_formulations()` 流程 | ~30 改 |
| 7 | `research.py` | 新增 `GET /api/research/rag/status` | +10 |
| 8 | `pyproject.toml` | `rag-cpu` extra | +2 |
| 9 | `tests/test_rag.py` | 新增 BM25FAISSStore + active_backend 测试 | +40 |
| 10 | `tests/test_fix_api.py` | 新增 formulation_mode 测试 | +25 |

**总计**：~230 行新增/修改，零破坏性变更。

---

## 7. 实施步骤（预计 4-5h）

| # | 步骤 | 时间 |
|---|------|------|
| 1 | `config.py`：新增 `gpu_enabled` + `formulation_mode` 字段 | 10min |
| 2 | `rag.py`：实现 `BM25FAISSStore`（ingest + query + jieba 分词） | 1h |
| 3 | `colbert_store.py`：新增 `colbert_available_gpu()`，重写 `active_backend()` | 30min |
| 4 | `rag.py`：修改 `build_store()` 支持 `bm25_faiss` 分支 | 10min |
| 5 | `formulations.py`：重构为 mode-aware 流程（hybrid/llm_only/kb_only） | 30min |
| 6 | `research.py`：新增 `GET /api/research/rag/status` | 10min |
| 7 | 测试：`test_rag.py` + `test_fix_api.py` + 全量回归 | 45min |
| 8 | 前端设置面板：GPU toggle + mode dropdown | 30min |
| 9 | Docker 部署 + 端到端测试 | 30min |

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `rank_bm25` 中文分词弱 | jieba 分词 + BM25 tokenizer（`jieba` 已有依赖） |
| `hybrid` 模式下 LLM 忽略 KB 证据 | prompt 中显式标注证据来源 + 强制引用 |
| `kb_only` 返回的 Evidence 不够结构化 | 定义 `EvidenceAsFormula` adapter |
| PyLate 未发布到 PyPI | GPU 模式兜底 BM25+FAISS，不阻塞启动 |
| FAISS 全量重建开销 | 增量索引 `faiss.IndexIDMap` |

---

**是否批准此 v2 方案并开始实施？**
