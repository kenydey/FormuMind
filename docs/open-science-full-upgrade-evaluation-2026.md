# FormuMind OpenScience 全面升级评估（2026-09-27，v2 核心价值对齐版）

## v2 修订说明

v1 共 68 个候选。本次按 Cheng 明确的核心价值过滤：

- **核心链**：关键词搜索资料 → 解析文件（OCR 等）→ RAG 知识库 + LLM Wiki → 按技术要求推荐配方 → 按关键因子设计 DOE → 自动寻优、收敛优化 → 知识库管理 → 文档生成。
- **数据源约束**：非化学类数据源不新增；只用原有数据源（OpenAlex / EPO / arXiv 等既有融合）。
- **移除 12 项**：学术出版向（引用格式化/CSL/BibTeX、Zotero、Research contract、ScholarEval）、新增非化学数据源（撤稿检查、PubMed/Crossref 降级链新增源）、通用 UX（通知收件箱、对话导出、@提及、Bookmarks、usage accounting、alt-text）。
- **合并重构 2 项**：P1-9/P1-25 的学术导出部分合并为"技术报告导出 DOCX/PDF"。
- **保留 55 项**：A 类核心链条 39 项，B 类支撑 infra（服务于核心链的 agent/MCP/版本基建）16 项。

## 参考仓库覆盖盲区（重要）

两个参考仓库都是学术文献平台，**以下核心链环节无任何参考材料，需 FormuMind 原生开发**，不在本次借鉴范围内：

- 按技术要求推荐配方的算法/策略
- 按关键因子设计 DOE 的方法
- 自动寻优、收敛优化的闭环

本次清单覆盖的是"证据与知识"侧：让配方推荐与 DOE 的**依据更严谨、来源可追溯、知识可沉淀、文档可生成**。

## 评分说明

- **N**=必要性（对核心链的价值），**F**=可行性，1–5；**人天**含测试。
- 原 ID 保留以便与 v1 及原始盘点对应；`(B)` 为支撑 infra。

---

# A 类：核心链条（39 项）

## A1 关键词搜索资料（8 项）

| ID | 项目 | 来源 | 机制（一句话） | N/F | 人天 |
|---|---|---|---|---|---|
| P0-3 | literature-review 循环协议 | synthetic SKILL.md | 检索预算20、缺口驱动重搜、覆盖率停止条件、防重复 query | 5/5 | 0.5 |
| P0-10 | MMR 多样性重排 | 自研（两仓库皆无） | 融合检索后 MMR（λ≈0.7）权衡相关性/多样性，防同课题组近重复 | 4/5 | 2–3 |
| P0-13 | SearchDedupe 会话内缓存 | synthetic search | 同 query+选项一次运行内命中缓存直接重放，省 30–50% 检索调用 | 4/4 | 3 |
| P0-19 | citedBy 按论文年龄归一化 | synthetic | 引用量除以年龄/按年份分位，新论文不被老论文淹没 | 3/5 | 1 |
| P0-18 | 搜索过滤维度 + 日期绝对校验 | synthetic search | 日期/域名过滤；LLM 输出日期做绝对校验（防引用未来论文类幻觉） | 3/5 | 2–3 |
| P1-7 | search→read 两段式 + passages | synthetic | search 只回元数据+摘要；read 按需取全文返回带页码 passage | 4/4 | 5 |
| P2-1 | query-aware 证据压缩 | 自研（两仓库仅雏形） | 用 query 重写长文本，只保留相关 passage 再喂主模型；增量创新主战场 | 4/3 | 10+ |
| P2-8 | SearchOutput 渐进式裁剪 | synthetic | 检索输出按 token 预算裁剪，高价值优先，不断 prompt | 2/4 | 3 |

## A2 解析文件 / RAG（3 项）

