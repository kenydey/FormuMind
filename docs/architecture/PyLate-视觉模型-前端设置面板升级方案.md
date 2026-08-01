# PyLate ColBERT + 视觉模型 + 前端设置面板 — 升级方案

> **状态**: 待审核  
> **目标**: GPU/CPU 自适应检索全面上线，视觉管道独立于检索后端，前端设置面板补齐缺失配置

---

## 1. 现状分析

### 1.1 PyLate ColBERT 与 CPU 兼容性

| 维度 | 分析 |
|------|------|
| **PyLate 本质** | PyLate 是 ColBERT late-interaction 检索的 PyTorch 纯实现。模型权重是浮点矩阵运算，**CPU 可跑**（慢 5-10x，但功能正常） |
| **当前状态** | `gpu_enabled=true` 时，`colbert_available_gpu()` 检查 `torch.cuda.is_available()`，无 GPU 则返回 `False`，退回 `bm25_faiss` |
| **关键阻塞** | 不是 PyLate 本身，而是 **当前 VPS 的 Xeon E5-2690 v2 缺少 AVX2**。PyLate 底层 FAISS/torch 编译时启用了 AVX2，执行到 AVX2 指令时 SIGILL |
| **PyLate AVX2 问题** | 与 Stanford ColBERT 同源 — 都走 FAISS → AVX2 路径。安装 `faiss-cpu` 的 baseline x86_64 wheel 可解决，但 PyLate 未提供纯净 CPU wheel |

**结论**：PyLate ColBERT 在无 AVX2 的 CPU 上与 Stanford ColBERT 有**相同的 SIGILL 问题**。当前设计已正确处理：检测 GPU+CUDA → 无则退到 `bm25_faiss`。**无需额外改动**。

### 1.2 视觉大模型与 ColBERT 的关系

| 维度 | 分析 |
|------|------|
| **视觉管道** | 已通过 `vision_extract_enabled` 标志独立实现，通过 LLM API 调用视觉模型（DeepSeek vision 等） |
| **与检索后端无关** | 视觉提取调用 LLM API（HTTP），ColBERT/BM25 是文本检索后端，两者**完全解耦** |
| **GPU 需求** | 视觉管道 **不需要本地 GPU** — 所有视觉推理通过云端 API 完成 |
| **多模态图谱** | `kg_multimodal_fusion_enabled` 联动视觉+KG，也通过 API，不依赖本地 GPU |

**结论**：视觉大模型管道**已经在无 GPU 环境可用** — 它走的是 DeepSeek API，不依赖本地算力。`gpu_enabled` 仅控制检索后端（ColBERT vs BM25），不影响视觉功能。**无需额外改动**。

### 1.3 前端设置面板现状

| 配置项 | 类型 | 后端暴露 | 前端可见 | 问题 |
|--------|------|:---:|:---:|------|
| `gpu_enabled` | `bool` | ❌ 不在 FLAG_REGISTRY | ❌ | 只能通过 `.env` 文件或 docker compose 环境变量设置 |
| `formulation_mode` | `str` (`"hybrid"`/`"llm_only"`/`"kb_only"`) | ❌ 不在 FLAG_REGISTRY | ❌ | 同上 |
| `rag_backend` | `str` (`"auto"`/`"bm25_faiss"`/`"pylate"`...) | ❌ 不适用 (非 bool) | ❌ | 同上，且为高级覆盖 |
| `start_period` | docker compose 配置 | N/A | ❌ | 仅影响部署，不适用 UI |

**当前 FLAG_REGISTRY 已有 50 个 bool 开关**，覆盖检索/KB/化学/数据/基础设施 5 个类别，全部通过 `EnvFlagsPanel` 的 True/False toggle 展示。**但缺少 v2 新增的检索+推荐配置**。

---

## 2. 升级方案

### 2.1 `gpu_enabled` — 加入 FLAG_REGISTRY

