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
| **装上解析器，用真实文件走完整入库链路**（PyMuPDF / pymupdf4llm / MarkItDown / python-docx：合成的 PDF、DOCX、XLSX、PPTX，中英文数据表 → 解析 → 分块 → 质量门 → 入库 → 检索 → 表格归一化） | 前几轮"沙箱没装解析器，无法实测"留下的盲区 | **8 处**（§2 #20–#24、#29–#31）——全部在"解析 → 入库"这条路上（主要是表格），且都是喂手写 markdown 的测试看不到的 |
| **在真实 Windows 上跑**（CI `windows-latest`：`install.bat` → Windows PowerShell 5.1 → `install.ps1`，再 import 应用、起服务、跑一组测试、故意让 pip 失败） | 只有真 Windows 才暴露的缺陷——整个仓库的 CI 原来全是 Linux | **2 处连续命中**（§2 #32、#33），都是"在 Windows 上根本用不了"级别 |
| **PowerShell 7 真解析 `install.ps1`**（下载官方 pwsh，用语言解析器解析；把字节按 Windows PowerShell 5.1 的 ANSI 方式解码再解析；真跑 `Invoke-Step` / `Invoke-Optional`） | 只靠静态扫描无法确认的 PowerShell 语义 | 确认 BOM 修复有效（无 BOM → 3 个解析错误）；辅助函数语义正确；`install.bat`（§2 #26） |

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
| 19 | 高 | 问答 | **"无 Key 也能离线运行"在界面里走不到**：同步 `/api/chat` 无 Key 时给出已加载资料的摘录（文档也这么承诺），但界面**只**走 `/api/chat/stream`，后者无 Key 直接发 `error: 未配置 LLM API Key`。离线路径存在，没有任何入口能到达它。现在流式走同一条管线，以 `phase → token → done` 交付，并带一条 `llm_offline` 提示（"以下是已加载资料的摘录，不是模型生成的回答"） | `test_chat_stream_offline.py`（6） | 6 / 6 失败 |
| 20 | **高** | 知识库 / 文档解析 | **数据表在入库时被"垃圾门"丢弃**：`is_garbage_chunk_text` 要求字母 / 数字 / 汉字占字符的 40% 以上，而管道表格天生满是标点（`\| 固体含量 \| 65 \| % \|`、`\| --- \| --- \|` 分隔行、单元格补白），且每个表格都是独立的原子 chunk，旁边没有正文来抬高比例。实测 MarkItDown 产出的 docx / xlsx / pptx 表格比例 0.31–0.34，PDF 表格（加粗 + `<br>`）0.38，宽数值表 0.24 → 全部判垃圾。端到端：一页数据表 `kb ingest gate: all 1 chunk(s) garbage — writing 0`，随后"粘度是多少"在知识库里查不到；检索期同一规则又把已入库的表格过滤一遍。现在只按**单元格文本**度量（去掉管道、分隔行、HTML 标签、加粗、`<br>`），空 HTML 表格反而改判垃圾 | `test_kb_gate_tables.py`（14，含"垃圾仍是垃圾"的对照与入库端到端） | 4 / 14 失败（含入库端到端：写入 0 个 chunk） |
| 21 | **高** | 文档解析 | **Word / PowerPoint 表格丢表头行，进而每个数值都丢单位**：Word 表格没有表头标记，MarkItDown 输出一行**空表头** + 把真表头压进正文（`\|  \|  \|  \|` / `\| 项目 \| 指标 \| 单位 \|`）。资产 `headers == ['', '', '']`，表头行变成一个伪属性"项目"，**单位列找不到**（`65` 而不是 `65 %`、`1200` 而不是 `1200 mPa·s`），类别只能靠 caption。现在空表头行让位给首个正文行——仅当首行没有数值单元格**且**下方有数值时（无表头的数据表、纯文字键值表保持原样） | `test_table_converter_output.py`（31，含真实 DOCX / PPTX / PDF 文件） | 20 / 31 失败 |
| 22 | 中 | 文档解析 | pymupdf4llm 把表头加粗（`**项目**`）、用 `<br>` 折行，被当成 caption 的标题带着 `## `——全部原样存进表格资产，于是 `_header_hits` / 单位探测 / 分类拿 `**单位**` 去比 `单位`。单元格去掉整格加粗与 `<br>`、caption 去掉标题标记（原文仍在 `raw_markdown`） | 同上 | 同上 |
| 23 | 中 | 文档解析 / 知识库 | **没有主题关键词的数据表永远不归一化**：分类词表几乎全是中文主题词，xlsx 表（无 caption）、英文 `Property \| Value \| Unit`、caption 不含主题词的表都是 `other`——而归一化器自己正是靠这些列名找名称 / 取值 / 单位列的。现在"名称列 + 另一列取值列"的表头形状即判 `performance`（置信度 0.6，低于两个关键词命中），词表与归一化器共用一份；同时补上 `耐盐雾性能`（中文 TDS 里最常见的写法，原本是整张表里唯一未映射的一行）等盐雾别名 | 同上 | 同上 |
| 24 | 中 | 知识库 / 检索 | **表格与它的 caption 分家**：表格总是独立 chunk，上方的 `表1 典型性能` / `Table 2. …` 被切成另一个几个字的 chunk——短的被长度门直接丢，长的留下一个没数据的 chunk，而表格（只有数字、没有主语）只能靠单元格值被检索到。现在 caption 并入表格 chunk（仅当紧邻上方、以 `表 N` / `Table N` **开头**且短；"如表 1 所示…"这种句子不算） | `test_chunking_table_caption.py`（13） | 7 / 13 失败 |
| 25 | 低 | 前端 | `Ingredient.grounding_confidence` 的 TS 类型缺 `"medium"`（后端是 `high / medium / low`，`medium` 成分在配方警告里列出） | `tsc` | — |
| 26 | 中（安装） | 部署 | `install.bat` 双击后控制台随脚本一起关闭——成功后的"手动启动"说明、失败时的报错**一闪而过**；文件里是 UTF-8 中文 `REM`，在中文 Windows（GBK 代码页）下 UTF-8 字符的末字节可被当作双字节前导字节而**吞掉后面的换行**；PowerShell 的退出码也没有回传。现为纯 ASCII、仅在双击启动时 `pause`（终端 / CI 不阻塞）、`exit /b %RC%`。另：下载官方 PowerShell 7，用语言解析器**真解析** `install.ps1`——有 BOM 时 0 个错误；按 5.1 的 ANSI 方式解码无 BOM 的字节则出现 3 个解析错误（证实了 §2 #9 的诊断）；真跑 `Invoke-Step` / `Invoke-Optional`（失败步骤以原退出码结束、可选步骤返回单个布尔、stderr 不致命）。这些检查现在是测试（CI 的 ubuntu runner 自带 `pwsh`，会真跑） | `test_installers.py`（+1）、`test_installers_pwsh.py`（3） | `install.bat` 用例失败 |
| 27 | 低 | 寻优与迭代 / 运维 | **默认安装下每次预测写一行 WARNING**：`predict()` 对每个含颜料的候选调用 `delta_e_2000`，其中的可选依赖探测（`import colour`）每次都失败一次并打一行 `optional feature check: No module named 'colour'`——一次寻优预测成千上万个候选，就是成千上万行（CI 日志里单个用例就有几十行）。探测改为缓存、静默。同时第一次让 `colour-science` 分支真跑起来：对照 CIEDE2000 公开测试数据（Sharma et al. 2005 第 1 对 = 2.0425）通过 | `test_colorimetry.py`（+3） | 实测：50 次预测，旧代码 50 行日志，新代码 0 行；另两条是对 CIE76 回退与 CIEDE2000 公开数据的钉子（后者装了库才跑） |
| 28 | **CI 红** | 工程 | 上一个检查点 `b09c727` 的 backend job 唯一失败：`test_optimizer_lab_seeding` 的 spy 收到了别人的 `multi_objective_score` 调用。**根因是本轮新增的 `test_openapi_smoke_walk`**：Celery-eager 下每个派发任务的接口都会在守护线程里跑真实任务体，最小请求触发的 inverse-design 跑了 **160 s**，超出 conftest 的 60 s 排空预算，漏进后面的用例。本地用"游走 → 寻优用例"同进程复现。现在游走只验证 HTTP 层（派发器回 202，不跑任务体），并在结束时断言没有遗留后台线程；游走从 3 分钟降到约 20 秒。任务体本来就有各自的测试 | `test_openapi_smoke_walk.py`（泄漏断言） | 复现命令由失败变通过 |
| 29 | 中 | 文档解析 | **上传的 `.html` / `.htm` 原样存成源码**：URL 入库一直用 `html_to_markdown` 转换，上传路径（两种扩展名都在上传框的 accept 列表里）走的文本档只解码字节——chunk 里存的是 `<!doctype html><html><head><style>…<script>…`，`<script>` / `<style>` 正文、导航、每个标签都当正文切块。现在上传与 URL 共用同一个转换器；没装 trafilatura（可选依赖）时，转换器的正则回退原来还会把表格压成一行字，现在先把 `<table>` 转成管道表（转义 `\|`、补齐参差行）；没有正文的页面得到空结果而不是原始标签。装了 trafilatura 时还有一处：小页面（数据表页面基本就是一张表）它会退化成逐格一行的纯文本基线，原来只要超过 100 字就被接受；现在页面含数据表而输出里没有管道行时改用转换器自己的保表路径（文章页仍用 trafilatura 的标题结构与去噪） | `test_html_upload.py`（12，其中 3 条需要 trafilatura，在 `backend-extras` 里跑） | 7 / 9 失败（不含 trafilatura 的条目） |
| 30 | 低 | 文档解析 | 没有文字层的 PDF（扫描件）：任务"完成：1 条"，提示"可能是扫描件"，但**不说怎么办**。本机既没有 rapidocr 也没有 MinerU 时，现在提示安装 rapidocr（`.[parse_pro]`）或配置 MinerU 云端解析 | `test_ingest_scanned_pdf_hint.py`（3） | 1 / 3 失败 |
| 31 | 低 | 文档解析 | 列 = 指标、行 = 样品的**宽表**被当"一行一属性"读，得到名字叫 `65`、值是下一列的伪属性。行标签半数以上是数字时整张表跳过并说明原因（属性集目前只用于展示，没有被推荐 / KG 消费，所以影响面是界面上的垃圾行） | `test_table_converter_output.py`（+2） | 1 / 2 失败 |
| 32 | **高** | 部署 / Windows | **后端在 Windows 上 `import` 就崩**：`artifact_versions` 与 `smart_collections` 在模块顶层 `import fcntl`（`fcntl` 在 Windows 上不存在），`app.main` 经路由列表导入前者 → `ModuleNotFoundError`；而安装器（§2 #9 修过退出码之后）照样打印"安装完成"。两个模块的注释早就写着"非 POSIX 退化为线程锁 + 原子写"，但顶层导入让这个退化永远走不到。**由 `installer-windows` 任务第一次真跑发现**——仓库里没有任何 Windows 的 CI，这类缺陷此前不可能被发现。新增 `services/_filelock.py`（POSIX `flock` / Windows `msvcrt.locking`，非阻塞重试 + 超时），两处改用它；守卫测试：`app/` 下任何模块在顶层导入 POSIX 专有 / Windows 专有模块即失败；msvcrt 分支在 Linux 上用假对象覆盖 | `test_filelock.py`（7） | 2 / 7 失败（守卫类） |
| 33 | 中 | 部署 / Windows | 同一次真跑的下一个：**智能集合在 Windows 上每次保存都失败**——`os.O_DIRECTORY` 在 Windows 不存在，属性访问抛 `AttributeError`，而旁边的 `except OSError` 接不住（Windows 子集 41 条测试里 8 条失败）。同批修：任务目录默认值写死 `/tmp/formumind_tasks`（3 处）和 `/tmp/_structure_tmp` 在 Windows 上落到 `<当前盘>:\tmp`，改用 `tempfile.gettempdir()`；PDF 导出的 CJK 字体搜索只认 Linux / macOS 路径，补 Windows 系统字体。守卫测试（Linux 上跑）：不得直接访问 POSIX 专有的 `os` 属性、不得出现硬编码 `/tmp` | `test_windows_compat.py`（6） | 3 / 6 失败 |

