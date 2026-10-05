# 第四轮全面审查：缺陷修复、核心功能评分与剩余问题修复方案

日期：2026-10-05　基线：`main @ ed6c685`（第三轮全部合入 + 一键安装脚本）
范围：后端（FastAPI / Celery / SQLite / KG / 推荐 / DOE / 寻优）+ 前端（React / Zustand）+ 安装与部署脚本

> 评分是对"代码 + 测试 + 探针"的主观判断，没有真实数据集、线上流量或人工盲评作依据；
> 数字的价值在于**前后对比**和**每一分差在哪**，不要当作绝对质量。

> 这一轮没有用子 agent 并行审查（额度用尽，agent 全部 429），改为**自己写探针、自己跑**。
> 结果反而更有说服力：下面每一条缺陷都是**用生产默认接线执行出来的**，并且新增的回归测试在修复前失败、修复后通过。

---

## 1. 这一轮怎么查的

前三轮靠"读代码 + 单测 + 静态扫描"，剩下的缺陷有一个共同点：**单测永远通过，因为单测自己把生产从不传的东西注入了进去**
（`hybrid_answer(engine=…)`、`persist_experiments` 用内存库、组件测试 mock 掉封装并返回数组……）。所以这一轮换成"不注入"的手段：

| 手段 | 能抓到什么 | 本轮命中 |
|---|---|---|
| **OpenAPI 全量游走**：对每个文档化接口用 schema 推导出最小请求，逐个调用，记录 5xx / 超时，同时开着吞异常追踪 | 单测没覆盖的接口错误处理、**一个请求把整个库锁死** | 3 个 500；DOE 闭环写锁死等（§2 #2） |
| **前端封装 ↔ OpenAPI 对比**（TypeScript 编译器 API 抽取封装 → 与后端 schema 逐字段比：路由 / 查询参数 / 表单字段 / 请求体 / 响应形状 / 枚举） | 前后端各自"自洽"、合起来对不上 | 5 处（§2 #5–#7、#12）；工具已留在 `scripts/audit/contract/` |
| **参数无关的 GET 运行时比对**（真实调用 36 个 GET，把实际 JSON 和 TS 声明的形状逐字段比） | 后端响应没声明 schema 的接口 | 0 处（设置 / 环境开关 / 密钥等一致） |
| **AST：把"会话工厂"当成会话 / 引擎用** | `with default_session_factory() as s`、`.bind` | 2 处（§2 #1、#8），现为守卫测试 |
| **AST：在 `commit_session` 事务里再开一个写连接** | SQLite 单写者下**自己等自己** | 1 处（§2 #2），其余 117 处命中全是误报 |
| **pyright / 吞异常追踪全量测试** | 属性访问、未绑定变量、被吞的编程错误 | `chat.py` 流式 done 块缩进错位（§2 #3）；text2sql `.bind` |
| **各项核心功能的端到端探针**（KB 入库 → 检索 → 问答；DOE 8 种设计 × 4 个领域 × 2 个引擎；寻优 4 个领域；推荐 4 个领域；闭环 loop；离线问答） | 功能"能不能真的跑通"、结果是否越界 | 混料设计越界（§2 #4）；离线答案误报（§2 #10） |
| **"空请求体 → 带参数端点"扫描**（封装发 `{}` / 不带体，而后端 schema 有可选参数） | 按钮点了没效果 | 1 处：材料页"属性补全"（§2 #17） |
| **新增脚本逐行审查**（`install.sh` / `install.ps1` / `install.bat`）+ 在干净目录里真跑 alembic | 安装器在全新克隆上能否走完 | 3 处（§2 #9） |

---

## 2. 本轮已修复（均带回归测试；"能失败吗"一栏是把修复撤掉后确认失败的）