| ID | 项目 | 来源 | 机制（一句话） | N/F | 人天 |
|---|---|---|---|---|---|
| P1-6 | PDF 全文 chunk 级 FTS5 索引 | aipoch full-text-index.ts | 独立 SQLite；chunk 保留页码+字符偏移+章节标题；bm25（标题权重2.0）；TTL 回收 | 4/5 | 3–5 |
| P1-18 | PDF 表格结构抽取契约 | aipoch pdf-structure.ts | 严格 schema：figure/table/algorithm + caption provenance；**优先化学版式：配方表、性能对比表、TDS/SDS 表格** | 4/3 | 8–15 |
| P0-6 | 文献去重（union-find） | aipoch duplicates.ts | DOI/PMID/PMCID/arXiv 精确键 + 标题年作者复合键；同 scheme 标识符冲突拒绝合并 | 5/5 | 1–2 |

## A3 知识库管理（14 项）

| ID | 项目 | 来源 | 机制（一句话） | N/F | 人天 |
|---|---|---|---|---|---|
| P0-7 | smart input digest 跳过重评 | aipoch smart-evidence.ts | 输入签名 sha256（title+abstract+附件版本）；未变则 deferred 跳过，commit 前再验防 race | 5/5 | 1 |
| P0-4 | insufficient-evidence 短路 | aipoch smart-collections.ts | 无标题 / 无摘要无 passage / >48000 字节 → 不调模型直接 uncertain | 4/5 | 0.5 |
| P0-8 | 人工 override AI 判定 | aipoch schema | (collectionId+itemId) 主键的人工决策优先于 AI；refresh 跳过；可清空 | 4/5 | 1–2 |
| P0-16 | 元数据"先审后交" | aipoch metadata-enricher.ts | search 返回 diff+reviewToken；commit 校验 token 与 metadataRevision，未变才写 | 3/5 | 1–2 |
| P1-21 | 筛选规则 preview 4 条试跑 | aipoch smart-collections.ts | 规则编辑时取前 4 条试跑，复用同一 assess()，返回 verdict+概率+token，不写 batch 表 | 4/4 | 2 |
| P1-22 | 筛选规则不可变版本 | aipoch schema | 规则改动递增 revision 并快照；run 绑定创建时 revision；中途变更中止 | 4/4 | 3–5 |
| P1-23 | 文献入库 opt-in 自动更新 | aipoch smart-collections.ts | collection 级 autoUpdate；入库 750ms 防抖 refresh；digest 未变跳过；200次/轮、1000条/天上限 | 4/3 | 3–5 |
| P1-36 | policyKey 配置变更失效 | aipoch smart-collections.ts | run 绑定 [serviceId, modelId, adapter, baseUrl, 版本]；中途变更中止，已有结果标 obsolete | 3/4 | 1 |
| P1-24 | Manifest 前端详情面板 | aipoch ArtifactSourcesPanel.tsx | 引用列表+详情弹窗；"Frozen review corpus" coverage 七格统计（诚实声明：计数=返回给 Agent 的内容，非已读完）；换样式→预览→存新版；条目不可手改 | 4/4 | 5–8 |
| P1-4 | 版本级证据冻结（增量） | aipoch literature-manifest.ts | artifactVersionId 1:1 绑定 manifestJson+checksum；evidenceJson/executionSnapshotJson 随版本冻结。**方案前核实与 Wave B Frozen Manifest 的重叠** | 5/4 | 6–10 |
| P1-16 | Artifact 版本状态机 | aipoch schema | Lineage（逻辑文件）1—N Version（不可变快照）；staging→pending→finalized；basedOnVersionId 派生图 | 4/5 | 6–8 |
| P1-14 | 版本 diff | aipoch diff-task.ts | 独立 worker；先行后字符二次对齐高亮；超限降级 | 4/5 | 3–4 |
| P1-37 | 版本回滚规范 | 反向发现（对方无） | 回滚 = 基于旧版本新建版本（basedOnVersionId 指旧版）；前端"恢复为新版"+后端 copy-on-write | 3/5 | 2–3 |
| P2-3 | Smart Collections 完整版 | aipoch 全套 | batch run 状态机（queued/running/…+per-item checkpoint+resume 校验+4 槽并发）；full-text evidence mode；live progress；P0-7/P1-21/22/23 已拿走高 ROI 部分，剩余为重型 batch 基建 | 3/3 | 15–25 |