**CI 上新增**：非阻塞 job `backend-extras`——装上 `file_ingest` / `report_export` / `color` 与 PyMuPDF 两个固定版本 + CJK 字体，跑所有需要这些可选依赖的测试，并把"被跳过"当成失败（阻塞 job 不装它们，这类测试在那里全是 `importorskip`，这正是三轮审查都没发现表格路径问题的原因；`colour-science` 那条分支此前在任何地方都没跑过）。

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
| 文档解析 | 7.8 | 6.9 | **8.0** | 这一轮**装上了解析器**（PyMuPDF / pymupdf4llm / MarkItDown / python-docx），第一次用真实 PDF / DOCX / XLSX / PPTX 实测：解析本身都通（PDF 走 `hybrid`，页标记 / 标题 / 表格正确）；但**表格这条路有 4 处缺陷**——Word 表丢表头行并丢光单位（#21）、加粗 / `<br>` / `##` 漏进存储文本（#22）、无关键词的数据表不归一化（#23）。修复后同一张数据表从 4 种格式、中英文两种写法出来，得到同样的属性集（含单位、`耐盐雾性能 → salt_spray`） | 只测了**合成**文件，没有真实文档基准：合并单元格 / 跨页表 / 双栏论文 / 公式 / 扫描件（OCR）都没验；docling / marker / MinerU 云端各档在本沙箱不可用；解析器依赖冲突需分镜像 |
| 知识库构建 | 8.2 | 7.2 | **8.4** | 入库 → 分块 → 检索 → 去重 → 完整性检查走通；**数据表原来在入库时被质量门当垃圾丢掉**（#20）——TDS 里最有价值的部分，进不了知识库；表格与 caption 分家（#24）；**全新克隆的首次安装原来走不完**（§2 #9）；块查看器字段错位（#5）；Neo4j 写入口不可用（#6）。修复后真实 docx / xlsx / pptx / pdf 数据表 → 带 caption 的表格 chunk + 属性集，按内容可检索 | 嵌入向量路径 CI 无覆盖；KG 实体链接无精度 / 召回评测；增量重建语义；质量门对**其他**结构化块（公式、图注）是否同样误伤，未逐类实测 |
| **问答能力** | 7.3 | 6.6 | **7.6** | Text2SQL 路由死了（#1）；流式结构化失败直接报错（#3）；离线答案带误导性告警（#10）；**界面里无 Key 直接报错，离线路径走不到**（#19）；数据表进不了知识库因而问答查不到（#20 / #24） | 见下三项 |
| └ 检索 | 7.7 | 7.0 | 8.3 | 结构化数据路径现在真的可达，且有硬白名单 / 项目范围；表格 chunk 不再被门丢弃，且带 caption，"耐盐雾性能 720"这类按内容的查询能命中 | 无检索评测集；非 OpenAI 兼容 provider 不能真流式；Text2SQL 的行级隔离只到"项目"这一层（§4-2） |
| └ 理解 | 7.0 | 7.0 | 7.0 | 无改动 | 改写 / 澄清无评测集；歧义消解依赖 KG 质量 |
| └ 上下文 | 7.2 | 7.2 | 7.2 | 无改动 | 历史截断 / 摘要策略未量化；会话持久依赖 Redis |
| 配方推荐 | 8.7 | 8.6 | **8.8** | 4 个领域离线推荐都满 100% 或带闭合告警；手工配方空成分 500 → 422（#11）；DOE 基准徽标重载保持（#7） | 无评测集；表面处理类强制 grounding 后只剩 97% |
| DOE 设计 | 8.3 | 7.8 | **8.5** | 8 种设计 × 4 个领域 × 2 个引擎逐个核对范围：**混料原来全部越界**（#4）；其余均在范围内，唯一例外是 native CCD 星点 | native CCD 星点仍超出物理范围（只告警）；DOE 历史无界面 |
| 寻优与迭代 | 8.4 | 7.4 | **8.5** | **闭环写入会自己等自己**（#2，生产里一个周期卡数分钟并拖死所有写）；自动闭环的轮次上限 / 计数重载丢失（#7）；修复后 loop 端到端（训练 → 寻优 → 下一轮 DOE）走通，结果键与前端 `LoopReport` 对得上 | 虚拟寻优曲线不是实测；无"寻优质量"评测（收敛速度 vs 随机基线） |

