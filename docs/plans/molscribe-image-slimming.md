# MolScribe 镜像精简 — Import 链分析报告

> 目标：将 `formumind-molscribe` 镜像从 **4.8G（4.67G 独占）** 精简为独立最小镜像，
> 在**不影响任何功能**的前提下降低磁盘冗余。本文档只做分析，供评审，不执行改动。

## 1. 现状与问题

当前 `deploy/molscribe/Dockerfile` 采用 `FROM formumind-backend:latest`（3.81G 全量依赖），
再降级 torch（2.13→2.3.0+cpu）+ 安装 MolScribe。由于 torch 降级会**重写整个 site-packages
依赖层**，导致该镜像与 backend 镜像**几乎零共享**（实测 unique 4.67G），等于把 backend
的全部依赖又存了一份，其中大部分（文档解析 / RAG / LLM 编排）对 MolScribe worker 毫无用处。

## 2. Import 链实测分析

### 2.1 Celery worker 启动链（`celery -A app.worker.celery_app.celery_app worker`）

实测 `import app.worker.celery_app` 后加载的**第三方包**（完整环境 backend/.venv 内）：

| 类别 | 包 |
|---|---|
| Celery 生态 | celery, kombu, billiard, amqp, vine, click, tzlocal |
| Pydantic | pydantic, pydantic_core, pydantic_settings, annotated_types, typing_extensions, typing_inspection |
| 配置/序列化 | python-dotenv, loguru, six, zstandard, brotli, cffi, greenlet |
| 科学计算 | **numpy**（84 模块，唯一 heavy） |

**关键结论**：启动链**不加载** torch / rdkit / colbert(ragatouille) / docling / mineru /
langchain / litellm / rapidocr / pyarrow / scipy / botorch 等任何重型包——
`tasks.py → workflow → predictor/optimizer → domain.chemistry` 对这些全部采用**函数内延迟 import**。

### 2.2 MolScribe 任务运行时链（`formumind.molscribe_recognize`）

实测 `from molscribe import MolScribe; import torch` 后加载的第三方包（molscribe 容器内）：

| 类别 | 包 |
|---|---|
| 深度学习 | torch(857 模块), torchvision(149), torchtext(28) |
| MolScribe 本体 | molscribe(19), SmilesPE, timm(136) |
| OpenNMT / 分词 | pyonmttok, sentencepiece, transformers, tokenizers |
| 图像 | albumentations, qudida, scikit-image, lazy_loader, opencv-headless |
| 化学/科学 | rdkit, scipy(490), sympy(419), pandas(293), pyarrow, numpy |
| 推理后端 | onnx, onnxruntime |
| 其他 | huggingface_hub, safetensors, jinja2, joblib, requests, rich, tqdm, psutil, filelock, dill, packaging |

## 3. 最小依赖集结论

**精简镜像 = 启动链(2.1) ∪ 运行时链(2.2)**，具体而言：

```
必须：
  python:3.11-slim 基础
  torch==2.3.0+cpu, torchvision==0.18.0+cpu, torchtext==0.5.0   ← torch 系列(独立 CPU index)
  molscribe==1.1.1, SmilesPE, timm==0.4.12                        ← MolScribe
  OpenNMT-py==2.2.0, pyonmttok, sentencepiece, transformers        ← OpenNMT 栈
  albumentations==1.1.0, qudida, scikit-image, opencv-python-headless
  rdkit, scipy, sympy, pandas, pyarrow, numpy                      ← 化学/科学
  onnx, onnxruntime                                                ← 推理后端
  celery, kombu, billiard, amqp, vine, click, tzlocal              ← celery(与 backend 同版)
  pydantic, pydantic-settings, python-dotenv, loguru               ← 配置
  huggingface_hub                                                  ← 权重解析(离线)
  app 源码（COPY backend/app + pyproject 最小安装）

不必要（可从 backend 镜像继承中剔除，约省 1.5~2G）：
  colbert / ragatouille          ← RAG 检索
  docling / marker-pdf / mineru-open-sdk / pymupdf4llm  ← 文档解析
  rapidocr-onnxruntime           ← 本地 OCR(与 MolScribe 无关)
  langchain / litellm            ← LLM 编排
  google-api-python-client 等    ← Google API
  playwright                     ← 浏览器测试
  botorch / gpytorch             ← 贝叶斯优化(仅 optimize 任务)
  colour / chemicals             ← 配方预测(仅 predictor 完整路径)
```

## 4. 精简 Dockerfile 草案

```dockerfile
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1
WORKDIR /app

# 1) torch CPU（MolScribe 验证组合）
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
    torch==2.3.0+cpu torchvision==0.18.0+cpu

# 2) celery + 配置（与 backend requirements 对齐版本）
RUN pip install celery==5.6.3 kombu==5.6.2 pydantic==2.13.4 pydantic-settings==2.15.0 \
    python-dotenv loguru numpy

# 3) MolScribe + 依赖（--no-deps 跳过 molscribe 的 torch<2.0 过期约束）
RUN pip install --no-deps molscribe==1.1.1 timm==0.4.12 SmilesPE==0.0.3 \
        OpenNMT-py==2.2.0 pyonmttok==1.38.1 sentencepiece==0.2.2 \
        albumentations==1.1.0 qudida==0.0.4 scikit-image==0.26.0 \
        rdkit scipy sympy pandas pyarrow onnx onnxruntime \
    && pip install opencv-python-headless==4.11.0.86

# 4) app 源码（最小安装，只取运行所需）
COPY app ./app
COPY pyproject.toml ./
RUN pip install --no-deps -e .
```

> 注：版本号以 backend 当前 lock 为准，实施时用 `pip freeze` 精确对齐，
> 避免 celery/pydantic 版本漂移导致任务序列化不兼容。

## 5. 预估收益

| 项 | 当前 | 精简后 | 节省 |
|---|---|---|---|
| molscribe 镜像 unique | 4.67G | ~2.5G | **~2G** |
| 镜像总体积 | 13.9G 逻辑 | ~11.5G | ~2G |

## 6. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 遗漏延迟 import 的依赖 → worker 起不来 | 中 | 高(识别不可用) | 构建后 `celery inspect ping` + 真实图端到端验证（已有脚本） |
| 版本漂移导致 celery 任务序列化不兼容 | 低 | 高 | 版本号与 backend `pip freeze` 精确对齐 |
| transformers/tokenizers 与 torch 2.3 兼容性 | 低 | 低 | 主机 venv 已验证同组合可跑（20/20 结构） |
| 精简后缺 `app` 内某运行时模块 | 中 | 中 | 静态 grep 全部 `from ..` 延迟 import 点，逐一核对 |

## 7. 建议实施步骤

1. 冻结当前 backend 的精确版本号（celery/pydantic/numpy 等）
2. 按草案写 `deploy/molscribe/Dockerfile`（改 FROM python:3.11-slim）
3. 构建 → 启动 → `inspect ping` 健康检查
4. 真实结构图端到端识别（双酚A 用例复用）
5. 对比前后镜像体积，确认无功能回归后替换

## 8. 待决策

- 是否接受「精简镜像独立维护一份依赖清单」的代价？（backend 依赖升级时需同步维护 molscribe 清单）
- 若更看重「单一依赖源」，可保留当前 `FROM backend` 方案，接受 4.67G 冗余。
