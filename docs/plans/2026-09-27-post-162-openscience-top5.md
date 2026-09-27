# PR #162 之后：双 OpenScience 再借鉴 Top 5（综合评审）

> 状态：**方案评审**（2026-09-27）  
> 输入：本会话分析 + 另一 AI 方案（preflight / fix-loop / manifest / MCP→Skill / screening）  
> 基线：#161 Skills/Evidence/Connectors + #162 坞清理·安装·MCP 导入·slash·域包 已合入 main  
> 约束：配方闭环为本；不整仓移植；优先「钢印引用 / 可治疗审核 / 可审计检索」

---

## 0. 对另一方案的校正（相对代码真相）

| 另一方案说法 | 代码真相 | 结论 |
|--------------|----------|------|
| 「`+` 只能选 skills，选不进 MCP」 | FE 已有 `selectedConnectors`；BE `gather_connector_evidence` 只吃 **内置** literature/chemistry | 缺口是 **自定义 MCP server → chat**，不是「完全不能选连接器」 |
| Publication preflight 对齐 `citation_binder` | `citation_binder` + Wiki `scrub_unbound_citations` 已有 scrub，**无**落盘前 blocking 门 / override 账 | 主张成立；接线点应是 Wiki/dossier 发布 +（可选）Evidence 导出 |
| Reviewer 只诊断 | `evidence_reviewer.py` 确为 fail-open 诊断，无 fix-loop / disposition | 主张成立 |
| literature-manifest | AIPOCH 有完整实现；FM **无** turn 级 frozen corpus | 主张成立 |
| Smart screening | 双方一致；FM 无 | 主张成立 |

本方案另保留的「PaperQA 加固 / DOI enrichment / Provenance UI」不进本轮 Top 5 主线，降为 **随 Wave 附带或紧随其后的可靠性债**。

---

## 1. 综合排序逻辑

```
差异化钢印（preflight）
    ↑ 吃 fix-loop 的 findings + manifest 的 frozen corpus
补明确缺口且便宜（MCP→chat，但必须带审批）
新场景增量（screening）
可靠性债（PaperQA）不挡差异化，并行或 Wave 2
```

**必要性 × 可行性 × 与现栈咬合度** 三维打分后，得到下列 Top 5。

---

## 2. 最优 Top 5

### #1 Publication preflight（引用 + 数字检查门）— **先做**

| 维 | 评估 |
|----|------|
| 来源 | SynSci `file/review.ts` + `publication.ts` |
| 必要性 | ★★★★★ |
| 可行性 | ★★★★☆ |
| 难度 | 中 |

**做什么**

- 纯函数三组检查（可单测、可离线）：
  1. `[^n]` 无定义 / 越界 → **blocking**（复用 `citation_binder` + Wiki scrub 规则）
  2. `[citation needed]` / `TODO` / `TBD` 残留 → **blocking**
  3. 含数字·%·单位·p 值的行无近邻 `[^n]` → **major**（正则移植，可调）
- `assertReady`：归属项目 → content hash 未变 → 已跑 review → 无 open blocking（或已 override）
- Override：必填 `actor` + `reason`，写入事件账

**接线**

- 主闸：Wiki/dossier **落盘或导出**前（`storm_polish` / dossier publish）
- 副闸：Evidence 模式「导出报告」按钮（可选同期）
- Chat 保持 fail-open（只提示，不挡发送）——与另一方案一致

**为何压过 Provenance UI chips**：chips 是展示；preflight 是**强制门**。展示可作为 preflight findings 的 UI，不必单独立项抢第一。

---

### #2 Reviewer fix-loop（[Auditor] 修正循环）— **紧接 #1**

| 维 | 评估 |
|----|------|
| 来源 | AIPOCH `reviewer/reviewer-fix-loop-owner.ts` + correction |
| 必要性 | ★★★★★ |
| 可行性 | ★★★★☆ |
| 难度 | 中 |

**做什么**

- 现有 `evidence_reviewer` 产出 findings → 拼 `[Auditor]` 上下文 → **最多 1–3 轮**有界 regenerate（只重审失败/警告 blocks）
- disposition：`pass→resolved` / `warn|fail→reflag_count+1` / 超轮次 → `unaddressed`
- Chat：仍 fail-open；Wiki 落盘：`unaddressed` fail → 转成 #1 的 **blocking** 输入

**与 #1 关系**：#2 是治疗器，#1 是钢印门；顺序可「#1 MVP（只检查不修）→ #2 接入 → #1 吃 disposition」。

---

### #3 MCP→Skill 文档 + `selected_mcp_servers` 进 chat（**含审批门**)

| 维 | 评估 |
|----|------|
| 来源 | AIPOCH `skill-doc` 思路 + SynSci `permission/next.ts` |
| 必要性 | ★★★★☆ |
| 可行性 | ★★★★★ |
| 难度 | 小–中 |

**校正后的缺口**

- 内置 connectors：已通
- **自定义 MCP**：设置可导入，**chat 请求未选、未注入、未路由 tools/call**