**一句话**：这一轮分数涨得最多的不是"新能力"，而是**把已有能力从"测试里绿"变成"生产里真的通"**——结构化问答、DOE 闭环、混料设计、材料页结构检索、数据表入库都属于这一类。
最大的两项发现：**#2** 不是功能错误，而是**让整个系统在 DOE 闭环期间变成只读**，被 fail-open 吞得干干净净；**#20** 是**数据表被当垃圾丢弃**——它躲过了三轮审查，原因很具体：沙箱没装解析器，所有表格测试都喂手写的、表头完整的 markdown。装上解析器、用真实文件走一遍，几分钟就暴露了。
**教训**：凡是 `importorskip` 把整类真实输入挡在 CI 外的地方，都在替缺陷放行；`backend-extras` job 就是为此加的。

---

## 4. 仍未解决的问题与下一步

按"没有它就无法判断好坏"优先。每项给出**做法**和**验收**。本轮已经动手的（流式无 Key、Windows 安装器的解析验证、TS 类型、表格路径）不再列在这里。

**1. 检索 / 问答 / 解析 / 寻优没有评测集（仍是最大缺口，R3 §4-1 原样保留）**
- 做法：20–30 条带标准答案的问答 + 10 份**真实**文档（含合并单元格 / 跨页表 / 双栏论文 / 公式 / 扫描件）+ 一组"寻优收敛 vs 随机基线"的合成任务；CI 里加一个**非阻塞** job 输出趋势（沿用 `golden_eval`）。本轮的 `backend-parsers` job 只覆盖合成的小文件，是起点不是基准。
- 验收：每次合并能看到召回@k / 引用命中率 / 单元格准确率 / 寻优收敛曲线。