| # | 严重度 | 功能 | 症状 → 根因 | 回归测试 | 撤掉修复后 |
|---|---|---|---|---|---|
| 1 | **高** | 问答 | **结构化数据问答（Text2SQL）在生产里从未工作**：`hybrid_answer` 读 `default_session_factory().bind`（`sessionmaker` 没有 `.bind`），AttributeError 被 fail-open 吞掉，所有结构化 / 混合问题都变成 `route="fallback"`。聊天从不传 engine，路由自己的测试全部注入 engine，所以没人发现。<br>**修复时一并补上"打开之后才暴露"的安全面**：SQLite authorizer（只允许读 4 张白名单表，禁写 / PRAGMA / ATTACH / 读 `sqlite_master` / `load_extension`——白名单原来只存在于提示词里，`SELECT * FROM source_documents` 能通过文本过滤）；提示词里的样例行按项目过滤（原来会把别的项目的行送给模型）；只藏在注释里的 `project_id = 'p1'` 不再满足范围校验；SQL 生成加 20 s 墙钟上限（该路由现在会被每个"像结构化"的聊天问题调用，不能让慢 provider 把整条回答拖住——超时就走文献路径）。新增 `database.default_engine()` | `test_text2sql_production_wiring.py`（14）、`test_session_factory_usage.py`（AST 守卫） | 前 12 条中 6 条失败；超时用例在旧实现下要等满 3 s（断言 < 1.5 s） |
| 2 | **高** | 寻优与迭代 / DOE | **DOE 闭环写入会自己等自己**：`persist_experiments` 在写事务里调用 `provenance.link`，后者另开连接写入 → 排在自己所属事务后面，撞 30 s 忙等超时**两次**（`ensure_provenance` + 插入）→ 被 fail-open 吞掉。一个实验 × 一个候选 = **60 s 且没记录任何关联**；真实闭环（N 个实验 × 最多 5 个候选）会卡 N·5 分钟，期间**写锁被它占着，所有其他写请求排队 / 超时**。OpenAPI 游走里表现为"`POST /api/doe/cycle` 之后所有写接口都挂住" | `test_doe_cycle_persist_provenance.py` | 失败（60 s 后） |
| 3 | 高 | 问答 | **流式问答在结构化生成失败时直接报错**：`structured is None` 时本应落到 markdown 流（日志也写了 "structured stream fallback"），但 `done` 事件和 `return` 缩进多了一层，读取只在成功分支里绑定的 `_claims` / `_audit` / `evidence_reviewer` → `UnboundLocalError` → 前端收到 `error`。任何不支持 JSON 模式的 provider（DeepSeek 就是）上，每个结构化请求都是一条错误 | `test_chat_stream_structured_fallback.py` | 失败 |
| 4 | 高 | DOE | **混料（simplex-lattice）设计的每一轮都越出所有范围**：适配器把**所有**因子（含固化温度）当混料成分，按 Σ high 缩放比例——默认防腐配方第 1 轮是"环氧 156 wt%、固化剂 0、磷酸锌 0、固化 0 °C"，无任何警告。界面里有这个选项。<br>现在：单纯形只覆盖配方成分（wt% / 无单位）；成分共享基线总量（Σ 中点），按伪成分变换从下界出发，再用"有上下界的单纯形投影"拉回区间且保持总量；工艺因子固定在中点并写进计划备注；成分不足 2 个时明确拒绝 | `test_doe_mixture_mapping.py`；更新了两条钉住旧契约（Σ high）的测试 | 失败 |
| 5 | 高 | 知识库 / 前端 | **材料页第一次结构检索就崩溃**：`substructureSearch` / `scaffoldSubstitutes` 在 TS 里声明返回数组，后端返回 `{smarts, hits}`；组件测试 mock 封装并返回数组，所以 `structHits.map is not a function` 在生产里才出现。`kbChunksBySource`（`{chunks}`）同类，只是被组件里的防御代码兜住。封装现在解开信封；`KbChunk` 类型与端点真实字段对齐（`id` / `offset_start` / `heading_path`，原来读的 `chunk_id` / `offset` 从未存在） | `methods.responseEnvelopes.test.ts`（前端）、`test_response_envelopes.py`（后端，钉住形状） | 失败 |
| 6 | 中 | 知识库 | **Neo4j "写入图谱"按钮永远 422**：界面发 `{name, cas, smiles}`，端点把 `uid` 声明为必填；成功横幅读的 `r.uid` 响应里也没有，`ok=false` 时仍显示"已写入"。现在 `uid` 由服务端推导（`chem:cas:<CAS>` / 名称 slug，沿用 wiki 投影的约定）并回传，界面按 `ok` 报告 | `test_kg_neo4j_upsert_contract.py` | 失败 |
| 7 | 中 | 寻优与迭代 | **自动闭环的"最多 N 轮"设置和轮次计数重载后丢失**：前端保存 `auto_loop_max_rounds` / `auto_loop_round`，后端 `ProjectWorkspace` 没有这两个字段，`model_validate` 把它们丢掉——重载后上限回到 5，**用来限制无人值守自动迭代的轮次计数重新从 0 数起**。同源：`Formulation.client_uid`（DOE 基准徽标的身份）也被丢，重载后徽标消失 | `test_project_workspace_contract.py`（含"前端保存的每个键都必须是模型字段"的扫描） | 失败 |
| 8 | 中 | DOE / 实验台账 | 实验附件的 Datalab 样本解析：`with default_session_factory() as session`（少一对括号）→ TypeError 被 `except Exception` 吞 → 附件永远不挂到样本上；同函数在事件循环里读 SQLite，已移到线程池 | `test_experiment_attachment_item_id.py` | 失败 |
| 9 | 高（安装） | 部署 | **一键安装在干净克隆上走不完**：① `data/` 被 gitignore，alembic CLI 不像 `make_engine` 那样建目录 → "unable to open database file"；② `install.ps1` 是无 BOM 的 UTF-8 且满是中文和 ✓/⚠/❌，Windows PowerShell 5.1（`install.bat` 启动的就是它）按 ANSI 读，✓ 的字节解成 `âœ“`，而 `“` 在 PowerShell 里是字符串定界符 → 脚本解析失败；③ PowerShell 不会因原生命令失败而停，pip / npm / alembic 失败仍打印"安装完成"。新增 `.gitattributes`（`*.sh` LF、`*.bat/*.ps1` CRLF）；`install.sh --help` 不再打印 `set -euo pipefail` | `test_installers.py`（11） | 3 / 11 失败 |
| 10 | 中 | 问答 | 无 LLM（支持的离线模式，或上游故障）时的回退答案是逐字引用证据，却不带 `[^1]`，数值核验于是对着自己引用的原文说"未找到对应来源"。现在引用带脚注，省略号只在真的截断时出现 | `test_offline_answer_numeric_gate.py` | 失败 |
| 11 | 中 | API | 三个把调用方错误 / 依赖不可用当成服务端故障的 500：`GET /api/examples/{id}` 未知 id（KeyError）→ 404；`POST /api/formulations/manual` 无成分（`forms[0]` IndexError）→ 422；`pause-doecycle` 写不进 Redis → 503 | `test_api_error_statuses.py` | 失败 |
| 12 | 低 | 前端 | 附件上传的 `kind` / `note` 被写进 multipart 表单体，后端从查询串读 → 永远是默认值 | `methods.responseEnvelopes.test.ts` | 失败 |
| 13 | 低 | 工程 | `app/evals/__init__.py` 的 `__all__` 声明了没导入的 `evaluate_rigor`（`from app.evals import *` 会炸）→ 导入；新增通用守卫（任何模块 `__all__` 里的名字必须存在） | `test_dunder_all_exports.py` | 失败 |
| 14 | 低 | 前端 / 部署 | 设置页每次轮询 `GET /api/settings/ocsr`，Redis 不可达时 `celery_app.control.ping` 在 kombu 连接重试里耗 6–8 s 才答"未安装"。先用已有的 TCP 探测判断 broker，不可达直接返回（结果仍缓存 30 s） | `test_ocsr_availability_broker_down.py` | 失败 |
| 15 | 低 | 工程 | RDKit `GetMorganFingerprintAsBitVect` 已弃用：每次推荐刷几十条 `DEPRECATION WARNING`，RDKit 移除时炸。8 处调用收敛到 `services/fingerprints.morgan_bitvect`（`rdFingerprintGenerator`，位向量与旧实现逐位一致） | `test_fingerprints.py`（含"app 里不得再出现旧调用"的扫描） | 失败 |
| 16 | 中 | 配方推荐 / 前端 | **配方卡片的"相似历史配方"永远是空的**：弹窗用 `formulation.factors` 当查询，但后端从不下发 `Formulation.factors`，前端也没有任何生产者（类型注释声称"跨项目 KG 相似度查询由它构建"）→ 每个推荐配方查询为空，直接显示"未找到相似历史配方"，功能等于没接。现在用配方自己的成分 wt% 作查询（与实验里 `factors` 同形），显式 `factors`（实测 run）仍优先 | `SimilarFormulationModal.test.tsx` | 失败 |
| 17 | 中 | 知识库 / 前端 | **材料页"属性补全"按钮什么也不做**：封装向 `/api/chemical/enrich-materials` 发 `{}`，该接口只补全**传给它的**材料列表（空列表），立即返回；面板却显示"属性补全完成: ? 条更新"（读了一个响应里没有的 `enriched`）。启动时的后台 PubChem 回填（`compounds.enrich_materials`）才是按钮想要的。新增 `POST /api/materials/enrich?limit=`（每次一批，返回 `enriched / scanned / remaining / available`，只填空字段、持久化），按钮改指向它并如实显示；原接口记为 agent 专用 | `test_materials_enrich_catalog.py`、`methods.responseEnvelopes.test.ts`；`/chemical/enrich-materials` 进入 `API_ONLY_ROUTES`（带原因） | 失败 |
| 18 | 低 | 前端 | Neo4j 状态徽标永远是"就绪 · ? 节点 / ? 边"：端点答 `{enabled, reachable, stats:{compound, formulation, …_rels}}`，界面读扁平的 `nodes / edges / compounds / formulations`。封装里做一次映射 | `methods.responseEnvelopes.test.ts` | 失败 |

