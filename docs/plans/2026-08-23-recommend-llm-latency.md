# recommend 路径提速 — 削减冗余 LLM 调用

> 日期：2026-08-23 ｜ 状态：已评审，**结论 = 不改**（见文末）

## 零、实施结论（2026-08-23 评审后）

- **A1（砍 CRAG 检索）已试并回退**：实测只省 ~3s（HyDE/rerank/grade 走 flash、每次 ~1s，
  非预判的「4-5 次贵调用」），且破坏了 `test_graph_survives_a_failing_federated_search`
  并改变产品语义（recommend 不再拉新文献）。得不偿失。
- **真正瓶颈 = 推荐 LLM 本身**：一次生成 `n=12` 候选（diversity 2× 过采样）×
  `deepseek-v4-pro` + tenacity 重试 ≈ 60s（隔离测量）/ 15-20s（测试内）。
- **A2（n 减半 / 换 flash）是真正的杠杆，但用户已决策：保持 pro + n=12，接受 ~15-20s**，
  推荐质量优先。故本方案暂不实施，留档备查。

## 一、根因（cProfile 实证，非猜测）

配方推荐 `run_research_graph(mode="recommend")` 实测 ~26s。用 cProfile 定位（111s
profile 时长），瓶颈分布：

| 调用点 | cumtime | 说明 |
|--------|---------|------|
| `llm.recommend_formulations` | 57s | 推荐 LLM（DeepSeek）|
| `_run_crag_retrieval` | 43.5s | CRAG 检索（**也在调 LLM**）|
| `retrieve_node` | 36.5s | HyDE + 子问题 + `llm_rerank` |
| `openai chat.completions.create` | 54.8s ×4 次 | 共 **4 次** LLM 调用，每次 ~13.7s，带 tenacity 重试 |

**结论：瓶颈是「recommend 模式走了完整 CRAG 检索」，做了 4-5 次 DeepSeek 网络调用。**
此前怀疑的 chemtools/PubChem、模型冷加载、离线 grounding **均未进 profile top40，全部排除**。

关键发现：代码里**已有**快速路径 `resolve_grounded_evidence()`（research_graph.py:640），
注释明确写着 *"Fast path for recommend: skip CRAG graph (HyDE, sub-questions, grading)
and use the active retrieval backend directly"* —— 直接 ColBERT 检索、**零 LLM 调用**。
但 `run_research_graph(mode="recommend")`（line 628）目前仍调用完整 CRAG
`_run_crag_retrieval` → `retrieve_node`(HyDE/rerank) → `grade_node`(LLM 评分)，
**没有用到这个快速路径**。

## 二、方案

### A1（推荐）：recommend 检索改走 ColBERT-only 快速路径

`run_research_graph(mode="recommend")` 的检索阶段跳过 HyDE / 子问题分解 / `llm_rerank` /
`grade_node`，直接 `colbert_store.search()` 取 top-k（复用 `resolve_grounded_evidence` 的
逻辑或 `_run_crag_retrieval` 加 `mode=="recommend"` 快速分支）。

- 效果：LLM 调用 4-5 次 → **1 次**（仅推荐 LLM），预期 ~26s → ~8-14s
- 证据质量：损失 LLM rerank/评分，但 ColBERT 语义检索分数对推荐场景足够（推荐 LLM 本身
  会再做一次语义理解）

### A2（可选叠加）：推荐 LLM 换 flash 模型

`recommend_formulations` 用 `deepseek-v4-flash`（非 pro），进一步 ~14s → ~3s。
与现有「文本 pro / 快速路径 flash」的模型分层一致。

### A3（可选）：收紧 tenacity 重试

4 次调用带 tenacity 重试（profile 显示 3 个 wrapped_f 各 ~19.9s），说明偶发重试放大了
延迟。确认重试语义后收紧（如 2 次→1 次）或对 recommend 路径关闭重试。

## 三、文件变更清单

| 文件 | 改动 |
|------|------|
| `app/pipeline/research_graph.py` | `_run_crag_retrieval` 或 `run_research_graph` 加 recommend 快速分支（ColBERT-only）|
| `tests/test_recommend_graph_fast.py` | 预算可从 45s 收紧回 ~15-20s（更贴近「快速」本意）|

## 四、实施步骤

1. `_run_crag_retrieval` 加 `mode=="recommend"` 快速分支：`colbert_store.search()` 直取 +
   pre_index 合并（镜像 `resolve_grounded_evidence` 的合并逻辑）
2. 补/改单测：recommend 模式断言不再触发 `llm_rerank`/`grade` LLM（mock 计数）
3. 收紧 `test_recommend_graph_fast.py` 预算，跑全量回归
4. commit + push（SSH）

## 五、风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 证据质量下降（无 LLM rerank）| 中 | 推荐相关性略降 | ColBERT 分数足够；保留 deep 模式完整 CRAG |
| 推荐 LLM 拿到的证据少/杂 | 中 | 推荐成分偏差 | 提高 colbert_top_k 补足候选量 |
| 行为与 deep 模式不一致 | 低 | 用户感知差异 | 明确文档化 recommend=轻量语义 |

## 六、验收标准

- `test_recommend_graph_fast.py` 预算收紧至 ≤20s 且稳定通过（连续 5 次）
- 全量套件全绿
- profile 确认 recommend 路径 LLM 调用次数 4-5 → 1
