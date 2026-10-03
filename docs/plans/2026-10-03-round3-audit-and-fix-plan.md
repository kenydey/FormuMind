# 第三轮全面审查：缺陷修复、核心功能评分与剩余问题修复方案

日期：2026-10-03　基线：`main @ 44afae0`（含已合入的 Literature Library）
范围：后端（FastAPI / Celery / SQLite / KG / 推荐 / DOE / 寻优）+ 前端（React / Zustand）+ 部署文件

> 评分是对"代码 + 测试 + 探针"的主观判断，没有真实数据集、线上流量或人工盲评作依据；
> 数字的价值在于**前后对比**和**每一分差在哪**，不要当作绝对质量。

> **更新（同日）**：原 §4 的 P1–P3 十六项已全部落地（§2.3），§3 评分按落地后重估，§4 现在只列**仍未解决**的问题与下一步。

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
| 下载不带鉴权 | `<a href="/api/...">`、`window.open()` 无法携带 `Authorization`，**开启 API 鉴权后** 材料导出、DOE 导出、轮次导出、附件下载、成果架下载全部 401 → 统一 `downloadWithAuth` + `AuthDownloadLink`，失败会提示；另有 6 份各自实现的"blob → 锚点 → 点击"保存例程（全部立即回收 URL，可能取消 Safari/Firefox 的下载）合并为 `saveBlob`，延迟 40 s 回收 |
| 切换项目丢编辑 | `loadProject` 先置 `projectLoading` 再调 `saveProject`（后者遇到该标志直接返回）→ "先保存要离开的项目"是空操作，最近 1.5 s 的编辑丢失 |
| **项目串数据** | "空 payload 保护"拿**内存里上一个项目**的 sources / chat 去填新项目的空 payload → 打开一个新建 / 未检索过的项目会带上上一个项目的资料和对话，下一次自动保存把它们写进新项目 |
| 项目切换残留 | 会话列表、检索状态、过滤报告、上传 / 校验告警等切项目后仍显示上一个项目的内容（`createProject` 清了，`loadProject` 没清） |
| 保存去重漏字段 | 脏检查指纹只覆盖 ~35 个字段中的 9 个：仅改检索词、勾选来源、引擎等的修改被判"未变化"，从不 PUT |
| `AttachmentPreview` 请求死循环 | `load` 依赖父组件传入的内联 `onChanged`；`load` 完成又调 `onChanged` → 父级 state 变 → 新回调 → `load` 再次执行……对话框打开期间无限请求（测试复现：超时） |
| 可编辑配方表丢焦点 + 监管标签错位 | `<tr key={name-idx}>`：在名称输入框里每敲一个字符行就重新挂载，焦点丢失；已解析的"🔒 专利 / ⚠ 管制"标签按行号保存，配方被替换后显示在**另一种材料**上 |
| `KgRelationPanel` 加载态卡死 | 请求进行中把检索词清空 → 取消的请求不会再关 `loading`，面板永远"加载中…" |
| 文献库竞态 | 切换项目后上一个项目的慢响应可覆盖新项目列表；集合过滤器沿用上个项目的 id → 空库 → 组件按项目重新挂载 + 只接受最新请求 |
| 版本历史 / 成果架竞态 | 回滚按钮作用于**显示的**版本号：切项目时旧项目的慢响应可能出现在新项目上，一键回滚会把别的项目的版本号套到当前项目 → 只接受最新请求 + 切换即清空 |

### 2.3 第二批：原 §4 的 P1–P3（带回归测试）

