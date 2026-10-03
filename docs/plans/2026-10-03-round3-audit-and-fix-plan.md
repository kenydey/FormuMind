# 第三轮全面审查：缺陷修复、核心功能评分与剩余问题修复方案

日期：2026-10-03　基线：`main @ 44afae0`（含已合入的 Literature Library）
范围：后端（FastAPI / Celery / SQLite / KG / 推荐 / DOE / 寻优）+ 前端（React / Zustand）+ 部署文件

> 评分是对"代码 + 测试 + 探针"的主观判断，没有真实数据集、线上流量或人工盲评作依据；
> 数字的价值在于**前后对比**和**每一分差在哪**，不要当作绝对质量。

---

## 1. 这一轮怎么查的

只靠"跑测试 + 读代码"在前两轮已经找不到更多东西了，这一轮换了 8 种交叉手段，每种都会暴露一类测试看不见的问题：

| 手段 | 能抓到什么 | 本轮命中 |
|---|---|---|
| pyright 属性扫描 | 访问不存在的字段 / 导入不存在的名字 | `KGRebuildReport.relations_upserted`、`session_scope`、`response.formulations` |
| 吞异常追踪插件 | `except Exception` / `degrade_return` 把编程错误吞成一行 debug 日志 | 上面三处 + `rebuild_all` 半途而废仍报"成功" |
| 死配置扫描（AST） | Settings 里有开关、界面有按钮、代码没人读 | 6 个死开关；新增守卫测试 |
| 环境变量交叉校验 | 代码 / compose / 脚本引用了启动审计不认识的 key | `FORMUMIND_RULES_DIR/DATA_DIR`、5 个部署用 key |
| 假数据漂移检查 | 测试里手写的响应结构和真实模型不一致 | 3 处旧 fake；`grounded_evidence`、`scored` |
| 事件循环阻塞扫描 | `async def` 里直接跑同步 I/O | chat 流、结构识别（180 s） |
| 出网追踪 + 硬隔离 | 单测偷偷访问公网（PubChem / OpenAlex / DuckDuckGo） | 数十个用例，现在默认禁止出网 |
| 前端交叉扫描 | 路由—封装—界面是否接通、鉴权头、异步竞态、加载态、key | 见 §2.2（10 项） |

---

## 2. 本轮已修复

### 2.1 后端（带回归测试）