**2. Text2SQL 只做到"项目"粒度的隔离**
- 现状：硬白名单 + 项目范围校验（正则，可被 `OR` 绕过）+ 样例行按项目过滤。多用户（`owner_id`）场景下没有行级隔离；聊天端点本身没有 owner 概念，所以这现在是纵深防御而不是访问边界。
- 做法：执行前把白名单表按 `project_id`（及 `owner_id`）物化进一个**内存 SQLite 快照**，生成的 SQL 只在快照上跑——模型写什么都碰不到范围外的行；这样 `require_project_scope` 的正则可以退役。
- 验收：对抗用例（`OR 1=1`、子查询、CTE 改名、`main.` 前缀）全部只返回范围内的行。

**3. native CCD 星点超出物理范围（R3 §4-7 的前半）——产品决策**
- 现状：pydoe 引擎走面心 CCD（在范围内），native 走旋转 CCD（α≈2），星点会低于 0 / 高于 100%，只在备注里告警。`test_golden_ccd_structure` 把"星点在 ±α"**钉成了契约**，所以这不是顺手能改的 bug。
- 做法：先决定契约——native 在因子有物理边界时改用面心（α=1），或保留旋转并把星点逐 run 标 `infeasible`；`ccd_alpha` 做成参数并在界面暴露。
- 验收：CCD 的越界行数为 0（或全部带 `infeasible` 标注），黄金测试同步更新。