| 项 | 问题（症状 → 根因） | 修复 | 提交 |
|---|---|---|---|
| P1-1 模型回滚 | 回滚只改 `current.json` 和内存模型：下一次实验提交（`auto_retrain`）、`POST /api/train` 或数据哈希变了的重启就重训并覆盖；`modelVersions` / `rollbackModel` 前端无人调用 | 回滚写 `pinned`；重训照常训练并**存档**新版本，但不切换当前模型，响应里说"已锁定在 vX，新版本 vY 已存档"；`POST /api/models/unpin`；模型卡片"版本"抽屉（列表 / 回滚需确认 / 解除锁定）；存档的候选版本按数据去重 | `26ed58c` |
| P1-2 outbox 心跳 | 恢复按"创建 / 认领后 30 分钟"判停滞，仍在跑的长任务会被重投；重投用新 Celery id，客户端手里的 id 永远等不到结果 | `task_outbox.updated_at` 当心跳（任务包装器运行期间定时刷新），恢复只重投"超过 cutoff 没动过"的行；重投复用原 Celery id（`task_id` 列，alembic 0043，附软 ALTER） | `1f0fac0` |
| P1-3 孤儿样本 | Saga 回滚删除 DataLab 样本失败时只落一行"待清理"，没有消费者 → ELN 残留孤儿样本 | `drain_orphans`（启动时 + `POST /api/ops/datalab-orphans/cleanup`）：404 视为已不存在、DataLab 不可达时不消耗重试、5 次后 `DEAD`；`GET /api/ops/datalab-orphans` | `1f0fac0` |
| P1-4 配方闭合 | "总和是不是 100%"在五处问、四种答案，可行性闸门不问；68% 的配方只有三条措辞不同的告警、判"可行"、与完整配方同样排序。**顺带**：只有一个 minimize 目标时按原始值降序排，最高 VOC 排第一 | `domain/closure.py` 唯一策略（≤0.5 通过 / ≤5 警告 / >5 错误），每处调用同一个；分数每超 1 个百分点扣 1%（上限 20%，从幅度里扣，负分不会变好）；可行性闸门 `[CLOSURE]` 拒绝错误级配方；单个 maximize 之外的目标用方向感知的归一化分并在批内共享标尺 | `a4d517d`、`0ecf8f0` |
| P1-5 SSRF | `_is_safe_url` 解析一次判断，httpx 连接时再解析一次——公网 / 内网交替应答的 DNS（重绑定）能通过检查后连到 127.0.0.1 | `PinnedTransport`：每个请求解析一次、全部地址必须公网、连接**已判定的 IP**（Host 与 TLS 的 SNI / 证书校验保持原主机名），重定向每一跳重新解析；`ingest_url`、PDF 下载（含 Google Patents 落地页）、全文抓取用它；NAT64 `64:ff9b::/96` 按其内嵌的 IPv4 判断；真实回环套接字 + 自签证书测试 Host / SNI / 证书名 | `b90153e` |
| P2-12 外呼开销 | 每次 `httpx.Client()` 重新加载 CA（约 48 ms），38 处各建各的 | `make_client` / `make_async_client` 共用进程级 SSL context（按 `SSL_CERT_FILE/DIR` 缓存，约 1 ms）；38 处全部改用；守卫测试禁止应用代码直接 `httpx.Client(` | `b90153e` |
| P2-6 接线 | 10 个无人调用的封装；13 个后端接口无前端入口 | 删除 10 个封装；"来源导出"（KB 文档勾选 → 一个 docx/pdf/html/md）和"审计清单"（审计弹窗按需生成）接上界面；守卫测试：每个封装必须有调用方，每个文档化路由要么被前端调用、要么在 `API_ONLY_ROUTES` 里写明原因（29 条：代理工具、运维诊断…），名单自己不许腐烂；`scripts/ops_check.py` 一次读完运维诊断接口，`docs/deployment.md` 列出 | `b25ac8c`、`ccf1728` |
| P2-7 死表 | `kg_formulation_links` 无任何读写；`formulation_linker.py` 只有角色推断 | alembic 0044 删表（降级可还原），模型删除；模块改名 `kg/ingredient_roles.infer_role` | `7081144` |
| P2-8 主题雷达 | `formumind.topic_sweep` 只能靠被注释掉的 beat 计划触发，文档说"可由 API 触发"不属实 | `POST /api/search/topic-sweep` + `FORMUMIND_TOPIC_RADAR_*` 驱动的 beat 计划（非法条目跳过并告警，不影响 worker 启动）+ `docker compose --profile radar` 的 beat 进程；任务现在会记录终态，缺省用联邦检索源 | `4b08282`、`ccf1728` |
| P2-9 事件循环阻塞 | 4 个上传端点在 `async def` 里解压 / 解析 / 写盘 | 移到线程池；本轮用过的扫描脚本变成测试：新增未卸载的同步服务调用即失败（短白名单写明原因，名单自己也有防腐检查，合成模块证明扫描能看见内联 / 别名 / 局部导入调用） | `c6f1632` |
| P2-10 Summit | 依赖 `torch<2.0`，项目的任何 extra 都装不上、CI 从未跑过，且 `observe` 每次覆盖上一次观测（策略永远只见一个点） | 删除适配器、探针、`build_optimizer` 一档、`/api/meta` 引擎项；文档不再把它列为引擎 | `1cbf5ff` |
| P2-11 共享标尺 | 替代 / 反向设计逐候选归一化，弱候选与强候选都得 0.5；反向设计的显示分数用需求目标、排序用 `targets.soft`，口径不一 | 替代的 `score_after` 在"全部替代 + 被替代的原配方"上共享标尺；反向设计用排序目标打分、终选种群共享标尺。（影响比审查时估计的小：成本 / 盐雾有候选无关的默认下限，真正塌缩的是 VOC>50 g/L 和没有默认范围的指标。）测试把预测器接到可控杠杆，关掉重打分即失败 | `9052896` |
| P3-13 compose | Neo4j 密码明文写在五处；Neo4j 与**无密码**的 Redis 发布在所有网卡 | 密码走 `${FORMUMIND_NEO4J_PASSWORD:-…}`（回退值保证用旧默认初始化过的安装不断）；两个数据存储只发布在 `127.0.0.1`；生产环境启用 Neo4j 且仍是默认密码时启动告警；守卫测试 | `ccf1728` |
| P3-14 ESLint | 前端没有 lint，`tsc` 看不见过期闭包 / 每次渲染都变的依赖 | ESLint 10 + `react-hooks` 两条规则（`--max-warnings 0`，CI 里跑）；配置测试用缺陷形状的样例证明规则开着。**当场查出 3 处**：`MaterialSubstitutionModal`（`?? []` 作依赖，每次渲染重跑 effect）、`useContourGrid`（memo 依赖 `xDomain[0]` 之类表达式）、`LabWorkbench`（三个批量处理函数漏依赖） | `38adaa1` |
| P3-15 Bib/Ris | 导出失败抛出原始响应文本（用户看到 `{"detail":"…"}`） | 走 `readApiError` | `73c6b40` |
| P3-16 `utcnow` | 审查时数到 87 处 | 实测各模块早已改成 `datetime.now(timezone.utc)`，只剩 1 处运行时调用 + 2 处测试；新增 `app/clock.utcnow()`，AST 守卫禁止 `utcnow` / `utcfromtimestamp` 的任何写法 | `d38bd7c` |