| 域 | 问题（症状 → 根因） | 提交 / 位置 |
|---|---|---|
| 知识库 | **每个 chunk 都被"提到"30 种目录材料**：`name not in text and zh_name not in text` 中，`zh_name` 为空时 `"" not in text` 恒为 False。46 种目录材料里 30 种没有中文名 → Xylene、Titanium dioxide、Talc、Deionized water… 出现在所有文档的所有分块里，KG 实体、提及计数、枚举式 RAG 的实体引用（开启关系抽取时还有关系）全部被污染。自 KG-0 引入起，默认设置（`kg_enabled` + `kg_entities_on_ingest` 均默认开）下每次入库都会触发 | `entity_linker._link_catalog_in_text` |
| 知识库 | KG 元素扩展图在 `backend/` 之外的工作目录下恒为空（回退路径少了一层目录：`parents[1]` → `parents[2]`） | `kg/element_map.py` |
| 知识库 | `rebuild_all` 在第一个来源后 `AttributeError`，被宽泛 except 吞掉，返回"看似成功"的半份报告；`link-source` / 重建还依赖"入库时"的实体开关 | `1d5d4f7` |
| 知识库 | `kb_ingest_audit` 主存储从未收到过一行（导入了 db/session 里不存在的 `session_scope`，ImportError 被吞，一直走 JSONL 回退） | `2f40260` |
| 寻优 | 标量寻优（numpy / optuna / BoTorch）不使用实验室实测，且 `measurement_source` 按"入参"而非实际使用情况标注 | `78cbc5f`（新增 `lab_points_used`） |
| 寻优 | `auto_retrain` 无人读取，每次提交都重训；现在能否决自动重训，响应里会告知（显式 `POST /api/train` 不受影响） | `1d5d4f7` |
| 配方推荐 | 严格 grounding 删掉溶剂 / 填料 / 颜料 / 通用助剂 → 配方只剩 72%、预测 VOC=0，被"最小化 VOC"目标奖励；一词名称（如 Benzotriazole）和中文文本永远无法被证据"落地" | `7f8b527` |
| 配方推荐 | DOE 循环读 `.formulations`（真实字段叫 `scored`）→ AttributeError 被吞 → 每轮白付一次推荐调用，Top-12 候选从未生效；异步推荐读不存在的 `grounded_evidence` → KB 回填永远收不到证据 | `d0c463c` |
| 配方推荐 | 每个配方 ~15 条"预测值缺少证据"告警（每个指标一条，× 每个候选） → 合并为每个配方一条，重复复核替换而不是叠加 | `claim_checker` |
| 配方推荐 | 相似实验查询加载全部领域的全部实验行 → 改为按领域过滤、最新 2000 行 | `27780ac` |
| 问答 | 流式问答里 `_finalize_evidence_fields`（Crossref + LLM 审稿 + 修复循环，数秒阻塞 I/O）直接跑在事件循环上 → 其间整个 worker 无响应 | `asyncio.to_thread` |
| 问答 | 会话存储不可用时 `save_session` 的 503 被通用 `except` 吞成 500 | `session.py` |
| 解析 | 结构识别在 OCSR 未启用时仍投递到没有消费者的队列并等满 180 s（且阻塞事件循环）；现在未启用即刻返回说明，启用时在线程池里等 | `structure_recognize.py`、`chemistry.py` |
| 解析 / 安全 | 大多数 multipart 端点先 `await file.read()`（QC、MCP 导入、材料导入、技能安装、导出架、3 个实验端点），不限上限 → 统一走"1 MiB 分块 + 超限 413"的读取器 | `323b0c6` |
| 安全 | SSRF 防护漏掉 CGNAT `100.64.0.0/10`（阿里云元数据 `100.100.100.200` 在其中） | `2f40260` |
| 检索 / 文献库 | 引用定位器（页 / 图 / 表）只有读没有写；集合只能建不能改名、删除 | `baf5819`、`60d8146` |
| 报告 | 发布预检阻断导出（409），界面却没法查看原因 / 放行；STORM 导出把整段 JSON 当错误信息 | `6bc3025`（新增 resolve 接口 + 预检面板） |
| 配置 | `RULES_DIR/DATA_DIR` 等被代码直接读取的 key 在开发环境（默认）里让启动失败；部署文件里的 5 个 key 同理 | `0f64aaa` + 本轮；新增守卫测试 |
| 工程 | 6 个死开关（`auto_retrain`、`kg_link_on_ingest`、`chat_composer_plus_enabled`、`chat_rerank_enabled`、`pdf_download*`）：接通或删除；旧 `.env` 仍被容忍；新增"每个 Settings 字段必须有读取者"守卫 | `1d5d4f7` |
| 工程 | 单测默认**禁止出网**（socket + httpx + requests + urllib 四层，`@pytest.mark.network` 才放行）；进程级网络结果缓存（熔断器 / 检索缓存）每个用例前复位 | `tests/conftest.py` |
| 工程 | 测试后台线程泄漏：`eager-*`、`wiki-compile`、`workbench-loop` 线程在测试结束后仍在跑（一个 NSGA-II 反向设计任务能拖 20 s+），与后续用例的全局 `httpx.Client` monkeypatch 冲突——`test_ingest_url_skips_blocked_without_network` 在完整运行里稳定失败、单跑却通过。现在测试结束时有界等待（≤ 60 s）这些线程并清 Settings 缓存。**教训**：第一版把等待放在环境变量还原**之前**，后台任务趁机把"鉴权开启"的 Settings 钉进缓存，下一个模块全部 401（`test_tasks_owner` → `test_tasks_sse` 可 3/3 复现）；等待必须在还原**之后** | `tests/conftest.py` |
| 工程 | ruff 门禁新增 `F401`（应用代码），清掉 36 处未使用导入；有意的再导出标注 `# noqa: F401` | `pyproject.toml` |