**4. 暂停 / 恢复 DOE 闭环只存在 Redis 里，且 24 小时后静默失效**
- 现状：标志 `doe_cycle:paused:<id>` 带 24 h TTL——周五暂停的自动闭环，周日会自己恢复；没有 Redis（开发 / eager 模式）时 `pause-doecycle` 恒 503。平台文档把 Redis 列为必需基础设施，所以后者是设计，**前者是需要决定的语义**。
- 做法：标志落到 SQLite（`campaigns` 上一列或 kv 表），Redis 仅作为加速，暂停不过期（或过期要有可见的提示 / 日志）；验收：eager 模式下能暂停并生效，暂停 25 小时后状态仍为 paused。

**5. 溯源写入的"侧门"**
- 现状：`provenance.link` 另开连接、裸 `commit()`，不走 `commit_session` 的 Redis 写锁（单进程下无影响；多进程争用时靠 30 s busy timeout，且 fail-open 只会丢边）。**成本不是问题**：实测 100 条边 106 ms（1.1 ms/条），之前担心的"N × 5 次 DDL"并不构成瓶颈。
- 做法（可选）：新增 `link_many` 单事务批量写并走 `commit_session`；验收：多进程并发写入下无 `database is locked`。

**6. Windows 现在是"测过的平台"，但只测了一部分**
- 已做（CI `windows-latest`，全绿）：`install.bat` → Windows PowerShell 5.1 → `install.ps1` 在干净检出上跑完；应用可 import、迁移后的数据库存在；服务起得来并答 `/health`；文件 / 锁 / HTTP 层的 41 条测试通过；故意让 pip 失败时安装器非零退出。过程中修了两个"Windows 上根本用不了"的缺陷（§2 #32、#33）。
- 已加（非阻塞）：`backend-windows` 任务在 Windows 上跑**整套**后端测试并把失败清单打在日志末尾——这份清单就是下一步的工作清单；结果见 §5。
- 仍缺：`install.bat` 的**双击** `pause` 行为（CI 不是双击启动）；Celery worker 在 Windows 上的 `--pool=solo` 路径；Redis / Datalab 在 Windows 上的部署；安装器默认选 `py -3`（CI 上选到了最新的 Python 3.14，项目自己的 Docker / CI 用 3.11）——能装能起，但"支持哪些 Python 版本"没有明说也没有测。
- 做法：按 `backend-windows` 的失败清单逐项修；安装器优先选 3.11 / 3.12（找不到再退回最新），并在 README 写明测过的版本；验收：`backend-windows` 失败清单为空，或每个失败都有"Windows 不适用"的显式 skip 原因。

