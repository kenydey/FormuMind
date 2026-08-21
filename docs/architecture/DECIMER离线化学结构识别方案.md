# DECIMER 离线化学结构识别方案（GPU/CPU 自适应 + Celery worker 隔离）— 实施方案 v2

> **目标**：引入 DECIMER 离线 OCSR（光学化学结构识别），作为「结构图→SMILES」的**主路径**（免 token、省钱），在线视觉 LLM（qwen3.8-max）降级为**兜底路径**。通过环境变量在 **GPU 模式**（tensorflow 完整版 + decimer-segmentation，先切分后识别）与 **CPU 模式**（tensorflow-cpu + 纯识别，无 segmentation）之间切换。DECIMER 运行在**独立 Celery worker 进程 + 独立 venv**，与主 backend 的 torch 环境完全隔离。

---

## 1. 背景与 POC 实测数据（2026-08-21）

在 VPS（4 核 E5-2690 v2 / 6.3G 内存 / 无 GPU）上实测 DECIMER 2.8.0：

| 指标 | `tensorflow` 完整版 | `tensorflow-cpu` | 结论 |
|---|---|---|---|
| 峰值内存 | 4066 MB | **3213 MB（3.14GiB）** | 必须用 cpu 版，否则吃 swap |
| 纯推理 | 10.0 s/张 | 9.5 s/张 | 慢，仅适合异步批量 |
| 冷启动 import | 137 s | 139 s | worker 常驻可摊薄 |
| 识别准确率 | ✅ | ✅ | 咖啡因 SMILES 100% 正确 |

**三条硬结论**（已沉淀进 `chemical-structure-to-smiles` skill）：
1. DECIMER 可纯 CPU 跑，但**必须 `tensorflow-cpu`**（-21% 内存）；
2. **decimer-segmentation（Mask R-CNN）在无 GPU 小内存机不可行**（会在 3.14GiB 上再加 1-1.5GiB 直接 OOM）——所以 CPU 模式跳过 segmentation；
3. DECIMER 识别免 token，作为**主路径**可显著降低视觉 LLM 的 token 消耗与费用。

---

## 2. 架构变更总览

### 2.1 兜底链（v3 精细降级：全程免 token 优先，省钱）

```
              结构图 → SMILES 识别
                     │
                     ▼
      ① DECIMER 离线直识（主路径，免 token，假设已裁剪）
         ├─ 成功 ─────────────────→ SMILES ──┐
         └─ 失败 / 缺 worker                  │
                     │                        │
                     ▼                        │
      ② 视觉 LLM 定位 bbox（吃少量 token）      │
         │ 裁剪出结构图局部                     │
         ▼                                    │
      ③ DECIMER 识别裁剪图（免 token）          │
         ├─ 成功 ─────────────────→ SMILES ──┤
         └─ 失败                               │
                     │                        │
                     ▼                        │
      ④ 视觉 LLM 完整识别（兜底，吃 token）      │
         ├─ 成功 ─────────────────→ SMILES ──┤
         └─ 失败 / 429 / 断路器                │
                     │                        │
                     ▼                        ▼
      ⑤ 占位符 + 警告        ┌──→ ⑥ RDKit MolFromSmiles 验证兜底
                             │    （主 backend，已有 _verify_molecules）
                             ▼
                   SMILES 字符串（verified / confidence）
```

> **省钱逻辑**：token 消耗从「每图一次 LLM 识别」降到「仅当 DECIMER 直识 + 裁剪图识别都失败时才用 LLM 完整识别」；LLM 定位只在 DECIMER 直识失败后触发，且只花少量 token（输出 bbox 而非完整 SMILES）。

### 2.2 定位 vs 识别分离（CPU 模式边界，已确认接受）

DECIMER 只做**识别**，不负责**定位**。定位同样「免 token 优先」——先赌图已裁剪，失败才动用 LLM 定位：