**做这些时顺带发现的**：① 完整跑一遍才发现闭合口径提交让 `test_physical_constraints` 过时（该测试断言 `dimension_closure` 里的重复告警）——更新为单一口径契约，告警首字母大写恢复原样；② 重绑定测试用"首次公网、之后回环"的脚本化解析器把攻击时间线原样演一遍，旧代码下它们会得到 `error:ConnectError` 而不是 `ssrf`。

---

## 3. 七项核心功能评分（满分 10；上轮 → 第一批后 → P1–P3 后）

| 功能 | 上轮 | 第一批后 | P1–P3 后 | 第二批的变化来自 | 距 9 分还差什么 |
|---|---|---|---|---|---|
| 资料检索 | 7.0 | 7.2 | **7.5** | SSRF 重绑定窗口关闭（含重定向每一跳、PDF 下载、全文抓取）；每次外呼省约 47 ms 的 CA 加载；主题雷达可手动 / 定时触发；导出失败给出原因；无人调用的封装清掉 | **检索质量没有基准集**（召回 / 精度从未量化）；限流下的降级只靠 mock 验证；走出口代理的部署里"固定 IP"不生效（代理自己解析名字，见 §4-2） |
| 文档解析 | 7.5 | 7.7 | **7.8** | 4 个上传端点的解压 / 解析 / 写盘移出事件循环，且有守卫测试防回归 | CI 里没有真实文档基准（表格 / 公式 / 扫描件的抽取准确率）；docling 与其他依赖冲突需分镜像；OCSR 默认关闭、需独立 worker |
| 知识库构建 | 7.5 | 8.0 | **8.2** | 死表删除、模块名不再误导；主题雷达可定时回填；来源可批量导出 | 嵌入向量路径在 CI 没有覆盖；KG 实体链接没有精度 / 召回评测；增量重建语义 |
| 问答能力 | 7.0 | 7.3 | **7.3** | 无直接改动（事件循环守卫让流式不再被新增的同步调用拖慢） | |
| └ 检索 | 7.5 | 7.7 | 7.7 | — | 无检索评测集；非 OpenAI 兼容 provider 仍不能真流式 |
| └ 理解 | 7.0 | 7.0 | 7.0 | — | 改写 / 澄清没有评测集；歧义消解依赖 KG 质量（需观察） |
| └ 上下文 | 6.5 | 7.2 | 7.2 | — | 历史截断 / 摘要策略没有量化；会话持久依赖 Redis，缺失时只降级为进程内 |
| 配方推荐 | 7.8 | 8.3 | **8.7** | 闭合口径统一并**进入排序**（不完整配方不再与完整配方同分、可行性闸门拒绝错误级）；只有一个 minimize 目标时不再把最差排第一；替代 / 反向设计的分数可在候选间比较 | 证据不足时预测器仍是先验；grounding 对中文只做子串匹配 |
| DOE 设计 | 8.0 | 8.2 | **8.3** | 模型版本有了界面（卡片"版本"抽屉） | CCD 星点超出物理范围只警告；混合物约束设计；DOE 历史无界面 |
| 寻优与迭代 | 7.3 | 7.8 | **8.4** | 模型回滚会"锁住"并有界面；outbox 心跳 + 按原 Celery id 重投（长任务不再被重复派发）；孤儿样本有消费者；不可用且有 bug 的 Summit 适配器移除 | 虚拟寻优的曲线只反映替代模型自洽；实测点少时优化器靠先验；多目标 Pareto 只在 BayBE 路径 |