## A4 配方推荐 / DOE / 寻优的证据与严谨性侧（10 项）

| ID | 项目 | 来源 | 机制（一句话） | N/F | 人天 |
|---|---|---|---|---|---|
| P0-1 | reviewer 反幻觉三规则 | aipoch rubric.ts §5.7/5.8/5.9 | 查不到≠伪造；伪造引用是唯一 not-found 定罪例外；reviewer 证据只能引实际读到的记录 | 5/5 | 1.5 |
| P0-2 | reviewer 判定臂与边界 | aipoch rubric.ts §5.2–5.6/5.10/5.11 | 八条 fail 判定臂（配反例）；只看本轮请求+有效计划；工件严格/散文宽松；单次提交即停 | 5/5 | 2 |
| P1-27 | Review 防自证 | synthetic | review 结论不得引用被 review 对象自身的陈述作证据（可并入 P0-1/2 一起做） | 3/4 | 3 |
| P1-2 | Provenance 图 + lineage | synthetic | artifact/run/source/claim 四类节点有向图；**核心用途：配方结论 ← 文献/实验数据的 lineage 查询**；metadata 保留字段写保护随做 | 5/4 | 5–8 |
| P2-10 | provenance 保留字段写保护 | synthetic | source_url、retrieved_at 等保留字段应用层写保护，防 agent 篡改证据来源（随 P1-2） | 3/5 | 0.5 |
| P0-11 | save/run 自动登记 | synthetic ArtifactFile | 每次 save/run 自动登记 {version, timestamp, parents, params, env}；run 产出自动成新版。**核实现有写入路径是否已登记** | 4/5 | 2 |
| P0-12 | ArtifactFile.audit 八项检查 | synthetic | 依赖声明/随机种子/输入校验/输出 schema/版本 pin/环境快照/参数完整性/产物可定位 | 4/5 | 3 |
| P1-34 | output-receipts 快照 | synthetic | 每次 run 在文件系统留输入 hash、输出清单、时间戳，支持事后审计与复现（与 P1-2 联动） | 3/3 | 5 |
| P1-35 | 复现报告模板 | synthetic | 一键生成环境/依赖/参数/输入 hash/输出校验的 Markdown 模板（与 P0-12 联动） | 3/5 | 1 |
| P1-15 | Session Plan | aipoch plan-service.ts | agent 提交结构化计划（phase/step）；pending→approved/rejected 审批不可逆；前置 phase 未完成后续禁流转；落盘 JSON+sha256。**长任务（DOE 设计、多轮寻优）必备** | 4/4 | 6–8 |

## A5 文档生成（4 项）

| ID | 项目 | 来源 | 机制（一句话） | N/F | 人天 |
|---|---|---|---|---|---|
| P0-14 | preflight 状态机 | synthetic Preflight | finding 生命周期 open→resolved/override（含理由）→finalize；产物变更自动标 stale。**核实现有 wiki.py preflight 是否已有** | 4/4 | 5 |
| P0-15 | 引用定位器 | aipoch artifact-literature.ts | citation 可选 locator（page/figure/table/…），随 manifest 冻结 | 3/5 | 1 |
| P0-20 | 导出 readiness 门控 | synthetic | 导出前检查：manifest 冻结了吗 / 引用都解析了吗 / 有未解决 finding 吗；不满足阻断并提示。**核实 ro_crate_export.py 是否已有** | 3/5 | 1–2 |
| P1-9' | 技术报告导出 DOCX/PDF | aipoch 改写（去学术化） | 配方报告/DOE 报告/优化报告导出 DOCX/PDF/HTML（pandoc 管线）；**去掉 Zotero 字段、LaTeX bundle、BibTeX/CSL 学术部分** | 4/4 | 4–6 |

