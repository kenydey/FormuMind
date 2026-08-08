# KB Ingest 故障修复计划

> **For Hermes:** 逐项实施修复，每项完成后验证。

**Goal:** 修复 KB ingest 的三个卡点：SQLite 锁、ColBERT embedding、langchain `add_texts` API 不兼容。

**Architecture:** 三处独立修复，无需级联依赖，可并行实施。

**Root Cause:** 日志分析确认三项根因。

---

## 根因分析

### 🔴 问题 1：`fulltext persistence failed: database is locked`

| 现象 | 日志中高频出现，Celery INSERT 被 Uvicorn 写操作阻塞 |
|------|------|
| 根因 | SQLAlchemy 连接 `busy_timeout=0`，即使 WAL 模式也立即放弃 |
| 文件 | `backend/app/database.py` — `create_engine` 的 `connect_args` |
| 修复 | 增加 `connect_args={"check_same_thread": False, "timeout": 30}` |

### 🔴 问题 2：`kb embedding unavailable: Could not import module 'PreTrainedModel'`

| 现象 | ColBERT/RAGatouille 依赖链断裂 |
|------|------|
| 根因 | `ragatouille==0.0.9.post2` 依赖 `colbert-ai`，内部 `PreTrainedModel` 导入链与 transformers 版本不兼容 |
| 文件 | `backend/app/services/colbert_store.py`、`backend/requirements.txt` |
| 修复 | 锁定兼容版本组合：`ragatouille>=0.3.0` 或降级 `transformers<4.49` |

### 🟡 问题 3：`operation failed: 'Docs' object has no attribute 'add_texts'`

| 现象 | RAGatouille API 变更，`add_texts` / `add_texts_from_str` 已移除 |
|------|------|
| 根因 | `backend/app/services/llm.py:1284-1286` 调用了已废弃的 RAGatouille 方法 |
| 文件 | `backend/app/services/llm.py` |
| 修复 | 改用 `docs.add_documents()` 或条件检查 `hasattr` 并回退 |

---

## 实施步骤

### Task 1：修复 SQLite busy_timeout

**文件**: `backend/app/database.py`

**Step 1**: 定位 `create_engine` 调用处
```bash
grep -n 'create_engine' backend/app/database.py
```

**Step 2**: 在 `connect_args` 中增加 `"timeout": 30`
```python
engine = create_engine(
    url,
    connect_args={
        "check_same_thread": False,
        "timeout": 30,           # 新增：等 30s 而非立即失败
    },
    ...
)
```

**Step 3**: 重启 Uvicorn + Celery，验证无 `database is locked` 错误

---

### Task 2：修复 ColBERT embedding

**文件**: `backend/requirements.txt`、`backend/app/services/colbert_store.py`

**Step 1**: 升级 ragatouille 到兼容版本
```bash
pip install ragatouille>=0.3.0
```

**Step 2**: 若升级后仍有 `PreTrainedModel` 错误，降级 transformers
```bash
pip install "transformers==4.48.0"
```

**Step 3**: 验证 import
```bash
python -c "from ragatouille import RAGPretrainedModel; print('OK')"
```

**Step 4**: 重启 Celery，检查日志 `kb embedding unavailable` 是否消失

---

### Task 3：修复 langchain `add_texts` API

**文件**: `backend/app/services/llm.py`（约第 1284 行）

**Step 1**: 将 `add_texts` / `add_texts_from_str` 替换为 `add_documents`
```python
# 旧代码 (1284-1286):
docs.add_texts_from_str(text, citation=ev.title, docname=key) if hasattr(
    docs, "add_texts_from_str"
) else docs.add_texts(text, citation=ev.title, docname=key)

# 新代码:
try:
    docs.add_documents([text], citation=ev.title, docname=key)
except AttributeError:
    logger.warning("RAGatouille add_documents unavailable, skipping embedding for %s", key)
```

**Step 2**: 重启 Celery，检查日志 `operation failed (permanent): 'Docs' object` 是否消失

---

## 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ragatouille 升级引入新不兼容 | 中 | 高 | 先降级 transformers 方案验证 |
| busy_timeout 不改写锁行为 | 低 | 低 | WAL+timeout 是标准方案 |
| 修复后仍有间歇性锁 | 中 | 中 | 需迁移 PostgreSQL（长期方案） |
| embedding 模型下载慢 | 高 | 低 | 首次启动需 ~2min，仅一次 |

## 验证标准

执行以下命令，三项均通过即修复成功：

```bash
# 1. 无 SQLite 锁错误
grep -c 'database is locked' celery.log  # 修复后应为 0

# 2. Embedding 可用
grep -c 'kb embedding unavailable' celery.log  # 修复后应为 0

# 3. 无 add_texts 错误
grep -c "add_texts" celery.log  # 修复后应为 0
```