**一句话**：这一批里分数涨得最多的是寻优（"回滚被悄悄覆盖""长任务被重投"都是用户看不见却会毁掉一轮实验的问题）和配方推荐（一个 68% 的配方不该和完整配方同分）。评分仍然没有评测集作依据——下一步最该补的是它，而不是再修一轮缺陷。

---

## 4. 仍未解决的问题与下一步

按"没有它就无法判断好坏"优先。每项给出**做法**和**验收**。

**1. 检索 / 问答 / 解析没有评测集（最大缺口）**
- 现状：所有分数是代码 + mock 级证据；检索召回 / 精度、改写与澄清、表格 / 公式 / 扫描件抽取从未被量化，"变好还是变坏"无从判断。
- 做法：20–30 条带标准答案的问答 + 10 份真实文档（含表格 / 公式 / 扫描件），CI 里加一个**非阻塞** job 输出趋势（沿用 `golden_eval` 标记与 `evals-history.jsonl`）。
- 验收：每次合并能看到召回@k / 引用命中率 / 表格单元格准确率的曲线。

**2. 走出口代理的部署里，固定 IP 不生效**
- 现状：配置了 `HTTPS_PROXY` 时请求交给代理，由代理解析名字——本进程的"解析一次、连接已判定地址"保护对这部分请求不起作用（只剩 `is_safe_url` 的预检）。这是有意的：httpx 在自定义 transport 下会忽略环境代理，不这样做会让所有走代理的部署断网。
- 做法：策略放在代理上（拒绝内网段）；可选：首次走代理时记一条警告。
- 验收：`docs/deployment.md` 写明；有代理的部署在代理侧验证过内网段被拒。

**3. `get_campaign_store()` 首次调用可能在事件循环里阻塞**
- 现状：`auto` 后端首次调用会探测 DataLab（≤ 2 s）；它被十几个 `async def` 处理函数直接调用（守卫白名单里写着原因）。
- 做法：lifespan 里预热一次。验收：从白名单里删掉这一项，守卫测试仍通过。

