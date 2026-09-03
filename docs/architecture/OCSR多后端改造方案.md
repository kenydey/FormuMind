# FormuMind OCSR 多后端改造方案

> 目标：用实测数据已定案的 **MolScribe**（无 GPU）替换过重的 DECIMER（TF），保留 DECIMER + decimer-segmentation 作为**有 GPU 时**的后端。OSRA / MolGrapher / Img2Mol / SwinOCSR 已淘汰。
>
> 定案依据（2026-08 POC 实测，20 张业务结构图）：
> - **MolScribe**：准确率 20/20=100%，内存峰值 1.93GB，冷启动 34.7s，推理 53.7s/张
> - **DECIMER**（现状）：单点 100%，内存 3.14GB，冷启动 137s，推理 10s/张，已致 OOM 整栈崩溃

---

## 一、背景与目标

当前 FormuMind 的离线结构识别走 DECIMER（TensorFlow），已造成两个问题：

1. **OOM 级联崩溃**：DECIMER worker 的 TF 冷启动峰值 3.14GB，与主栈（~4GB）并发时触发全局 OOM（2026-08-23 09:46 实测整栈崩溃）。
2. **必须独立 venv + 独立 worker**：TF 与主 venv 的 torch 冲突。

**目标**：引入 OCSR 后端抽象，默认无 GPU 走 MolScribe（torch，内存省 40%、冷启动快 4 倍、准确率 100%），有 GPU 时走 DECIMER + segmentation。DECIMER worker 默认不随主栈启动（消除 OOM 风险）。

---

## 二、架构设计

### 2.1 后端选择逻辑

```
resolve_ocsr_backend(settings):
  backend = settings.ocsr_backend            # auto | molscribe | decimer
  if backend == "molscribe": return "molscribe"
  if backend == "decimer":   return "decimer"
  # auto：探测 GPU
  if torch.cuda.is_available() or tf.config.list_physical_devices("GPU"):
      return "decimer"          # 有 GPU → DECIMER + segmentation
  return "molscribe"            # 无 GPU → MolScribe（当前 VPS）
```

### 2.2 数据流（兜底链，5 级）

```
extract_image(image)
  │
  ├─ ① OCSR 离线直识（免 token，主路径）
  │     ┌─ molscribe 后端 → send_task("formumind.molscribe_recognize", queue="molscribe")
  │     │                    → .venv-molscribe worker（torch 2.3.0+cpu + MolScribe）
  │     └─ decimer   后端 → send_task("formumind.decimer_recognize",  queue="decimer")
  │                          → .venv-decimer worker（tensorflow-cpu + DECIMER）
  │     ↓ 成功 → _molecules_from_smiles(smiles)  [RDKit 验证兜底]
  │
  ├─ (decimer+gpu 分支) ②③ decimer-segmentation 切分 → decimer 识别   [预留]
  │
  └─ ④ 视觉 LLM 完整识别（兜底，吃 token）→ RDKit 验证   [原逻辑不动]
```

**关键点**：①②③④ 全部失败才返回占位符；两个后端共用同一 `_molecules_from_smiles` + RDKit 验证出口。

### 2.3 进程隔离（与现状 DECIMER 同构，更轻）

| 后端 | venv | 依赖 | Celery 队列 | 内存峰值 |
|------|------|------|------------|---------|
| molscribe | `.venv-molscribe` | torch 2.3.0+cpu + MolScribe（源码装） | `molscribe` | 1.93GB |
| decimer | `.venv-decimer` | tensorflow-cpu + DECIMER | `decimer` | 3.14GB |

两者都独立于主 backend venv（MolScribe 因锁 `numpy<2.0` 与主 venv 的 numpy 2.4.6 冲突；DECIMER 因 TF vs torch 冲突）。

---

## 三、文件变更清单

### 后端（8 个文件）

| # | 文件 | 变更 | 类型 |
|---|------|------|------|
| 1 | `backend/app/config.py` | 新增 `ocsr_backend: str = "auto"`（auto/molscribe/decimer）、`molscribe_queue: str = "molscribe"`、`molscribe_timeout_s: float = 180.0`、`molscribe_beam_size: int = 5`；保留 `decimer_*` 字段 | 改 |
| 2 | `backend/app/services/ocsr.py` | **新增**：统一 OCSR adapter。`resolve_ocsr_backend()` / `predict_smiles_molscribe()` / `predict_smiles_decimer()` / `molscribe_available()` / `availability()`（多后端状态报告） | 新增 |
| 3 | `backend/app/services/decimer_ocr.py` | 保留（DECIMER 后端实现），`resolve_decimer_mode()` 语义不变，供 decimer worker 内部使用 | 不改 |
| 4 | `backend/app/services/vision_extract.py` | `_decimer_direct` 重构为 `_ocsr_direct(content, settings, backend)`：按后端投递到对应队列；`extract_image()` 改调 `resolve_ocsr_backend()` 分发 | 改 |
| 5 | `backend/app/worker/tasks.py` | 新增 `run_molscribe_recognize_task`（`formumind.molscribe_recognize`，调 `predict_smiles_molscribe`）；保留 `run_decimer_recognize_task` | 改 |
| 6 | `backend/app/worker/celery_app.py` | `_prewarm_decimer` 改为 `_prewarm_ocsr`：按当前进程 venv 自动选择预热 MolScribe 或 DECIMER | 改 |
| 7 | `backend/app/api/settings.py` | 新增 `GET/POST /api/settings/ocsr`（后端选择 + 多后端状态）；`/api/settings/decimer` 保留为兼容（转调 ocsr） | 改 |
| 8 | `backend/app/services/env_flags.py` | FLAG_REGISTRY：`decimer_enabled` 文案改为「OCSR 离线结构识别」，新增 `ocsr_backend` 说明 | 改 |