**做什么**

1. MCP 启用/变更时：`tools/list` → 渲染 `data/skills/mcp-<id>/SKILL.md`（或 `resources` 缓存），走现有 skills 发现
2. `ChatRequest.selected_mcp_servers: list[str]`（与 `selected_skills` 平行）
3. FE：`+` 菜单 MCP 区勾选（可与 builtin connectors 分区）
4. 路由：只读工具 → 现有 `call_tool_readonly`；**risky/writeish → Ask 一次 / 会话 allowlist**（把我方原「MCP 审批分层」并入，避免裸接通）

**为何高于独立「审批分层」立项**：没有 chat 接通，审批无用户路径；接通而不审批则 #162 导入能力变风险面。

---

### #4 证据固化 manifest（Frozen corpus）

| 维 | 评估 |
|----|------|
| 来源 | AIPOCH `artifacts/literature-manifest.ts` |
| 必要性 | ★★★★☆ |
| 可行性 | ★★★☆☆ |
| 难度 | 中（埋点分散） |

**做什么**

- 每次检索/connector/KB augment 记录：`criteria`（query/源/时间窗）+ `itemIds` + `totalCount`
- 按 `(project_id, session_id, turn_id)` 聚合；Wiki 落盘时 canonical JSON + sha256 随版本不可变
- Preflight 增加可选检查：正文引用的 chunk/id ⊆ 本轮 manifest（Frozen corpus）

**排序说明**：合规地基，但**单独上线用户无感**；故排在钢印与 MCP 接通之后，作为 #1 的加强臂。MVP 可先只记 chat turn + STORM 检索，不追求全入口。

---

### #5 Smart Screening 轻量版

| 维 | 评估 |
|----|------|
| 来源 | AIPOCH `smart-collections.ts::assess` |
| 必要性 | ★★★☆☆～★★★★☆（有系统综述故事时升档） |
| 可行性 | ★★★★☆ |
| 难度 | 小–中 |

**做什么**

- 规则：`{description, inclusion[], exclusion[]}`
- `insufficient-evidence` 短路（无标题/摘要不调模型）
- 分类：`match | no-match | uncertain` + 简短理由；AI vs 人工覆盖分存
- 先 **preview 4 条** 验证 prompt，再批次

**边界**：不做完整 Literature Library / BibTeX 全家桶。

---

## 3. 未进 Top 5（明确处置）

| 项 | 处置 |
|----|------|
| 声明级 Provenance chips（我方原 #1） | **并入 #1 UI**：展示 findings，不单独立项 |
| PaperQA 流式/解耦（我方原 #3） | **Wave 1.5 可靠性债**：Evidence 模式投诉或 preflight 假阴性高时插队 |
| DOI/撤稿 enrichment（我方原 #4） | **并入 #1 major 规则 + scholar_helpers 加固**，不单独立项 |
| 独立 MCP 审批分层（我方原 #5） | **并入 #3** |
| RRF k=1 A/B、`field()` 诚实缺失 | 随手 / 现有 RAG 线，不占本 Top 5 |
| RO-Crate / Marketplace / Specialist | 继续不做 |

---

## 4. 推荐落地波次

```text
Wave A（1.5–2.5 周）  ★用户可感知差异化
  A1 Publication preflight MVP（Wiki 落盘闸 + override）
  A2 MCP→chat（selected_mcp_servers + skill-doc + risky Ask）
  A3 Reviewer 1-round fix-loop（Evidence 模式；findings 喂 A1）

Wave B（1.5–2 周）    ★合规与场景
  B1 literature manifest（chat+STORM 埋点 → 落盘 checksum）
  B2 preflight Frozen-corpus 检查臂
  B3 Smart screening preview → batch

Wave C（按需）
  C1 PaperQA stream + provider 解耦
  C2 DOI/DataCite 回退 enrichment
  C3 Evidence bench 金标
```

**默认锁定**：先 A，再 B；C 不挡合入。

---

## 5. 验收口径（摘要）

| 项 | 验收 |
|----|------|
| #1 | 故意缺 `[^n]` / 残留 TODO 的报告无法 finalize；override 后有 actor/reason 事件 |
| #2 | warn 断言经 ≤3 轮修复或标 unaddressed；unaddressed fail 挡 Wiki 落盘 |
| #3 | `+` 勾选自定义 MCP 后请求带 `selected_mcp_servers`；只读 tool 可调；writeish 需确认 |
| #4 | 同一 turn 检索 manifest sha 随 Wiki 版本；篡改引用 id 触发 preflight |
| #5 | preview 4 条产出 match/no-match/uncertain；人工覆盖不覆盖 AI 原始决策字段 |

---

## 6. 请产品确认的三句话

1. **钢印优先**：同意 Wiki/报告落盘走 preflight，Chat 仍 fail-open？  
2. **MCP 接通必须带 Ask**：同意自定义 MCP 进 chat 与审批同波交付？  
3. **Screening 是否 Wave B**：若近期无系统综述用户故事，可整体后移，把配额给 PaperQA 加固。
