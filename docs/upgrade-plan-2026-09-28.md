# FormuMind 下一步升级方案（基于 2026-09-28 代码审查剩余项）

> 状态：待 Cheng 审阅。审阅通过前不执行任何代码改动。
> 范围约束：化学价值链内（检索→解析→RAG→配方推荐→DOE→优化→知识管理→文档生成）；非化学数据源不进。

## 剩余问题盘点（审查报告中本轮未覆盖的）

| 编号 | 问题 | 级别 | 状态 |
|------|------|------|------|
| B-10 | screening evaluate 非法年份未映射 400，直接 500 | P2 | 未修 |
| B-11 | reviewer `_AUTO_STATE` 无界增长（无 TTL/上限） | P2 | 未修 |
| B-12 | MCP session 无生产级清理，子进程驻留 | P2 | 未修 |
| B-13 | Smart Collections PATCH 部分更新语义被破坏 | P2 | 未修 |
| B-15 | `search_round_deadline_s` 未在 Settings 声明，不可配置 | P2 | 未修 |
| B-16 | cross-encoder 每次 rerank 全量重载模型（GB 级） | P2 | 未修 |
| F-1 | 新建集合后 snapshot 字段空白（需手动刷新） | P2 | 未修 |
| F-2 | ReviewerAuditModal 未传 projectId → 返回全项目记录（数据越界） | P2 | 未修 |
| F-3 | ManifestDetailPanel `item_ids` 缺失时白屏（疑似） | P2 | 未修 |
| F-4~F-12 | 前端 P3：异步竞态、非受控组件、空 question、双击删除等 9 项 | P3 | 未修 |
| P-6 前端 | SourceDetailModal 600 chunk 全量渲染，无虚拟化 | P2 | 未修（后端 SQL 分页已做） |
| P-8 | experiments 搜索全表拉回 Python 扫描 JSON 列 | P2 | 未修 |
| P-9 | 聊天请求触发 MCP probe 疑似无缓存（疑似） | P2 | 未修 |
| 重复逻辑 | `_match_catalog`、`_cache_get/_put`×3、`_escape_like`×3、optimizer 适配器等 | P2 | 未修 |
| 依赖声明 | `neomodel` 声明但零 import；`neo4j` 被 import 但未声明 | P2 | 未修 |
| 审计遗留 | artifact 跨进程完整事务锁（本轮只锁了单次写）；`_doe_metadata` 迁移每次全表扫描 | — | 未修 |

## 方案：分 4 波

### Wave A — 正确性收尾（约 3–4 人天）
1. **B-10**：evaluate 加 `try: int(year)` → 400，与同文件另三个端点一致。1 测试。
2. **B-13**：PATCH 改 `exclude_unset=True`；filters 改 merge 语义（未提供字段保留）。3 测试。
3. **B-15**：Settings 声明 `search_round_deadline_s`，去掉 fallback 硬编码。1 测试。
4. **F-2**：ReviewerCard 透传 projectId 到 ReviewerAuditModal；后端缺 project_id 时拒绝而非返回全量（fail-closed）。2 测试。这是**数据越界**，优先级最高。
5. **F-1**：doCreate 后调一次 detail，或前端补 `snapshot_count=0/last_snapshot=null` 默认值。
6. **F-3**：`new Set(frozen.item_ids ?? [])`，加损坏数据测试。

### Wave B — 性能收尾（约 4–6 人天）
1. **P-6 前端**：SourceDetailModal 虚拟化（react-window 或 CSS content-visibility），600 chunk 渲染从 O(n) DOM 降到 O(visible)。性能测试：渲染时间断言。
2. **P-8**：experiments 搜索改 SQL `json_extract` 过滤；若查询模式支持，加 FTS5。注意 SQLite JSON 函数可用性先探针。
3. **B-16**：cross-encoder `_MODEL_CACHE`（单例懒加载 + 进程内复用）。注意多进程下每个 worker 一份是预期的。
4. **P-9**：先验证是否为真问题（加 probe 调用计数测试）；若真无缓存，按 server+generation 缓存 probe 结果。
5. **审计遗留**：`_doe_metadata` 迁移加版本标记（`schema_version` 表或 marker 文件），一次性执行，不再每次 registry 初始化全表扫描。

### Wave C — 健壮性（约 5–7 人天）
1. **B-11**：`_AUTO_STATE` 加 TTL（LRU + 过期清理线程）或上限（如 1000 条，超限逐出最旧）。内存泄漏类，长期运行服务必修。
2. **B-12**：lifespan shutdown 关闭 MCP sessions；`_reset_client_cache` 从"测试隔离用"升级为生产可用的清理路径；子进程退出超时后 kill。
3. **审计遗留**：artifact 跨进程完整"读→校验→写"事务锁。两条路二选一（需 Cheng 定）：
   - A：把 fcntl 锁提升到事务边界（finalize/restore 全程持锁）；
   - B：明确文档约束"artifact 服务单进程部署"，多 worker 场景走队列串行化。
   推荐 A，B 作为降级文档。
4. **依赖声明**：删 `neomodel`（零 import）；`pyproject.toml` 补 `neo4j` 声明，与 requirements.txt 对齐。

### Wave D — 前端 P3 打磨 + 重复逻辑（约 4–5 人天）
1. **F-4**：异步请求加取消/序号守卫（以 `TableBadges.tsx` 的 cancelled 模式为范本），覆盖 ReviewerCard / ManifestDetailPanel / LiteratureFreezeStrip / MemoryPanel。
2. **F-5**：LiteratureFreezeStrip select 改受控组件。
3. **F-6**：`STATUS_TONE[v.status] ?? STATUS_TONE.staging`。
4. **F-9**：review 直接通过时 toast 提示"无需修复"（后端返回 `{"rounds": 0}` 无 run_id，前端回退逻辑易困惑）。
5. **F-11**：question 为空时禁用"重审"按钮（防后端 400）。
6. **F-12**：doDelete 防抖。
7. **重复逻辑**：`_match_catalog`、`_cache_get/_put`、`_escape_like` 抽共享模块；optimizer 四个适配类 `best`/`ranked` 提基类。前端 NotebookLM 登录流程、radio 组、Hub pane 重复段抽组件。
8. **F-7/F-8/F-10**：顺手清理（非空断言、未展示字段、useCallback）。

## 不做 / 延后
- **P-10** connector 串行：目前仅 2 个 connector，影响可忽略，延后。
- **P0-16**（元数据先审后交）：Cheng 已决定先不做，保持不变。
- **MCP HTTP 传输**：按之前结论按需，不在本方案内。
- **4 个基线沙箱失败**（SSRF/DNS×3、httpx.InvalidURL×1）：沙箱环境限制，非代码问题，不修。

## 验证标准（每波）
- 根因分析先行；新增回归测试；后端全量零新增失败（基线对照）；前端 tsc + vitest 全绿；`git diff --check` 干净。
- commit/push 另行授权。

## 工作量汇总
| 波次 | 内容 | 估算 |
|------|------|------|
| A | 正确性收尾（含 F-2 数据越界） | 3–4 人天 |
| B | 性能收尾 | 4–6 人天 |
| C | 健壮性 | 5–7 人天 |
| D | 前端 P3 + 去重 | 4–5 人天 |
| 合计 | | **16–22 人天** |

建议顺序：A → B → C → D。A 可独立先行（F-2 数据越界建议最优先）。