```
整页 PDF / 图片
      │
      ▼
 ┌ ① DECIMER 直识（免 token，假设已裁剪）────────────┐
 │    成功 → 直接返回 SMILES（零 token）              │
 │    失败 ↓                                         │
 └──────────────────────────────────────────────────┘
      │
      ▼
 ┌ ② 定位结构图（找 bbox，仅直识失败后才触发）────────┐
 │  · 缓存 bbox（历史定位结果复用）→ 免 token          │
 │  · GPU 模式 → decimer-segmentation（离线，免 token）│
 │  · CPU 模式 → 视觉 LLM 定位（一次性，少量 token）   │
 └──────────────────────────────────────────────────┘
      │ 裁剪出结构图局部
      ▼
   ③ DECIMER 识别裁剪图（免 token）→ 失败 → ④ LLM 完整识别
```

> **已确认边界**：无视觉 LLM 时（未配置 / 429），CPU 模式只能识别「已裁剪的结构图」——即流程退化为「① DECIMER 直识 → 失败则占位符」；整页 PDF 的结构定位在该场景下不可用（GPU 模式无此限制，segmentation 离线完成定位）。

### 2.3 进程隔离拓扑（关键）

```
┌─ 主 backend venv（torch 2.3.0+cpu，现状不变）────────────────┐
│  uvicorn API（8000）+ 主 Celery worker（default 队列）          │
│  vision_extract.py 结构图→SMILES 主流程                        │
│  ↓ 先投递 DECIMER（decimer 队列）→ 失败再走视觉 LLM            │
│  send_task("formumind.decimer_recognize", ...) → Redis          │
└───────────────────────────────────────────────────────────────┘
                            │ Redis（broker/result，现状复用）
                            ▼
┌─ 独立 decimer venv（tensorflow-cpu + decimer，无 torch）────────┐
│  decimer Celery worker：                                        │
│    celery -A app.worker.celery_app worker -Q decimer -c 1       │
│    TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1           │
│  run_decimer_recognize_task → predict_SMILES → 返回 SMILES      │
└───────────────────────────────────────────────────────────────┘
```

> **为什么必须独立 venv**：backend `.venv` 已有 CPU torch 2.3.0（ColBERT/Baybe/BoTorch 用），DECIMER 依赖 TensorFlow，两者同 venv 必冲突。独立 worker 还天然获得内存隔离（DECIMER 峰值 3.14GiB 不挤占 API 进程）。

---

## 3. 配置项定义

### 3.1 `backend/app/config.py`（Settings 新增字段）

```python
# ═══ DECIMER 离线结构识别（OCSR）════
# 化学结构图离线识别为 SMILES，作为主路径（免 token），视觉 LLM 兜底。
# 运行在独立 decimer Celery worker（独立 venv），不占主服务内存。
decimer_enabled: bool = False
# auto = 探测（GPU 可用→gpu，否则→cpu）；gpu = tensorflow 完整版 + segmentation；
# cpu = tensorflow-cpu + 纯识别（无 segmentation，定位交给视觉 LLM）
decimer_mode: str = "auto"
# TensorFlow 线程限流（CPU 模式必须 =1，防吃满 4 核饿死 API worker）
decimer_threads: int = 1
# Celery 队列名（独立 worker 消费）
decimer_queue: str = "decimer"
# DECIMER 单张识别超时（CPU 纯推理 10s + 排队余量）
decimer_timeout_s: float = 60.0
```

**环境变量映射**：
```bash
FORMUMIND_DECIMER_ENABLED=true|false
FORMUMIND_DECIMER_MODE=auto|gpu|cpu
FORMUMIND_DECIMER_THREADS=1
FORMUMIND_DECIMER_QUEUE=decimer
FORMUMIND_DECIMER_TIMEOUT_S=60
```

### 3.2 环境标志（`services/env_flags.py` 的 FLAG_REGISTRY）

```python
EnvFlag("decimer_enabled", "DECIMER 离线结构识别",
        "化学结构图优先用 DECIMER 离线识别为 SMILES（免 token，省费用），"
        "失败自动回退视觉 LLM。独立 worker 进程，不占主服务内存。"
        "cpu 模式纯识别；gpu 模式含 decimer-segmentation 结构切分（预留）。",
        "chem", "需独立 decimer worker venv + Celery worker；冷启动约 2 分钟"),
```

> 注册后，现有 Settings UI 会自动显示该开关（env_flags 的既定机制），无需新写前端开关组件。

---