**7. 质量门对短公式仍会误伤（影响小，取舍）**
- 现状：表格已修（#20）。其余块已实测：长公式（比例 0.55–0.58）、代码块（0.54）、图注（0.58）、图片链接（0.70）都能通过；**短公式**（`$$k = A e^{-E_a / (RT)}$$` 比例 0.27，`$$x = 1$$` 只有 9 个字符）会被判垃圾。公式 chunk 脱离上下文本来就很少是检索目标，所以只是记录，不是缺陷。
- 做法（若要处理）：对 `block_type == "formula"` 豁免密度规则，只保留长度下限；验收：Arrhenius 式这类带说明文字的公式 chunk 不再被丢。

**8. 前端类型里的死字段**：`Formulation.measured`（没有任何生产者，只有两个图表组件把它当预测的后备）——删除或接上。（`grounding_confidence` 缺 `"medium"` 已补。）

**9. 其余 R3 §4 未动的项**：#2 走代理时固定 IP 不生效、#3 `get_campaign_store()` 首次探测、#4 无界面的接口、#5 旧脚本 `verify_frontend_api.py`（现在有了 `scripts/audit/contract/`，可以直接删）、#6 `_utcnow` 帮手收敛、#8 KB 嵌入路径 CI、#9 compose 未真起过栈、#10 ESLint 范围。

---

## 5. 验证记录

| 项 | 结果 |
|---|---|
| 后端全量（`pytest -m "not golden_eval"`，默认禁止出网） | 本地，装了解析器 + colour-science 的环境：**4195 通过 / 18 跳过 / 1 失败**——那 1 个是我新写的真实文档测试里的竞态（读到"源行已写、chunk 未写"的瞬间），已改成等任务结束，并在 8 倍 CPU 过载下复验通过。用例收集数 3992 → 4212（本轮 +220）。**CI**：阻塞 job（不装可选依赖）与新增的 `backend-extras` job 在 `eda93c7` 上**均为绿**，`backend-extras` 里"真实文件测试不得被跳过"一步也通过 |
| 中途记录 | ① 带 `sys.settrace` 追踪的全量里有 2 条 `test_vector_ceiling` 性能预算用例因追踪开销超时，不带追踪重跑通过——不是缺陷。② 检查点 `b09c727` 的 CI 红过一次：`test_optimizer_lab_seeding` 被 OpenAPI 游走泄漏出的 160 s 后台任务污染（§2 #28），已修，本地用"游走 → 寻优用例"同进程复现并验证 |
| `ruff check app tests` | 通过 |
| 前端 `tsc --noEmit` / `npm run lint`（`--max-warnings 0`）/ `vite build` | 均通过 |
| 前端 `vitest run` | **131 个文件 / 653 个用例通过** |
| 回归测试是否真能失败 | 逐条核对（撤掉修复后）：text2sql（前 12 条中 6 条失败 + 超时用例要等满 3 s）、experiments item-id、安装器（3/11、`install.bat` 三项断言全失败）、DOE 闭环（60 s 后失败）、混料映射、流式回退、无 Key 流式（6/6）、表格转换产物（20/31）、质量门（4/14，含入库端到端写入 0 个 chunk）、caption 合并（7/13）、真实文档端到端（4/4）、HTML 上传（7/9）、日志洪水（50 次预测 50 行 → 0 行）均失败 |
| 真实文件实测（合成文件，装了 PyMuPDF / pymupdf4llm / MarkItDown / python-docx） | PDF（`hybrid` 档）/ DOCX / XLSX / PPTX × 中英文数据表：修复前每种格式都有至少一处表格缺陷（§2 #20–#24）；修复后 4 种格式得到同样的属性集（单位齐全、`耐盐雾性能 → salt_spray`），按"耐盐雾性能 720"可检索到表格 chunk |
| PowerShell 7.4.6 真解析 `install.ps1` | 有 BOM：0 个解析错误；无 BOM 且按 cp1252 解码：3 个解析错误（证实 BOM 必要）；`Invoke-Step` / `Invoke-Optional` 行为符合设计。现为测试，CI 的 ubuntu runner 会真跑 |
| OpenAPI 全量游走（修复前 → 后） | 290 个接口：修复前 3 个 500 + 15 个超时（连锁）；修复后 0 个 500、0 个超时（剩余 503 均为"功能关闭 / 依赖不可用"）。游走现在只验证 HTTP 层（任务派发回 202、不跑任务体），约 20 秒 |
| DOE 探针 | 8 种设计 × 4 个领域 × 2 个引擎：越界行数 0（native CCD 星点除外，§4-3） |
| 日志洪水扫描 | 寻优 / DOE 流程里不再有重复 ≥ 3 次的 WARNING（唯一的重复项是无 Redis 时每次写入一行"写锁不可用"，是预期的降级提示） |

