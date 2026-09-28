# FormuMind 下一步升级方案（2026-09-28 第二轮）

> 状态：待 Cheng 审阅。审阅通过前不执行任何代码改动。
> 前提：代码审查 P0/P1/P2/P3 已全部清零（除 4 个沙箱环境失败）；本方案转向产品价值升级。
> 范围约束：化学价值链内；非化学数据源不进。

## 现状判断（代码实测依据）

| 价值链环节 | 状态 | 依据 |
|-----------|------|------|
| 关键词检索 | 强 | OpenAlex 多 arm 并行、专利、streaming search |
| 文件解析 OCR | 强 | 分层 parser（pymupdf→rapidocr→markitdown…），中文 txt 已修 |
| RAG + LLM Wiki | 强 | FTS5、Smart Collections、Agent 记忆 |
| 配方推荐 | 强 | 推荐引擎 + BayBE |
| DOE 设计 | 强 | BayBE/Optuna/PyDOE 引擎，LHS 回退 |
| 自动优化收敛 | 中 | `auto_loop.py` + `doe_cycle_service.py`（184 行单函数）存在，前端有 LoopModal/DoeResultsPanel，但闭环深度待验证 |
| 知识管理 | 强 | Collections、Wiki、Memories |
| 文档生成 | 强 | 技术报告导出 |
| 证据压缩 tier2 | 未启用 | `query_compress_llm_enabled` 默认 False，已实现未上线 |

最薄的一环是**自动优化收敛的闭环深度**，其次是 **tier2 压缩的投产决策缺 eval 数据**。

## 方案：4 波

### Wave 1 — tier2 压缩 eval 门控上线（约 3–4 人天）
**目标**：用数据决定 tier2（LLM query-aware 重写）是否默认开启，而不是一直关着。

1. 在 golden 集（`backend/tests/golden_eval_dataset.py`）上跑 A/B：tier2 开 vs 关。
2. 对比指标：citation_veracity、coverage、numeric_consistency（rigor_rubric 现有三项）+ token 成本/延迟。
3. 决策矩阵：
   - 质量提升显著且成本可接受 → 默认开启（可配开关保留）；
   - 提升有限 → 保持关闭，记录结论；
   - 仅长 passage 有效 → 按长度阈值条件开启。
4. 新增 `test_tier2_ablation.py`，把结论锁进回归测试。

### Wave 2 — 闭环优化深度（约 6–8 人天）
**目标**：DOE → 执行 → 结果回填 → 下一轮推荐的完整闭环真正可操作。

1. 先做闭环审计：从 LoopModal 出发，走一遍 plan → `/doe/cycle` → 结果 → 历史 → 下一轮，列出断点（缺失的 API/前端状态/数据回填）。
2. 补断点（预计）：cycle 结果自动回填 experiment 行、收敛判断可视化（`loop_convergence_*` 配置已存在，查前端是否展示）、失败 cycle 的重试路径。
3. `doe_cycle_service.py` 目前 184 行单函数，按职责拆分（plan 构建 / 执行调度 / 结果收敛），每块加测试。
4. 端到端测试：mock 引擎跑完整两轮 cycle，断言第二轮利用了第一轮结果。

### Wave 3 — Eval 体系扩展（约 4–5 人天）
**目标**：让 rigor gate 从"有"变成"可信"。

1. golden 集从 12 组扩到 30+，新增 coating/腐蚀/配方优化场景（Cheng 的 VIANT 领域知识可在此落地）。
2. rigor gate 加趋势追踪：每次 CI 把三项分数写 JSONL，分数退化超阈值告警（不是 fail，而是 report）。
3. golden 集版本化：数据集变更走 review，避免静默改标准。

### Wave 4 — 生产就绪（约 4–6 人天）
**目标**：从"本地跑通"到"可部署"。

1. `docker-compose.yml` 生产 profile：后端/前端/redis 分离，healthcheck，restart policy。
2. SQLite 备份/恢复：一键备份脚本 + 恢复演练（数据目录挂载卷）。
3. 启动自检：缺必需配置时 fail-fast 并给出明确错误（而不是运行时 500）。
4. 文档：`docs/deployment.md` 一页部署指南。

## 不做 / 延后
- **P0-16**（元数据先审后交）：维持 Cheng"先不做"的决定。
- **MCP HTTP 传输**：维持按需。
- **P-10** connector 串行：影响可忽略。
- **4 个基线沙箱失败**：环境限制。

## 工作量汇总
| 波次 | 内容 | 估算 |
|------|------|------|
| 1 | tier2 eval 门控上线 | 3–4 人天 |
| 2 | 闭环优化深度 | 6–8 人天 |
| 3 | Eval 体系扩展 | 4–5 人天 |
| 4 | 生产就绪 | 4–6 人天 |
| 合计 | | **17–23 人天** |

建议顺序：1 → 2 → 3 → 4。Wave 1 最小且能最快产生决策价值，可独立先行。
每波验证标准不变：根因先行、回归测试、全量零新增失败、commit/push 另行授权。