```python
# 在 FLAG_REGISTRY 中 "检索" category 新增：
EnvFlag("gpu_enabled", "GPU 加速 ColBERT 检索",
        "启用后使用 PyLate ColBERT 进行知识库检索（需 CUDA GPU ≥ 4GB VRAM）。"
        "关闭时使用 BM25 + FAISS 混合检索（纯 CPU，不限硬件）。",
        "retrieval",
        "切换后需重启；GPU 不可用则自动退回 CPU 模式"),
```

**前端效果**：在设置 → 环境变量 → "检索 · Retrieval" 分组中，多一个 True/False toggle。

### 2.2 `formulation_mode` — 新增下拉选择器（非 Toggle）

`formulation_mode` 是 `str` 类型，3 个可选值，不适合 True/False toggle。需要新增一个轻量级下拉组件。

#### 2.2.1 后端：新增 API 端点

```python
# backend/app/api/settings.py

class FormulationModeUpdate(BaseModel):
    mode: str = Field(default="hybrid", pattern="^(hybrid|llm_only|kb_only)$")

@router.get("/settings/formulation-mode")
def get_formulation_mode() -> dict:
    s = get_settings()
    choices = [
        {"value": "hybrid", "label": "Hybrid 叠加", "desc": "知识库证据 + LLM 合成（推荐）"},
        {"value": "llm_only", "label": "LLM Only", "desc": "纯 LLM 推荐（快速，离线）"},
        {"value": "kb_only", "label": "KB Only", "desc": "仅知识库检索（LLM 降级时）"},
    ]
    return {"current": s.formulation_mode, "choices": choices}

@router.post("/settings/formulation-mode")
def set_formulation_mode(body: FormulationModeUpdate) -> dict:
    os.environ["FORMUMIND_FORMULATION_MODE"] = body.mode
    write_env_updates({"FORMUMIND_FORMULATION_MODE": body.mode})
    get_settings.cache_clear()
    return {"mode": body.mode, "status": "ok"}
```

**注**：我不在 `FLAG_REGISTRY` 中处理它，因为 `FLAG_REGISTRY` 只支持 `bool` 类型（见 `_validate_registry()` 中的 `isinstance(field.default, bool)` 检查）。字符串选项用独立端点更清晰。

#### 2.2.2 前端：新增 `FormulationModeSelector` 组件

```tsx
// frontend/src/components/FormulationModeSelector.tsx

import { useEffect, useState } from "react";
import { api } from "../api";

interface ModeChoice {
  value: string;
  label: string;
  desc: string;
}

export default function FormulationModeSelector() {
  const [current, setCurrent] = useState("hybrid");
  const [choices, setChoices] = useState<ModeChoice[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getFormulationMode().then((r) => {
      setCurrent(r.current);
      setChoices(r.choices);
    });
  }, []);

  const handleChange = async (mode: string) => {
    setSaving(true);
    await api.setFormulationMode(mode);
    setCurrent(mode);
    setSaving(false);
  };

  return (
    <div className="border border-edge/60 rounded p-3 bg-panel/20">
      <h3 className="text-sm text-slate-200 mb-2">配方推荐模式</h3>
      <div className="space-y-2">
        {choices.map((c) => (
          <label
            key={c.value}
            className={`flex items-start gap-3 rounded border px-3 py-2 cursor-pointer ${
              current === c.value
                ? "border-accent/50 bg-accent/5"
                : "border-edge/60 hover:border-edge"
            }`}
          >
            <input
              type="radio"
              name="formulation_mode"
              value={c.value}
              checked={current === c.value}
              onChange={() => handleChange(c.value)}
              disabled={saving}
            />
            <div>
              <span className="text-sm text-slate-200">{c.label}</span>
              <p className="text-[11px] text-slate-500">{c.desc}</p>
            </div>
          </label>
        ))}
      </div>
      <p className="text-[10px] text-slate-600 mt-2">
        对应环境变量 FORMUMIND_FORMULATION_MODE
      </p>
    </div>
  );
}
```

#### 2.2.3 `SettingsModal.tsx` — 集成新组件

在 SettingsModal 的 tab 结构中，新增 "推荐" tab 或合并到现有 tab：

```tsx
// 在现有 tabs 后追加
{ label: "推荐 · Rec", key: "recommend" as const }
```