## 4. 模式矩阵（决策矩阵）

| 模式 | 依赖 | segmentation | 内存峰值 | 适用环境 |
|------|------|:---:|---:|---|
| `cpu` | `tensorflow-cpu` + `decimer` | ❌ 跳过（视觉 LLM 定位） | 3.14 GiB | **当前 VPS**（无 GPU） |
| `gpu` | `tensorflow` + `decimer` + `decimer-segmentation` | ✅ Mask R-CNN 先切分 | 高（需 GPU ≥8GB） | **预留**（近期无 GPU 机器） |
| `auto`（默认） | 运行时探测 | GPU 可用→gpu，否则→cpu | — | 通用部署 |

---

## 5. 实现细节

### 5.1 `services/decimer_ocr.py`（新增，软依赖 adapter，参照 chemtools.py）

```python
"""DECIMER 离线 OCSR adapter — 软依赖，缺库/未启用时行为不变。"""
from __future__ import annotations
import logging
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


def decimer_available() -> bool:
    """当前进程能否 import DECIMER（仅独立 decimer worker 为 True）。"""
    try:
        __import__("DECIMER")
        return True
    except Exception:
        return False


def resolve_decimer_mode(settings: Settings | None = None) -> str:
    """auto → 探测 GPU；显式 gpu/cpu 直接返回。"""
    s = settings or get_settings()
    mode = (s.decimer_mode or "auto").strip().lower()
    if mode in ("gpu", "cpu"):
        return mode
    try:
        import tensorflow as tf
        if tf.config.list_physical_devices("GPU"):
            return "gpu"
    except Exception:
        pass
    return "cpu"


def predict_smiles_local(image_path: str) -> str | None:
    """同进程直接识别（仅当 decimer 已装在当前 venv，即 decimer worker 内）。"""
    if not decimer_available():
        return None
    try:
        from DECIMER import predict_SMILES
        return predict_SMILES(image_path)
    except Exception as exc:
        logger.warning("DECIMER predict_SMILES failed: %s", exc)
        return None


def availability() -> dict:
    s = get_settings()
    return {
        "enabled": s.decimer_enabled,
        "mode": resolve_decimer_mode(s),
        "installed_in_process": decimer_available(),
        "queue": s.decimer_queue,
        "segmentation": resolve_decimer_mode(s) == "gpu",
    }
```

### 5.2 `worker/tasks.py`（新增 Celery 任务，仅 decimer worker 消费）

```python
@celery_app.task(bind=True, name="formumind.decimer_recognize")
def run_decimer_recognize_task(self, payload: dict) -> dict:
    """离线识别单张结构图 → SMILES。只在 decimer 队列 worker 上执行。"""
    from ..services.decimer_ocr import predict_smiles_local
    image_path = payload.get("image_path", "")
    smiles = predict_smiles_local(image_path)
    if not smiles:
        return {"ok": False, "smiles": None, "reason": "DECIMER unavailable or failed"}
    return {"ok": True, "smiles": smiles}
```

### 5.3 `services/vision_extract.py`（主路径反转：DECIMER 优先 → 视觉 LLM 兜底）