---

# B 类：支撑 infra（16 项，服务于核心链）

| ID | 项目 | 来源 | 机制（一句话） | N/F | 人天 | 约束备注 |
|---|---|---|---|---|---|---|
| P0-5 | Project Agent Context | aipoch | 项目表存自由文本指令，session 启动作 system-prompt append；失败 fail-closed | 5/5 | 2–3 | — |
| P1-1 | Agent 记忆系统 | aipoch memory | 记忆表（全局/项目/about-you）；FTS5+bm25 auto-recall（6000 字符预算）；写入门控（拒密钥/拒注入/幂等）。**可直接上向量+FTS5 混合反超对方** | 5/5 | 8–12 | 分两期 |
| P0-9 | skill-doc 渲染层 | aipoch skill-doc.ts | MCP 工具列表渲染为标准 SKILL.md；启动+变更时同步；失败删旧 doc | 5/5 | 3–5 | 仅化学类 MCP server |
| P0-17 | MCP 超时/重试 | 反向发现（对方无） | 默认超时 60s + 指数退避；错误分类上报。直接超越 | 3/5 | 1–2 | — |
| P1-30 | MCP 连接健壮性 | aipoch client-manager.ts | 单 server 单 Client 缓存；并发去重；generation 屏障；失败分类；stderr 脱敏 | 3/5 | 3–5 | — |
| P1-3 | MCP 逐工具审批三态 | aipoch approval-broker.ts | Allow/Ask/Block（block>ask>allow）；Ask 挂起 5 分钟自动 deny；决策可按 once/session/project/global 持久化 | 5/4 | 8–12 | 纯后端门禁约 4–5 天 |
| P1-5 | MCP HTTP 传输 | aipoch | streamable_http/SSE；自定义 fetch（≤5 跳、强制同源）；URL 安全门（HTTPS/loopback） | 4/4 | 5–8 | 按需：有化学 HTTP MCP 才做 |
| P2-2 | MCP OAuth | aipoch oauth-client.ts | PKCE/刷新走 SDK；token 加密持久化；多 server 可共享 credential | 4/3 | 12–20 | 等 P1-5 后 |
| P1-11 | reviewer 工具白名单 | aipoch bridge-tools.ts | reviewer 会话仅 4 个只读工具，id 逐个校验；杜绝审计者篡改现场。**核实现有 evidence_reviewer 工具边界** | 4/4 | 3–5 | — |
| P1-12 | 自动审计+幂等+抑制 | aipoch workspace-events.ts | turn stop 100ms 防抖触发（默认关、按会话 opt-in）；per-turn 原子幂等；修正轮一次性抑制防自循环 | 4/4 | 3 | 核实 fix-loop 触发方式 |
| P1-13 | lifecycle/outcome 状态机 | aipoch reviewer.ts | running/complete/error × pass/flagged/null；runReview 永不抛错；产物被改标 stale | 4/4 | 2–3 | 核实 fix-loop 持久化 |
| P1-19 | Reviewer 独立小模型 | aipoch model-runtime-owner.ts | reviewer 可配便宜小模型；失败明确报错；Review 行记 model 标签审计成本 | 4/3 | 3–5 | — |
| P1-26 | finding disposition | aipoch reviewer.ts | fix loop 每轮写 disposition 行（resolved/unaddressed/still_open），终态可查 | 3/4 | 2 | — |
| P1-29 | stale review 检测 | aipoch stale-reviews.ts | 加载重算 scope（含 artifact digest）；被改过标 stale 撤下结论；读失败记"未验证" | 3/3 | 2–3 | — |
| P1-28 | Reviewer 前端卡片+审计页 | aipoch ReviewerCard.tsx | 会话内嵌卡片（warn/fail 计数）+ 独立审计页（action log viewer、stale 提示、re-run） | 3/4 | 3–5 | — |
| P1-38 | evals 严谨性 rubric | synthetic evals | 引用真实性/覆盖率/数值一致性等维度的回归 eval，锁 prompt 与模型改动 | 3/3 | 5 | — |