render 时：
```tsx
{settingsTab === "recommend" && <FormulationModeSelector />}
```

### 2.3 `rag_backend` — 高级配置（可选）

`rag_backend` 是高级覆盖项，多数用户不需要。暂不暴露 UI，仅在文档中说明可通过 `.env` 手动设置。

### 2.4 `docker-compose.yml` healthcheck `start_period` — 已修复

已在 `8f461f7` 中完成，无需 UI 暴露。

---

## 3. 文件变更清单

| # | 文件 | 变更 | 行数 |
|---|------|------|------|
| 1 | `env_flags.py` | FLAG_REGISTRY 新增 `gpu_enabled` flag | +6 |
| 2 | `settings.py` | 新增 `get_formulation_mode` / `set_formulation_mode` 端点 | +30 |
| 3 | `FormulationModeSelector.tsx` | 新组件 | +50 |
| 4 | `SettingsModal.tsx` | 新增 "推荐" tab + 集成 FormulationModeSelector | +10 |
| 5 | `api.ts` | 新增 `getFormulationMode` / `setFormulationMode` | +8 |
| 6 | `EnvFlagsPanel.tsx` | 自动渲染新的 `gpu_enabled` toggle（无需改动） | 0 |

**总计**：~104 行新增，1 个新文件，1 个文件修改。

---

## 4. 实施步骤

| # | 步骤 | 时间 |
|---|------|------|
| 1 | `env_flags.py`：添加 `gpu_enabled` 到 FLAG_REGISTRY | 5min |
| 2 | `settings.py`：添加 `get/set_formulation_mode` 端点 | 15min |
| 3 | `api.ts`：添加前端 API 方法 | 5min |
| 4 | `FormulationModeSelector.tsx`：新组件 | 20min |
| 5 | `SettingsModal.tsx`：集成新 tab | 5min |
| 6 | 本地验证 + Docker 部署 | 20min |

---

## 5. 最终效果

### 设置面板新布局

```
┌─ 设置 ──────────────────────────────────────┐
│ [API] [模型] [环境变量] [推荐] ← 新增 tab    │
│                                               │
│ 检索 · Retrieval                              │
│  ┌──────────────────────────────────────────┐ │
│  │ GPU 加速 ColBERT 检索    [True/False]    │ │ ← 新增
│  │ 启用后使用 PyLate ColBERT...             │ │
│  └──────────────────────────────────────────┘ │
│                                               │
│ 推荐 · Rec                                    │
│  ┌──────────────────────────────────────────┐ │
│  │ 配方推荐模式                             │ │
│  │ ○ Hybrid 叠加 — KB证据 + LLM合成（推荐） │ │ ← 新增
│  │ ○ LLM Only — 纯LLM推荐（快速离线）       │ │
│  │ ○ KB Only — 仅知识库检索（LLM降级）       │ │
│  └──────────────────────────────────────────┘ │
│                                               │
│ RAG 状态（只读）                               │
│  检索后端: bm25_faiss  |  公式模式: hybrid     │ ← 新增
└───────────────────────────────────────────────┘
```

### 关于视觉大模型

**视觉管道已在无 GPU 环境可用**。`vision_extract_enabled` 标志控制图片/表格的结构化提取（通过 DeepSeek vision API），与 `gpu_enabled` 完全解耦：

| 场景 | GPU_ENABLED | vision_extract_enabled | 效果 |
|------|:---:|:---:|---|
| 当前 VPS | false | true | 视觉提取 → DeepSeek API ✅ <br>KB 检索 → BM25+FAISS ✅ |
| GPU VPS | true | true | 视觉提取 → DeepSeek API ✅ <br>KB 检索 → PyLate ColBERT ✅ |
| 离线 | false | false | 纯文本管道，无视觉 + BM25 检索 |

**PyLate ColBERT 不直接接入视觉模型** — 它的职责是文本检索。视觉能力通过现有的 `vision_extract_enabled` + `kg_multimodal_fusion_enabled` 实现，两者走 LLM API，不受本地 GPU 影响。

---

**是否批准此升级方案？**