### 2.2 前端（带回归测试）

| 问题 | 症状 / 根因 |
|---|---|
| 下载不带鉴权 | `<a href="/api/...">`、`window.open()` 无法携带 `Authorization`，**开启 API 鉴权后** 材料导出、DOE 导出、轮次导出、附件下载、成果架下载全部 401 → 统一 `downloadWithAuth` + `AuthDownloadLink`，失败会提示；blob URL 延迟 40 s 再回收（立即回收会取消 Safari/Firefox 的下载） |
| 切换项目丢编辑 | `loadProject` 先置 `projectLoading` 再调 `saveProject`（后者遇到该标志直接返回）→ "先保存要离开的项目"是空操作，最近 1.5 s 的编辑丢失 |
| **项目串数据** | "空 payload 保护"拿**内存里上一个项目**的 sources / chat 去填新项目的空 payload → 打开一个新建 / 未检索过的项目会带上上一个项目的资料和对话，下一次自动保存把它们写进新项目 |
| 项目切换残留 | 会话列表、检索状态、过滤报告、上传 / 校验告警等切项目后仍显示上一个项目的内容（`createProject` 清了，`loadProject` 没清） |
| 保存去重漏字段 | 脏检查指纹只覆盖 ~35 个字段中的 9 个：仅改检索词、勾选来源、引擎等的修改被判"未变化"，从不 PUT |
| `AttachmentPreview` 请求死循环 | `load` 依赖父组件传入的内联 `onChanged`；`load` 完成又调 `onChanged` → 父级 state 变 → 新回调 → `load` 再次执行……对话框打开期间无限请求（测试复现：超时） |
| 可编辑配方表丢焦点 + 监管标签错位 | `<tr key={name-idx}>`：在名称输入框里每敲一个字符行就重新挂载，焦点丢失；已解析的"🔒 专利 / ⚠ 管制"标签按行号保存，配方被替换后显示在**另一种材料**上 |
| `KgRelationPanel` 加载态卡死 | 请求进行中把检索词清空 → 取消的请求不会再关 `loading`，面板永远"加载中…" |
| 文献库竞态 | 切换项目后上一个项目的慢响应可覆盖新项目列表；集合过滤器沿用上个项目的 id → 空库 → 组件按项目重新挂载 + 只接受最新请求 |
| 版本历史 / 成果架竞态 | 回滚按钮作用于**显示的**版本号：切项目时旧项目的慢响应可能出现在新项目上，一键回滚会把别的项目的版本号套到当前项目 → 只接受最新请求 + 切换即清空 |

---

## 3. 七项核心功能评分（满分 10；上轮 → 本轮）