**新增的长期守卫**（让这一类问题下次在测试里失败，而不是在线上）：
`test_session_factory_usage.py`（AST）、`test_project_workspace_contract.py`（前端保存键 ⊆ 后端模型）、`test_response_envelopes.py` + 前端同名封装测试、
`test_dunder_all_exports.py`（AST）、`test_installers.py`、**`test_openapi_smoke_walk.py`（每个接口最小请求不得 500 / 挂住；约 1–2 分钟）**。

**做这些时顺带确认没问题的**（避免下次重复排查）：21 个"看起来没人读"的 Settings 字段全部是 `getattr(get_settings(), "name", default)` 形式读取（含 `evals_rigor_thresholds` → `scripts/rigor_gate.py`）；
pyright 的 11 处 `Requirement(...)` "缺参数"是误报（`Field(0, ge=0)` 位置默认值）；`measured=` 是 `ExperimentRecord` 的 before-validator 兼容入口；
`body_field_unknown_to_backend` 里的 `constraints` / `base_url` 分别由 `_migrate_legacy_constraints` 和 `populate_by_name` 接住；
`engine="native"` 的寻优返回 `optuna-tpe` 是设计（"native" = 非 baybe 的内置寻优，取可用的最好一档）；
未知目标指标走带 `prediction_tiers` 标注的通用先验，不是静默 0；`/health` 冷启动 0.55 s（游走里看到的 9 s 是和全量测试抢 CPU）。