```python
def _decimer_direct(content: bytes, settings) -> VisionExtraction | None:
    """① DECIMER 离线直识（免 token，假设图已裁剪）。"""
    if not settings.decimer_enabled:
        return None
    try:
        from app.worker.celery_app import celery_app
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(content); path = f.name
        res = celery_app.send_task(
            "formumind.decimer_recognize",
            args=[{"image_path": path}],
            queue=settings.decimer_queue,
        ).get(timeout=settings.decimer_timeout_s)
        os.unlink(path)
        if res and res.get("ok"):
            return _molecules_from_smiles(res["smiles"])  # 复用 _verify_molecules 做 RDKit 验证
    except Exception as exc:
        logger.warning("DECIMER direct path failed: %s", exc)
    return None


def _llm_locate_and_decimer(content: bytes, filename: str, settings) -> VisionExtraction | None:
    """② LLM 定位 bbox（少量 token）→ 裁剪 → ③ DECIMER 识别（免 token）。"""
    if not settings.decimer_enabled:
        return None
    try:
        # ② 视觉 LLM 只输出结构图 bbox（normalized 坐标），不输出 SMILES
        boxes = _vision_locate_boxes(content, filename, settings)  # 新能力：LLM 定位
        if not boxes:
            return None
        # 裁剪每个 bbox → 送 DECIMER 识别（免 token）
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(content))
        for box in boxes:
            cropped = img.crop(_denormalize(box, img.size))
            buf = io.BytesIO(); cropped.save(buf, "PNG")
            extraction = _decimer_direct(buf.getvalue(), settings)
            if extraction is not None:
                return extraction
    except Exception as exc:
        logger.warning("LLM-locate + DECIMER path failed: %s", exc)
    return None


def extract_image(content: bytes, filename: str):
    """结构图→SMILES：① DECIMER 直识 → ② LLM 定位 + ③ DECIMER 识 → ④ LLM 完整识别 → ⑤ 占位符。"""
    settings = get_settings()
    if settings.decimer_enabled:
        # ① DECIMER 直识（免 token）
        extraction = _decimer_direct(content, settings)
        if extraction is not None:
            return extraction, None
        # ②③ LLM 定位 + DECIMER 识别（定位少量 token，识别免 token）
        extraction = _llm_locate_and_decimer(content, filename, settings)
        if extraction is not None:
            return extraction, None
    # ④ 兜底：视觉 LLM 完整识别（原逻辑不动）
    return _vision_llm_extract(content, filename)   # 现有 vision_available + _call_vision 路径
```

> **② LLM 定位是新增能力**：需扩展视觉 prompt + schema，让 LLM 输出结构图 `bbox`（normalized 坐标）而非完整 SMILES。若该能力暂不实现，则②③退化为「跳过」，流程等价于 v2 的「① DECIMER 直识 → ④ LLM 完整识别」——本期可先实现①④，②③作为增量优化。
```

### 5.4 部署脚本（新增 `scripts/setup_decimer_worker.sh`）

```bash
#!/usr/bin/env bash
set -euo pipefail
DECIMER_VENV="${DECIMER_VENV:-/root/FormuMind/.venv-decimer}"
MODE="${FORMUMIND_DECIMER_MODE:-cpu}"

python3.11 -m venv "$DECIMER_VENV"
source "$DECIMER_VENV/bin/activate"

if [ "$MODE" = "gpu" ]; then
    pip install -e "/root/FormuMind/backend[decimer-gpu]"
    apt-get install -y poppler-utils   # segmentation 内部 PDF→图用（预留）
else
    pip install -e "/root/FormuMind/backend[decimer-cpu]"
    pip uninstall -y tensorflow && pip install "tensorflow-cpu>=2.12,<2.21"
fi

export TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1
exec celery -A app.worker.celery_app worker \
    --queues="${FORMUMIND_DECIMER_QUEUE:-decimer}" --concurrency=1 --loglevel=info