| 功能 | 上轮 | 本轮 | 变化来自 | 距 9 分还差什么 |
|---|---|---|---|---|
| 资料检索 | 7.0 | **7.2** | SSRF 补 CGNAT；文献库补齐定位器写入 / 集合改名删除；文献库切项目串数据、竞态已修；单测不再偷偷访问公网 | 检索质量没有基准集（召回 / 精度从未量化）；SSRF 仍有 DNS 重绑定窗口；限流下的降级只靠 mock 验证；主题雷达（`topic_sweep`）没有任何触发入口 |
| 文档解析 | 7.5 | **7.7** | 所有上传统一限长（413）；结构识别未启用时不再空等 180 s、不再冻结事件循环 | CI 里没有真实文档基准（表格 / 公式 / 扫描件的抽取准确率）；docling 与其他依赖冲突需分镜像；OCSR 默认关闭、需独立 worker |
| 知识库构建 | 7.5 | **8.0** | 修复"30 种目录材料出现在所有分块"这一 KG 污染；`rebuild_all` 不再半途静默失败；审计表真正落库；`kg_link_on_ingest` / 元素图路径接通 | 嵌入向量路径在 CI 没有覆盖；KG 实体链接没有精度 / 召回评测；死表 `kg_formulation_links`；增量重建语义 |
| 问答能力 | 7.0 | **7.3** | 见下面三项 | |
| └ 检索 | 7.5 | 7.7 | KG 实体引用不再被 30 个无关材料淹没；证据收尾（Crossref + 审稿）移出事件循环，流式不再卡顿 | 无检索评测集；非 OpenAI 兼容 provider 仍不能真流式 |
| └ 理解 | 7.0 | 7.0 | — | 改写 / 澄清没有评测集；歧义消解依赖 KG 质量（刚修好，需观察） |
| └ 上下文 | 6.5 | 7.2 | 会话存储不可用时返回 503 而非 500；切项目不再把上一个项目的资料 / 对话 / 会话列表带过来；切换前先保存 | 历史截断 / 摘要策略没有量化；会话持久依赖 Redis，缺失时只降级为进程内 |
| 配方推荐 | 7.8 | **8.3** | 严格 grounding 不再删掉溶剂 / 填料 / 颜料（配方曾只剩 72%、VOC 预测为 0）；一词名称与中文证据可落地；DOE 循环的 Top-12 候选真正生效；告警每配方一条；可编辑表不再丢焦点、监管标签不再错位 | 配方总和闭合在三处用不同容差、可行性闸门不检查，且**不进入排序**；证据不足时预测器仍是先验；grounding 对中文只做子串匹配 |
| DOE 设计 | 8.0 | **8.2** | DOE 导出恢复可用（鉴权）；DOE 循环候选修复；轮次导出同理 | CCD 星点超出物理范围只警告；混合物约束设计；DOE 历史 / 模型版本无界面 |
| 寻优与迭代 | 7.3 | **7.8** | 标量寻优真正使用实测（`lab_points_used`，标注与事实一致）；`auto_retrain` 生效；切项目前先保存 | 模型回滚是临时的且无界面（下一次重训即覆盖）；outbox 无心跳（长任务可能被重复派发）；孤儿样本无人清理；Summit 适配器只喂最后一次观测 |

**一句话**：知识库构建和配方推荐是这一轮收益最大的两项——前者是一个"测试永远发现不了"的污染（每个分块 +30 个假实体），后者是严格 grounding 把正确配方改坏。问答的"理解"没动，是下一轮最缺证据的一项。

---

## 4. 尚未修复的问题与修复方案

按"做错的代价 × 发生概率"排序。每项都给出**做法**和**验收**，可以直接拆成独立提交。

### P1 — 下一步先做

**1. 模型回滚是临时的，且没有界面**（寻优与迭代）
- 现状：`POST /api/models/rollback` 把旧版本载入内存并写 `current.json`；下一次实验提交（`auto_retrain` 开启）或 `/api/train` 立刻重训并覆盖。`api.modelVersions` / `api.rollbackModel` 前端无人调用。
- 做法：① `current.json` 增加 `pinned: true`，回滚时写入；② `_retrain_all` 对 pinned 的 (project, metric) 仍训练并存档新版本，但**不切换** current 与内存模型，响应里提示"已锁定在 vX，新版本 vY 已存档"；③ `POST /api/models/unpin`（或回滚到最新版本即解除）；④ 前端 `ModelCard` 增"版本"抽屉：列出版本、回滚、解除锁定，回滚需二次确认。
- 验收：回滚后新增实验 → current 不变；解除后恢复；界面可见版本列表。

**2. outbox 无心跳：长任务会被重复派发**
- 现状：启动恢复按"创建 / 认领后 30 分钟"判停滞，仍在运行的长任务（loop、整库 KB 导入数小时）会被重新投递。
- 做法：alembic 0043 给 `task_outbox` 加 `heartbeat_at`；`publish_progress` 顺带刷新；恢复只重投 `heartbeat_at < now - cutoff`；重投用 `apply_async(task_id=原 id)` 保持幂等。
- 验收：任务运行超过 cutoff 时重启，不产生第二次执行。