---

## 3. 七项核心功能评分（满分 10；R3 报分 → 本轮实测校正 → 本轮修复后）

"校正"这一列是诚实的部分：这一轮发现的缺陷说明 R3 对 DOE / 寻优 / 问答的打分偏高——它们的测试全绿，但生产接线上有死路。

| 功能 | R3 报分 | R4 校正（缺陷计入） | R4 修复后 | 这一轮的依据 | 距 9 分还差什么 |
|---|---|---|---|---|---|
| 资料检索 | 7.5 | 7.5 | **7.5** | 离线降级链路走通（种子语料回退 + `source_status` 标注）；本轮没发现该功能自身缺陷，也没有新增能力 | 没有评测集；走代理的部署里 SSRF 固定 IP 不生效（R3 §4-2）；测试里的 `ddgs`（primp）绕过 socket 守卫会真的访问 DuckDuckGo |
| 文档解析 | 7.8 | 7.8 | **7.8** | 本沙箱没装 PDF / DOCX 解析器，**无法实测这两类**；markdown / txt / 分块 / 偏移 / 表格接口走通 | 没有真实文档基准（表格 / 公式 / 扫描件准确率）；解析器依赖冲突需分镜像 |
| 知识库构建 | 8.2 | 8.0 | **8.3** | 入库 → 分块 → 检索 → 去重 → 完整性检查走通；**全新克隆的首次安装原来走不完**（§2 #9）；块查看器字段错位（#5）；Neo4j 写入口不可用（#6） | 嵌入向量路径 CI 无覆盖；KG 实体链接无精度 / 召回评测；增量重建语义 |
| **问答能力** | 7.3 | 6.8 | **7.5** | Text2SQL 路由死了（#1）；流式结构化失败直接报错（#3）；离线答案带误导性告警（#10） | 见下三项 |
| └ 检索 | 7.7 | 7.2 | 8.0 | 结构化数据路径现在真的可达，且有硬白名单 / 项目范围 | 无检索评测集；非 OpenAI 兼容 provider 不能真流式；Text2SQL 的行级隔离只到"项目"这一层（§4-2） |
| └ 理解 | 7.0 | 7.0 | 7.0 | 无改动 | 改写 / 澄清无评测集；歧义消解依赖 KG 质量 |
| └ 上下文 | 7.2 | 7.2 | 7.2 | 无改动 | 历史截断 / 摘要策略未量化；会话持久依赖 Redis |
| 配方推荐 | 8.7 | 8.6 | **8.8** | 4 个领域离线推荐都满 100% 或带闭合告警；手工配方空成分 500 → 422（#11）；DOE 基准徽标重载保持（#7） | 无评测集；表面处理类强制 grounding 后只剩 97% |
| DOE 设计 | 8.3 | 7.8 | **8.5** | 8 种设计 × 4 个领域 × 2 个引擎逐个核对范围：**混料原来全部越界**（#4）；其余均在范围内，唯一例外是 native CCD 星点 | native CCD 星点仍超出物理范围（只告警）；DOE 历史无界面 |
| 寻优与迭代 | 8.4 | 7.4 | **8.5** | **闭环写入会自己等自己**（#2，生产里一个周期卡数分钟并拖死所有写）；自动闭环的轮次上限 / 计数重载丢失（#7）；修复后 loop 端到端（训练 → 寻优 → 下一轮 DOE）走通，结果键与前端 `LoopReport` 对得上 | 虚拟寻优曲线不是实测；无"寻优质量"评测（收敛速度 vs 随机基线） |