没有验证的部分：Windows PowerShell **5.1** 上真实执行 `install.ps1` 与 `install.bat` 的双击行为；Docker compose 真起栈；**真实**（非合成）文档上的解析质量——合并单元格、跨页表、双栏论文、公式、扫描件 OCR；docling / marker / MinerU 各档；带真实 LLM 的问答质量；Redis 在线时的写锁行为（本地无 Redis，`commit_session` 走"无锁继续"分支）。

---

## 6. 本轮留下的工具（下一轮直接复用）

- `scripts/audit/contract/`：`extract_ts.cjs`（抽取前端封装）→ `dump_openapi.py`（导出后端 schema）→ `compare.py`（逐类对比，见文件头注释）。输出是**线索而不是结论**：别名、before-validator 兼容入口、FormData 成员都会被报成"未知字段"。本轮真正有价值的命中集中在 `resp_array_vs_other`、`form_field_unknown_to_backend`、`body_required_not_sent`。
- `backend/tests/test_openapi_smoke_walk.py`：OpenAPI 全量游走，已进 CI（只验证 HTTP 层，任务派发回 202 不跑任务体；见 §2 #28 的教训）。
- `backend/tests/test_document_parsers_real.py` + `test_table_converter_output.py` 里的真实文件段：用 python-docx / openpyxl / PyMuPDF 现场生成数据表文件，经 `POST /api/ingest` 走完整链路，再断言"表格在知识库里、带 caption / 表头 / 单位、按内容可检索"。下次要验合并单元格、跨页表、新格式时，直接在这里加一个生成函数即可。CI 的 `backend-extras` job 会跑它们，且把"被跳过"当失败。
- `backend/tests/test_installers_pwsh.py`：用真实 PowerShell 解析并执行 `install.ps1` 的辅助函数；按 Windows PowerShell 5.1 的方式（无 BOM → ANSI）解码后再解析，可复现"没有 BOM 就解析失败"。
- 日志洪水扫描思路：在 `logging` 根 logger 和 loguru 上挂计数器，跑一遍寻优 / 推荐 / DOE，列出重复 ≥ 3 次的 WARNING / ERROR——比读代码更容易发现"热路径里每次都失败一次的可选依赖探测"（`colour` 就是这么找到的）。
- 吞异常追踪思路：`sys.settrace` 只跟随 `app/` 的帧，记录 AttributeError / NameError / TypeError（特定文案）等"像代码缺陷"的异常，不管它之后被怎样吞掉——比"只 hook 日志通道"强（text2sql 的 `.bind` 就漏过了前两版追踪器）。