**3. `datalab_orphan_cleanup` 没有消费者**
- 现状：Saga 回滚删除 DataLab 样本失败时只落一行"待清理"，之后没人处理 → ELN 里残留孤儿样本。
- 做法：`dispatcher.drain_orphans()`（启动恢复线程里调用 + `POST /api/ops/datalab-orphans/cleanup`）；把 `_delete_sample` 公开为 `delete_sample`；成功置 DONE，失败累计次数 → DEAD；`GET /api/ops/datalab-orphans` 列表。
- 验收：伪造一次删除成功、一次失败，状态分别为 DONE / 仍 PENDING 并计数。

**4. 配方校验口径三处不一，且"总和不足"不影响排序**
- 现状：总和检查散在三处且容差不同——`chemistry.validate_formulation`（±0.5）、`chemistry` 的 v11 物理层（±5）、`formulation_gate.validate_formulations`（±5）；可行性闸门 `feasibility.check_formulation` 完全不看总和。同一配方在不同入口得到不同结论，而且总和不足（如严格 grounding 之后的 68%）只出一条告警，不影响排序。
- 做法：抽 `domain/closure.py` 作为唯一容差策略（|Σ−100| ≤ 0.5 通过，≤ 5 警告，> 5 错误），三处与可行性闸门统一调用；`multi_objective_score` 加 `closure_penalty`（上限 −20%）。
- 验收：同一输入各入口结论一致；68% 配方排名低于等价的 100% 配方。

**5. SSRF 仍有 DNS 重绑定窗口**
- 现状：`_is_blocked_ip` 在检查时解析域名，真正请求时再解析一次（TOCTOU）。
- 做法：自定义 httpx transport，把已校验的 IP 固定为连接目标（保留 Host / SNI）；统一用于 `ingest_url`、PDF 下载、检索抓取。
- 验收：解析器第一次返回公网 IP、第二次返回内网 IP，请求应被拒绝。

### P2 — 接线与一致性

**6. 13 个后端接口没有前端入口，12 个前端 API 封装无人调用**

| 类别 | 项 | 建议 |
|---|---|---|
| 诊断 / 运维（保持 API-only，补文档） | `GET /api/ops/evidence-stats`、`/ops/kb-health`、`/ops/recommend-stats`、`/kb/relevance-shadow/stats` | 在 `docs/` 的运维章节列出，加到 `scripts/` 巡检脚本 |
| 面向用户但缺界面 | `POST /api/sources/export`；`GET /api/reports/checklist/{run_id}`；`POST /api/session-plans/{plan_id}/advance` | 分别挂到来源面板、报告面板、计划面板 |
| 面向代理 / 服务 | `POST /api/connectors/mcp/call`；`/api/artifacts/versions/{id}/{content,evidence,verify,submit,evidence/verify}` | 保持 API-only，补 OpenAPI 说明；若要界面，参考发布预检面板 |
| 无人调用的封装 | `getFormulationSkill`、`listFormulationSkills`、`listInstalledSkills`、`getReportCapabilities`、`getWikiCatalog`、`getWikiDossierPack`、`getWikiPage`、`patchWikiDossier`、`kbGoldenQuestions`、`replaceMcpServers`；`modelVersions` / `rollbackModel`（见 P1-1） | 要么接界面，要么删除；新增一个"封装必须有调用方"的守卫测试（同 Settings 守卫） |

**7. 死表与误导性命名**：`kg_formulation_links` 表和模型无任何读写；`kg/formulation_linker.py` 其实只有 `_infer_role`。做法：alembic 0043 删表 + 重命名模块；或真正实现"实验 → KG 实体"的链接（让实测结果挂到实体节点，给 `kg_feedback` 用）。

