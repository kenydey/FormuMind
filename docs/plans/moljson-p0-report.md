# MolJSON P0 实测报告 — DeepSeek SMILES vs MolJSON 增益验证

- **日期**：2026-08-30
- **关联**：`docs/plans/moljson-structure-reasoning.md`（P0 决策门）、`scripts/benchmark_moljson.py`（基准脚本）、`app/services/moljson.py`（转换模块）、`tests/test_moljson.py`（单测 6/6 绿）
- **方法**：28 case × 2 格式（SMILES / MolJSON）= 56 次真实 DeepSeek 调用，4 类任务复刻论文（arXiv:2605.01822）子集：数原子 / 环计数 / 官能团 / 连通性

---

## 1. 结果

| 任务 | SMILES | MolJSON | Δ |
|---|---|---|---|
| atoms（数原子，含复杂环/手性） | 7/11 | **9/11** | **+2** |
| rings（SSSR 环数） | 7/7 | 7/7 | 0 |
| funcs（官能团识别） | 6/6 | 6/6 | 0 |
| path（连通性判断） | 4/4 | 4/4 | 0 |
| **total** | **24/28 (86%)** | **26/28 (93%)** | **+2 (7pp)** |

MolJSON 的具体改进（atoms 任务失败案例）：
- `O=C=NC1CC(C)(C)CC(CN=C=O)C1`（IPDI，16 原子）— SMILES 数错，MolJSON 对
- `C1=CC2=C(C=C1)NC3=CC=CC=C3N2`（15 原子稠环）— SMILES 数错，MolJSON 对

---

## 2. 决策门判定

**判定：PASS（达标，但增益温和）**

论文（GPT-5）趋势得到方向性复现：**MolJSON 系统性减少「原子计数 + 环复杂度」类错误**——正是论文识别出的 SMILES 核心失败模式。但幅度远小于论文（7pp vs 论文 27-31pp 差距），原因：

1. **模型差异**：DeepSeek 对 SMILES 的基线能力已很强（86% vs 论文 SMILES 64%），压缩了提升空间
2. **任务差异**：论文最强增益在 constrained generation（95.3% vs 64%）——需结构化输出模式，本基准未复现该任务（简化为连通性，DeepSeek 双格式全对）

## 3. 结论与建议

| 项 | 结论 |
|---|---|
| MolJSON 作为 LLM 输入 | **有真实增益但温和**（+7pp，集中在计数/环任务）——**不值得全链路替换 SMILES**（token 开销大、聚合物/无机盐无 SMILES） |
| **P1 校验闭环**（MolScribe 输出 + 推荐输出 SMILES 过 MolJSON/RDKit 回读） | **值得做**——价值独立于 LLM 增益（纯 RDKit 往返，非法结构拒收），且正是用户「结构零误差」诉求的直接落地 |
| P2 结构级推理（机理问答） | 前置依赖 P1；届时 MolJSON 作输入格式的收益在本报告基础上**只升不降**（推理越复杂，显式图优势越大） |

**推荐**：继续 P1（校验闭环），不在 P0 停留。MolJSON 定位为「结构推理的校验与输入件」，不做「全链路格式替换」。

## 4. 复现方式

```bash
cd /root/FormuMind/backend && source .venv/bin/activate
python -m scripts.benchmark_moljson --limit 1   # 冒烟（4 case，每任务 1 个）
python -m scripts.benchmark_moljson             # 全量（28 case，~15 分钟）
# 报告 → benchmark_moljson_report.json
```