```

> **关键 pitfall**：`decimer` 的 `requires_dist` 声明 `tensorflow>=2.12,<=2.20`（完整版），`pip install decimer` 会拉完整版。CPU 模式必须「装完后 `uninstall tensorflow` → 装 `tensorflow-cpu`」，否则峰值 4GiB 吃 swap（POC 已实测）。

---

## 6. 依赖管理（pyproject.toml）

```toml
[project.optional-dependencies]
# DECIMER 离线 OCSR：cpu 纯识别 / gpu 含结构切分（预留）。
# 均需独立 venv + 独立 Celery worker（tensorflow 与主 env 的 torch 冲突）。
decimer-cpu = ["decimer>=2.8.0", "tensorflow-cpu>=2.12,<2.21"]
decimer-gpu = ["decimer>=2.8.0", "tensorflow>=2.12,<2.21", "decimer-segmentation>=1.5.0"]
```

---

## 7. 文件变更清单

| # | 文件 | 变更 | 规模 |
|---|---|---|---|
| 1 | `backend/app/config.py` | 新增 `decimer_enabled` / `decimer_mode` / `decimer_threads` / `decimer_queue` / `decimer_timeout_s` | +12 |
| 2 | `backend/app/services/decimer_ocr.py` | **新增**（软依赖 adapter） | +90 |
| 3 | `backend/app/services/vision_extract.py` | 主路径反转：DECIMER 优先 → 视觉 LLM 兜底 | +50 |
| 4 | `backend/app/services/env_flags.py` | 注册 `decimer_enabled` flag（自动进设置 UI） | +6 |
| 5 | `backend/app/worker/tasks.py` | 新增 `run_decimer_recognize_task` | +15 |
| 6 | `backend/pyproject.toml` | 新增 `decimer-cpu` / `decimer-gpu` extras | +4 |
| 7 | `scripts/setup_decimer_worker.sh` | **新增**（独立 venv 创建 + 启动 worker） | +25 |
| 8 | `frontend`（设置面板） | DECIMER 开关（复用 env_flags UI）+ 模式下拉 + 状态展示 | ~40 |
| 9 | `backend/tests/test_decimer_ocr.py` | **新增**（mock：mode 解析 / availability / 兜底链分支） | +60 |
| 10 | `docs/architecture/DECIMER离线化学结构识别方案.md` | 本方案 | — |

**总计**：约 +300 行新增，零破坏性变更（全部走「软依赖 + 关闭时行为不变」模式）。

---

## 8. 实施步骤（预计 6-7h，含前端）

| # | 步骤 | 时间 |
|---|---|---|
| 1 | `config.py` + `env_flags.py`：新增字段与 flag | 15min |
| 2 | `services/decimer_ocr.py`：软依赖 adapter | 45min |
| 3 | `worker/tasks.py`：新增 decimer_recognize 任务 | 20min |
| 4 | `vision_extract.py`：主路径反转（DECIMER 优先 → 视觉 LLM 兜底） | 1h |
| 5 | `pyproject.toml` extras + `setup_decimer_worker.sh` | 30min |
| 6 | 独立 venv 安装（cpu 模式实测）+ 启动 decimer worker | 30min |
| 7 | 前端设置面板：开关 + 模式下拉 + 状态展示 | 1h |
| 8 | 测试：`test_decimer_ocr.py` + 全量回归（mock，不真跑 TF） | 1h |
| 9 | 端到端验证：结构图 → DECIMER 主路径 → 视觉 LLM 兜底链全走通 | 30min |

---

## 9. 风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| tensorflow vs torch 同 venv 冲突 | 🔴 | 独立 decimer venv（方案核心），绝不装进 backend `.venv` |
| 内存 3.14GiB 逼近 available 3.4GiB | 🔴 | 独立 worker `-c 1` + `TF_*_THREADS=1`；swap 已有 5.5Gi 兜底 |
| decimer 拉 tensorflow 完整版（非 cpu） | 🔴 | 安装脚本 `uninstall tensorflow → install tensorflow-cpu`（POC 验证） |
| DECIMER 优先增加识别延迟（10s/张 + 排队） | 🟡 | 独立队列异步化，不阻塞 API；超时 `decimer_timeout_s` 后立即回退视觉 LLM |
| 冷启动 import 137s | 🟡 | worker 常驻，模型只加载一次 |
| DECIMER 语义错误（差一个 CH₂） | 🟡 | 复用 `_verify_molecules` 的 RDKit 验证 + confidence 标记 |
| 磁盘（venv 2.4G + 模型 664M） | 🟡 | 独立 venv 单独管理，可随时删除重建 |
| decimer 模型首次下载 285MB | 🟡 | 安装脚本预下载到 `~/.data/DECIMER-V2`（部署时一次性） |
| GPU 模式 segmentation 依赖 poppler | 🟢 | `apt install poppler-utils`，仅 GPU 预留部署需要 |

---

## 10. 已确认决策（v2 更新）

1. ✅ **兜底链反转**：DECIMER 离线（主，免 token）→ 视觉 LLM（在线兜底）→ 占位符，优先省钱。
2. ✅ **接受边界**：无视觉 LLM 时 CPU 模式只能识别「已裁剪结构图」。
3. ✅ **前端设置面板**：本期做（复用 env_flags UI 自动开关 + 模式下拉 + 状态）。
4. ✅ **GPU 模式**：预留（`gpu` 配置与 `decimer-gpu` extra 已声明，近期不部署）。

---

**是否批准此 v2 方案并开始实施？**