**8. 主题雷达没有触发入口**：`formumind.topic_sweep` 的 beat 计划被注释掉，原文档所称"可由 API 手动触发"并不存在。做法：`POST /api/search/topic-sweep` + 设置项驱动的 beat 计划（`topic_radar_*`）；或删除任务。

**9. `async def` 里残留的同步 I/O**：`skills.install_upload`（解压 + 写盘）、`connectors.mcp_import_upload`、`projects.upload_project_export`、`experiments.import_experiments_csv`。做法：`run_in_threadpool`；把本轮用过的扫描脚本改成 CI 守卫（新增阻塞调用即失败）。

**10. `SummitOptimizer` 只喂最后一次观测**（`_prev` 每次被覆盖），且依赖（`summit`）在 `pyproject` 里因 torch 冲突被注释掉——默认部署里它构建不出来，`build_optimizer` 回退到别的引擎，这条路径实际上是死代码。做法：删除适配器，或累积观测并加可选依赖 job。

**11. `substitution` / `inverse_design` 仍用逐候选的 `default_bounds`**：同一指标的归一化范围随候选变化。影响很小（只作第 6 级并列排序键，`score_after` 前端并不展示），复用 `recommend_pipeline._rescore_with_shared_bounds` 即可。

**12. 每次外呼都新建 `httpx.Client`（38 处）**：构造一次约 48 ms（每次重新加载 CA 证书，也没有连接复用）。PubChem 逐名查询、检索提供方等热点路径上累积可观，也是 §2.1 里那个后台任务在测试里拖 20 s+ 的原因。做法：模块级共享 Client（或至少共享 SSL context），应用关闭时统一释放。

### P3 — 工程与部署

13. `docker-compose.yml` 明文写死 Neo4j 密码 `formumind123`，并把 7474 / 7687 发布到所有网卡。建议密码走 `.env`，端口绑定 `127.0.0.1`（需要远程访问的另行显式开启）。
14. 前端没有 ESLint（`react-hooks/exhaustive-deps` 能提前抓到本轮的 `AttachmentPreview` 死循环一类问题）。
15. `exportLiteratureBib/Ris` 失败时抛出原始响应文本，其余封装统一走 `readApiError`。
16. 87 处 `datetime.utcnow()`（3.12 起弃用、无时区）与 41 处带时区的调用混用，目前没有触发比较错误，但迁移到 Python 3.12 后会有大量 DeprecationWarning。

---

## 5. 建议的执行顺序

1. **P1-1 + P1-4**（模型锁定 + 闭合口径）：直接影响"推荐结果可信度"和"迭代是否被悄悄覆盖"，各自独立提交。
2. **P1-2 + P1-3**（outbox 心跳 + 孤儿清理）：同一次迁移（0043），一起做。
3. **P1-5**（SSRF 固定 IP）。
4. **P2-6 / 7 / 8**：先做"封装必须有调用方"守卫，让清单自己维护，再逐项接线或删除。
5. **P2-9**：异步阻塞守卫（把本轮用过的扫描脚本放进 CI），让这一轮的发现方式变成 CI 能力；**P2-12** 顺手做。

---

## 6. 验证记录

| 项 | 结果 |
|---|---|
| 后端全量（`pytest -m "not golden_eval"`，默认禁止出网） | 3818 通过 / 27 跳过 / 0 失败（448 s；加入后台线程等待后 +17 s） |
| `ruff check`（E9 / F401 / F63 / F7 / F82 / F811） | 通过 |
| 前端 `tsc --noEmit` | 通过 |
| 前端 `vitest run` | 123 个文件 / 610 个用例全部通过 |
| `vite build` | 通过 |
| 回归测试是否真能失败 | 抽查 3 组（材料导出、版本历史、`AttachmentPreview`）：在旧代码上失败，新代码上通过 |

没有验证的部分：没有真实 LLM / PubChem / DataLab / Redis 环境，所以涉及这些的路径只有 mock 级证据；
BayBE 路径只在 CI 的非阻塞 job 里跑。