**4. 界面缺口里真正有用户价值的**
- 成果发布流程（`/api/artifacts/versions/{id}/{content,evidence,verify,submit,finalize,…}`）和计划步骤推进（`/api/session-plans/{id}/advance`）目前都是代理驱动的 API-only。若要给人用，参照"发布预检面板"的做法；其余 21 条 API-only 路由留着，原因在 `tests/test_frontend_api_wiring.py` 里。

**5. 旧脚本 `scripts/verify_frontend_api.py`**：与 pytest 守卫重复，且对嵌套模板字面量（`${qs ? `?${qs}` : ""}`）会误报。删除，或改成调用守卫里的读取器；CI 的 `api-contract` job 随之调整。

**6. `_utcnow` 帮手有十余份，语义四种**（朴素 datetime / 带时区 / 浮点秒 / ISO 串）：可收敛到 `app.clock`，需要逐个确认调用方对时区的假设。

**7. DOE**：CCD 星点超出物理范围只警告；混合物（成分和为 100%）约束设计。

**8. 知识库**：嵌入向量路径的 CI 覆盖、KG 实体链接的精度 / 召回评测、增量重建语义。

**9. 部署文件只做了 YAML 级校验**：沙箱没有 Docker，compose 的改动（环回绑定、`${…}` 密码、`radar` profile、healthcheck 里的 `$$` 转义）没有真正起过栈。需要在有 Docker 的机器上 `docker compose config -q`，并用 `docker compose --profile radar up -d` 走一遍（含 Neo4j 健康检查能否用环境变量里的密码登录）。

**10. 前端 lint 只开了 hooks 规则**：`AttachmentPreview` 那种"父组件传入的内联回调不稳定"的循环 `exhaustive-deps` 看不出来，仍靠回归测试。

---

## 5. 执行顺序

已按下面的顺序完成：P1-1 + P1-4（模型锁定 + 闭合口径）→ P1-2 + P1-3（outbox 心跳 + 孤儿清理，同一次迁移）→ P1-5 + P2-12（固定 IP + 共享 TLS）→ P2-6/7/8（封装守卫 → 接线 / 删除 → 死表 → 主题雷达）→ P2-9/10/11 → P3。

下一步建议：**§4-1（评测集）** → §4-9（在有 Docker 的机器上验证部署改动）→ §4-2 / §4-3（各约半天）→ 其余。

---

## 6. 验证记录

| 项 | 结果 |
|---|---|
| 后端全量（`pytest -m "not golden_eval"`，默认禁止出网） | 3969 通过 / 27 跳过 / 0 失败（566 s） |
| `ruff check .`（E9 / F401 / F63 / F7 / F82 / F811） | 通过 |
| 前端 `tsc --noEmit` | 通过 |
| 前端 `npm run lint`（`--max-warnings 0`） | 通过 |
| 前端 `vitest run` | 129 个文件 / 639 个用例全部通过 |
| `vite build` | 通过 |
| 回归测试是否真能失败 | 抽查：**共享标尺**——关掉重打分，替代 / 反向设计两条失败；**SSRF 重绑定**——把四个入口换回普通 client，四条失败（含"请求是否真的发出"的断言：测试环境自己的出网守卫会在传输层拒绝非本地主机，只看"返回空"分辨不出）；**hooks 规则**——配置测试用缺陷形状的样例证明规则开着 |
| 完整跑才发现的回归 | 闭合口径提交使 `test_physical_constraints` 过时（该测试断言 `dimension_closure` 里的重复告警），已改为单一口径契约 |

没有验证的部分：

* 没有 Docker：compose 的改动只有 YAML 解析和守卫测试，没有真正起过栈（见 §4-9）。
* 没有真实 LLM / PubChem / DataLab / Redis / Neo4j 环境，涉及它们的路径只有 mock 级证据；BayBE 路径只在 CI 的非阻塞 job 里跑。
* SSRF 固定 IP：TLS 的 SNI / 证书名校验只在回环套接字 + 自签证书上验证过；"走环境代理"的分支用注入的传输层测试，没有对着真实代理跑。