### 前端（3 个文件）

| # | 文件 | 变更 | 类型 |
|---|------|------|------|
| 9 | `frontend/src/components/OcsrPanel.tsx` | **新增**（由 DecimerPanel 改造）：后端下拉（auto/molscribe/decimer）+ 多后端状态展示 | 新增 |
| 10 | `frontend/src/components/SettingsModal.tsx` | 引用 `OcsrPanel` 替换 `DecimerPanel` | 改 |
| 11 | `frontend/src/api.ts` | `getOcsr`/`setOcsrBackend` 端点 | 改 |

### 脚本（3 个文件）

| # | 文件 | 变更 | 类型 |
|---|------|------|------|
| 12 | `scripts/setup_molscribe_worker.sh` | **新增**：建 `.venv-molscribe`（torch 2.3.0+cpu + MolScribe 源码装 + huggingface_hub），固化 POC 已验证的安装序列 | 新增 |
| 13 | `scripts/start_all.sh` | 默认启动 molscribe worker（无 GPU 默认后端）；decimer worker 保持按需（`--with-decimer`） | 改 |
| 14 | `scripts/setup_decimer_worker.sh` | 保留（GPU 分支） | 不改 |

### 测试（1 个文件）

| # | 文件 | 变更 |
|---|------|------|
| 15 | `backend/tests/test_ocsr_adapter.py` | **新增**：`resolve_ocsr_backend` 三值分发、`_ocsr_direct` 按后端选队列、缺库时中性返回 |

---

## 四、实施步骤时间表

| 阶段 | 内容 | 预估 | 依赖 |
|------|------|------|------|
| **P1 后端 adapter** | config 字段 + `ocsr.py` + `tasks.py` 新增 molscribe 任务 + `vision_extract.py` 分发 | 0.5 天 | 无 |
| **P2 worker 脚本** | `setup_molscribe_worker.sh`（复用 POC venv 安装序列）+ `start_all.sh` 默认启动 molscribe | 0.5 天 | P1 |
| **P3 API + 前端** | `ocsr` 端点 + `OcsrPanel.tsx` + `SettingsModal` + `api.ts` | 0.5 天 | P1 |
| **P4 测试全绿** | `test_ocsr_adapter.py` + 跑现有 pytest（排除已知预失败） | 0.5 天 | P1-P3 |
| **P5 集成验证** | 起 molscribe worker → 上传真实结构图 → 验证直识 + 回退链 + 前端状态 | 0.5 天 | P1-P4 |
| **P6 推理提速验证** | 调 `molscribe_beam_size`（5→2/3）实测提速，权衡准确率 | 0.5 天 | P5 |

**总计约 3 天**（可并行 P1-P3，压缩到 2 天）。

---

## 五、风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| **MolScribe 推理慢 53.7s/张** | 高 | 中 | 默认 beam_size 调小（P6 实测）；或保留 `auto` 但给「慢但省 token」的预期说明 |
| MolScribe numpy<2.0 与主 venv 冲突 | 高（确定） | 中 | 必须独立 venv（方案已按此设计），不试图进主 venv |
| torch AVX2 SIGILL（老 CPU） | 高（确定） | 高 | 锁定 `torch==2.3.0+cpu`（与主 venv 一致），setup 脚本固化，禁止升级 |
| MolScribe PyPI 包锁 torch<2.0 | 高（确定） | 中 | 必须 `git clone` 源码装（非 `pip install MolScribe`），脚本固化 |
| 双 worker 内存叠加 | 中 | 高 | 默认只起 molscribe worker（1.93GB）；decimer 按需，不并发 |
| GPU 分支未实测 | 中 | 低 | decimer+segmentation 标为「预留」，当前 VPS 无 GPU 不启用；上 GPU 前需补测 |
| 前端 DecimerPanel 改造遗漏 | 低 | 低 | OcsrPanel 复制改造，旧面板保留可回退 |

---

## 六、验收标准

1. **无 GPU（当前 VPS）**：`ocsr_backend=auto` → 解析为 `molscribe`；上传结构图走 MolScribe 直识成功（准确率 ≥ 现有 DECIMER 水平）。
2. **回退链**：MolScribe 失败/缺库时自动回退视觉 LLM，全程 RDKit 验证，行为与现状一致。
3. **DECIMER worker 默认不随主栈启动**（消除 OOM 风险），`--with-decimer` 按需启动仍可用。
4. **pytest 全绿**（排除已知预失败：test_integrations / test_pipeline / test_lifespan_fastpath / test_api_auth）。
5. **前端**：设置页 OCSR 面板可选后端（auto/molscribe/decimer），状态实时反映当前后端 + 是否已装。
6. **无回归**：`decimer_enabled=false` 或未装任何后端时，管线行为与现状完全一致（中性返回 → 视觉 LLM 兜底）。

---

## 七、不在本次范围

- DECIMER segmentation 的实际实现（Mask R-CNN 切分）——GPU 分支预留，上 GPU 前单独评估。
- MolParser（Markush 支持）——远期，等代码/权重成熟。
- 视觉 LLM 定位 bbox（②③ 兜底中间级）——当前 CPU 模式定位仍靠视觉 LLM 完整识别。