---

# X 类：已移除（12 项）

| 原 ID | 项目 | 移除理由 |
|---|---|---|
| P1-20 | 撤稿检查 | 依赖 Retraction Watch / Crossref 等**新增非化学数据源** |
| P1-10 | 元数据多源降级链（新增源部分） | Crossref / PubMed / DataCite / PMC / EuropePMC 为非化学新增源；失败隔离思想可在既有源上复用，不单独立项 |
| P1-8 | 引用格式化 APA/BibTeX | 学术出版向；核心"文档生成"指配方/DOE/优化技术报告 |
| P1-17 | BibTeX/RIS 双向 + CSL 渲染 | 同上；Zotero 互操作非化学平台刚需 |
| P1-31 | 通知收件箱 | 通用 UX，偏离核心链 |
| P1-32 | 对话导出 Markdown/PDF | 通用 UX；技术报告导出已由 P1-9' 覆盖 |
| P1-33 | Composer @ 提及补全 | 通用 UX |
| P2-4 | Research contract 预注册 | 学术向（防 HARKing），偏离 |
| P2-5 | ScholarEval 8 维度打分 | 学术主观质量打分，偏离 |
| P2-6 | Bookmarks | 通用 UX |
| P2-7 | Usage accounting | 通用计费统计，偏离 |
| P2-9 | figure alt-text 检查 | 出版无障碍规范，偏离 |

（P1-9/P1-25 的 Zotero 字段、LaTeX bundle、BibTeX/CSL、PPTX 学术部分已剔除，重构为 P1-9' 技术报告导出。）

---

# 修订后的实施顺序

- **第 1 波（约 1–1.5 周）**：P0-1/P0-2（rubric 进 evidence_reviewer，3.5 人天）+ P0-3（循环协议）+ P0-5（Project Context）+ P0-6（去重）+ P0-7/P0-4/P0-8（digest+短路+override，知识库质变）+ P0-9（skill-doc）+ P0-10（MMR）+ P0-13（SearchDedupe）+ P0-12（audit）+ P0-14（preflight 状态机）。
- **第 2 波（约 4–8 周）**：P1-1（记忆，分两期）+ P1-3（MCP 审批）+ P1-2（provenance 图，配方 lineage）+ P1-6/P1-7（FTS5 全文索引+两段式）+ P1-18（化学表格抽取）+ P1-11/12/13（reviewer 加固）+ P1-9'（技术报告导出）+ P1-15（Session Plan，服务 DOE/寻优长任务）。
- **第 3 波（路线图）**：P1-4（版本级证据冻结）+ P1-16（版本状态机）+ P1-5/P2-2（MCP HTTP/OAuth，按需）+ P2-1（query-aware 压缩，增量创新）+ P2-3（Smart Collections 完整版）。
- **先核实再定（方案阶段一次精确代码核查）**：P1-4 vs Wave B Frozen Manifest；P1-24 vs LiteratureFreezeStrip；P0-11/P0-14/P0-20 vs 当前 preflight/ro_crate 实现；P1-11/12/13 vs evidence_reviewer/reviewer_fix_loop 现状。

---

# 附：原始报告位置

- synthetic-openscience 完整盘点：`/tmp/code-review/synthetic-full-inventory-main.md`（子代理原始报告 `/tmp/code-review/synthetic-report1-*.md`、`report2-*.md`、`report3-*.md`）
- aipoch-open-science 完整盘点：`/tmp/code-review/aipoch-full-inventory.md`（子代理原始报告 `/tmp/code-review/aipoch-report1-mcp.md` … `report4-reviewer.md`）
- v1 评估文档（未过滤版）：本文件 v1 内容已被 v2 替代；如需回看可从 git 历史找回。