**一句话**：这一轮分数涨得最多的不是"新能力"，而是**把已有能力从"测试里绿"变成"生产里真的通"**——结构化问答、DOE 闭环、混料设计、材料页结构检索都属于这一类。
最大的单项发现是 #2：它不是功能错误，而是**让整个系统在 DOE 闭环期间变成只读**，而且被 fail-open 吞得干干净净。

---

## 4. 仍未解决的问题与下一步

按"没有它就无法判断好坏"优先。每项给出**做法**和**验收**。

**1. 检索 / 问答 / 解析 / 寻优没有评测集（仍是最大缺口，R3 §4-1 原样保留）**
- 做法：20–30 条带标准答案的问答 + 10 份真实文档（含表格 / 公式 / 扫描件）+ 一组"寻优收敛 vs 随机基线"的合成任务；CI 里加一个**非阻塞** job 输出趋势（沿用 `golden_eval`）。
- 验收：每次合并能看到召回@k / 引用命中率 / 单元格准确率 / 寻优收敛曲线。

**2. Text2SQL 只做到"项目"粒度的隔离**
- 现状：硬白名单 + 项目范围校验（正则，可被 `OR` 绕过）+ 样例行按项目过滤。多用户（`owner_id`）场景下没有行级隔离；聊天端点本身没有 owner 概念。
- 做法：执行前把白名单表按 `project_id`（及 `owner_id`）物化进一个**内存 SQLite 快照**，生成的 SQL 只在快照上跑——模型写什么都碰不到范围外的行；这样 `require_project_scope` 的正则可以退役。
- 验收：对抗用例（`OR 1=1`、子查询、CTE 改名、`main.` 前缀）全部只返回范围内的行。

**3. native CCD 星点超出物理范围（R3 §4-7 的前半）**
- 现状：pydoe 引擎走面心 CCD（在范围内），native 走旋转 CCD（α≈2），星点会低于 0 / 高于 100%，只在备注里告警。
- 做法：native 引擎在因子有物理边界时改用面心（α=1）或把星点裁到边界并逐 run 标注 `infeasible`；`ccd_alpha` 做成参数。
- 验收：`test_doe_probe` 类扫描里 CCD 的 oob 行数为 0。

**4. 暂停 / 恢复 DOE 闭环只存在 Redis 里**
- 现状：没有 Redis（开发 / eager 模式）时 `pause-doecycle` 恒 503，状态接口则降级回答。
- 做法：标志落到 SQLite（`campaigns` 上一列或 kv 表），Redis 仅作为加速；验收：eager 模式下能暂停并生效。

**5. 溯源写入的成本与"侧门"**
- 现状：`provenance.link` 每条边都跑一遍 `ensure_provenance`（3 条 DDL + 元数据读）并且裸 `commit()`，不走 `commit_session` 的 Redis 写锁；闭环里 N × 5 条边就是 N × 5 次。
- 做法：启动时 `ensure_provenance` 一次；新增 `link_many` 单事务批量写，走 `commit_session`。验收：100 条边 < 100 ms。

**6. Windows 安装器从未在 Windows 上跑过**
- 现状：沙箱没有 PowerShell。BOM / 退出码问题是按行为规格 + 静态守卫修的。
- 做法：CI 加 `windows-latest` job 跑 `install.ps1`（跳过前端与启动）；验收：job 绿，且故意让 pip 失败时安装器非零退出。

**7. 无 LLM Key 时流式问答直接报错，而同步问答会给离线摘录**
- 现状：流式入口在没 Key 时立刻 `error: 未配置 LLM API Key`；同步 `/api/chat` 会返回"根据已加载资料…"。文档却宣称"LLM Key 可选，离线运行"。
- 做法：产品决策——流式也走离线摘录（单个 `done` 事件），或在界面明确提示"离线模式只支持非流式"。

**8. 前端类型里的死字段**：`Formulation.measured`（没有任何生产者）、`grounding_confidence` 缺 `"medium"`——删除或补齐。

**9. 其余 R3 §4 未动的项**：#2 走代理时固定 IP 不生效、#3 `get_campaign_store()` 首次探测、#4 无界面的接口、#5 旧脚本 `verify_frontend_api.py`（现在有了 `scripts/audit/contract/`，可以直接删）、#6 `_utcnow` 帮手收敛、#8 KB 嵌入路径 CI、#9 compose 未真起过栈、#10 ESLint 范围。

---

## 5. 验证记录

| 项 | 结果 |
|---|---|
| 后端全量（`pytest -m "not golden_eval"`，默认禁止出网） | **4049 通过 / 27 跳过 / 0 失败**（558 s）。用例收集数 3992 → 4074（本轮 +82）。中途一次全量带 `sys.settrace` 追踪跑，有 2 条 `test_vector_ceiling` 性能预算用例因追踪开销超时，不带追踪重跑通过——不是缺陷 |
| `ruff check app tests` | 通过 |
| 前端 `tsc --noEmit` / `npm run lint`（`--max-warnings 0`）/ `vite build` | 均通过 |
| 前端 `vitest run` | **131 个文件 / 653 个用例通过**（含新增封装 / 相似配方 / Neo4j 统计测试） |
| 回归测试是否真能失败 | 逐条核对：text2sql（6/12 失败）、experiments item-id、安装器（3/11）、DOE 闭环（60 s 后失败）、混料映射、流式回退均在撤掉修复后失败 |
| OpenAPI 全量游走（修复前 → 后） | 290 个接口：修复前 3 个 500 + 15 个超时（连锁）；修复后 0 个 500、0 个超时（剩余 503 均为"功能关闭 / 依赖不可用"） |
| DOE 探针 | 8 种设计 × 4 个领域 × 2 个引擎：越界行数 0（native CCD 星点除外，§4-3） |

没有验证的部分：Windows 上真实执行 `install.ps1`；Docker compose 真起栈；PDF / DOCX 解析（沙箱没装解析器）；带真实 LLM 的问答质量；Redis 在线时的写锁行为（本地无 Redis，`commit_session` 走"无锁继续"分支）。

---

## 6. 本轮留下的工具（下一轮直接复用）

- `scripts/audit/contract/`：`extract_ts.cjs`（抽取前端封装）→ `dump_openapi.py`（导出后端 schema）→ `compare.py`（逐类对比，见文件头注释）。输出是**线索而不是结论**：别名、before-validator 兼容入口、FormData 成员都会被报成"未知字段"。本轮真正有价值的命中集中在 `resp_array_vs_other`、`form_field_unknown_to_backend`、`body_required_not_sent`。
- `backend/tests/test_openapi_smoke_walk.py`：OpenAPI 全量游走，已进 CI。
- 吞异常追踪思路：`sys.settrace` 只跟随 `app/` 的帧，记录 AttributeError / NameError / TypeError（特定文案）等"像代码缺陷"的异常，不管它之后被怎样吞掉——比"只 hook 日志通道"强（text2sql 的 `.bind` 就漏过了前两版追踪器）。
