# 后端功能 ↔ 前端缺口 全量补齐实施计划(A/B/C)

日期:2026-09-05 · 状态:待评审 · 基线:main @ 6ffdcc5 · 排查方法:OpenAPI 127 端点 ↔ 前端 655 api 方法 + 组件调用方交叉核实

## 〇、总览

| 类 | 项 | 名称 | 后端改动 | 前端改动 | 估工 |
|---|---|---|---|---|---|
| A | A1 | 多会话聊天(会话持久化接入) | 中(chat+session 打通) | 中(会话侧栏) | 1.5 天 |
| A | A2 | 材料库管理面板 | **零改**(GET /api/materials 已挂,隐藏于 schema) | 中(面板 CRUD) | 1 天 |
| A | A3 | 结构搜索(SMARTS/骨架替代) | 零改 | 中(搜索面板) | 1 天 |
| A | A4 | DOI/arXiv/专利号统一摄取 | 零改 | 小(AddSourceModal 新模式) | 0.5 天 |
| B | B1 | 资料切块详情 + KB 完整性 | 零改 | 小-中 | 0.5 天 |
| B | B2 | KG 重建/挂源入口 | 零改 | 小 | 0.5 天 |
| B | B3 | Neo4j 图谱面板 | 零改(neo4j 在跑,7687 开放) | 中(独立面板) | 1 天 |
| C | C1-C6 | 无需 UI/关闭清单 | 视项 | — | — |

## 一、A 类:面向用户、当前零入口

### A1 多会话聊天(后端 session 存储 ↔ chat 链路打通)
**现状(实证)**:后端 `/api/session/{save,load,list,info,delete}` 全就绪(session 存储带 TTL);但 `/api/chat`、`/api/chat/stream` 请求体(ChatRequestValidated)与 handler **完全不消费 session**;前端 chat 由 `searchSlice` 驱动(每轮全量传 history),无"会话"概念——后端会话存储成了死代码。

**方案**:
- 后端:`ChatRequestValidated` 加 `session_id: str | None`;chat handler 开头若有 session_id → `load` 该会话 history 并入参;结尾(仅非流式 `/api/chat`)→ `save` 回写(history 追加本轮)。session 端点补前端缺的 auth/owner 归属(保持现状即可,无鉴权单用户)。
- 前端:搜索/问答区加**会话下拉**(新建/切换/删除/重命名语义=list+save+load);`searchSlice` 的 chat 调用携带当前 session_id;切换会话时自动 save 当前 + load 目标;项目级 workspace.chat_history 保留(旧行为不变,会话层为叠加)。
- 测试:后端 chat session_id 往返单测(load→append→save 幂等);前端 store 会话切换测试;现有 chat 测试回归。

### A2 材料库管理面板
**现状**:`GET /api/materials?q&role&availability`(list_materials,response=MaterialListResponse)**已挂路由但 include_in_schema=False**(前端无从发现);`POST /api/materials`(MaterialSpec: name/role/formula/smiles/cas_no/zh_name/molar_mass/price/voc/density/oil_absorption/tg_k)、`POST /api/materials/availability`(在库状态)就绪;无任何前端方法/组件。
**方案**:新建材料库面板(入口:配方卡片组分行的"材料"或新 tab):
- 列表:GET /api/materials(搜索 q/role/availability 过滤);展示 name/zh_name/role/CAS/formula/价格/在库状态
- 新建/编辑:表单(与 MaterialSpec 对齐);编辑复用 upsert
- 在库状态切换(availability);删除不做(无 DELETE 端点,材料可被配方引用,按需再加)
- 与 A3 结构搜索同面板(搜索页签合并:文本搜索 / 结构搜索)
- 测试:api 方法封装 + 组件渲染/store(如有);后端无改动无需新后端测试。

### A3 结构搜索(SMARTS 子结构 / Murcko 骨架替代)
**现状**:`GET /api/chemical/substructure?smarts=&top_k=`、`GET /api/chemical/scaffold-substitutes?smiles=&top_k=` 就绪(前者按 SMARTS 筛含某官能团/环的材料;后者同骨架=drop-in 替换候选,即你 Mg 钝化替换场景的能力);前端 0 方法。
**方案**(并入 A2 材料面板):
- 材料面板加「结构搜索」页签:SMARTS 输入框(placeholder 示例 `C(=O)O` 羧基)+ 子结构结果列表;SMILES 输入 → 骨架替代候选(标注与目标同骨架,diff 在侧链)
- 候选可一键"替换到当前配方"(调 MaterialSubstitutionModal 现有链路)
- 测试:api 封装方法单测;面板渲染;后端零改。

### A4 DOI/arXiv/专利号统一摄取
**现状**:`POST /api/ingest/task {doc_type, identifier}`(DOI/arXiv/专利号→全文→入库,OA 三候选破墙)端到端已通(P1,记忆存档);AddSourceModal 仅 ingestText/ingestUrl/文件,**无 DOI 模式**。
**方案**:AddSourceModal 增加标识符输入模式(doc_type 选择:DOI/arXiv/专利号/URL 统一走 ingest/task;文本/文件走原路径)。输入校验(DOI `10.` 前缀、arXiv id、专利号规则)与即时反馈(evidence/source_guide 展示——后端已返回提取状态引导)。
- 测试:api.ingestTask 封装 + 模式切换组件测试。

## 二、B 类:部分缺失(随对应面板迭代)

### B1 资料切块详情 + KB 完整性
- `GET /api/kb/chunks/by-source/{source_id}`(chunks 带 page/para/offset):左栏已加载资料行加「查看切块」——弹窗列该文档全部 chunk(页码/段落),**回答可追溯性增强**(与命中高亮配合)
- `GET /api/kb/integrity`(orphan 扫描):放 DependencyManager(已接 kbStats/kbReindex)→ 加"完整性检查"按钮与报告展示
- 测试:api 方法 + 弹窗组件测试。

### B2 KG 重建/挂源
- `POST /api/kg/rebuild`(返回 linked_sources/entities/mentions/links 报告):DependencyManager 或图谱面板加「重建图谱」按钮+确认+报告
- `POST /api/kg/link-source/{source_id}`:左栏资料操作菜单加「链入知识图谱」(成功后刷新 KgRelationPanel)
- 测试:组件按钮/报告渲染。

### B3 Neo4j 图谱面板
**现状**:Neo4j 服务在跑(bolt://localhost:7687 开放);`/api/kg/neo4j/*` 7 端点就绪(compounds/formulations/schema/stats/compounds-similar/formulation-compounds/link);KgRelationPanel 走 SQLite kg,neo4j 无前端。
**方案**(独立面板,不替代 SQLite 视图):Neo4j 状态卡(stats 节点/边数+adapter 状态)→ 化合物/配方浏览与搜索(compounds/formulations 端点)→ similar 展示(相似化合物/配方)。**前置确认项**:与用户确认 neo4j 是否为核心存储方向(SQLite kg 已是主力)——若是实验性,降级为"开发工具页"(放 DependencyManager 内)而非主 UI。
- 测试:api 方法 + 面板;neo4j 若未用于产品路径则面板标注 experimental。

## 三、C 类:无需 UI(处置清单)
| # | 端点 | 处置 | 理由(实证) |
|---|---|---|---|
| C1 | `/health`、`/health/detailed` | **保持** | 运维探针,前端 DegradedBanner 已间接消费(health 200 判定) |
| C2 | `kg/retrieve`、`kb/search`、`kb/hybrid-search` | **保持内部** | chat/tasks/design/chemistry/research_graph 服务端多处编排调用,各自已有 UI 承载 |
| C3 | `experiments/import-csv` | **已覆盖,关闭** | DoeResultsPanel/workflowSlice 已接 |
| C4 | `chemical/enrich-materials` | **并入 A2** | 材料面板加"批量补全属性"按钮(手动触发 enrich) |
| C5 | `formulations/versions/{detail,diff,lineage_id}` | **已覆盖,关闭** | VersionHistoryModal/RowVersionHistoryModal 已接(字符串 diff 假阴性,方法 compareWorkbenchVersions 等存在) |
| C6 | `experiments/hooks/*`、`workbench/*`(bias-trend/quality/reconcile 等) | **已覆盖,关闭** | LabWorkbench/BiasTrendPanel/Quality 面板已接(methods: getWorkbenchQuality/reconcileWorkbench 等) |

## 四、文件变更清单(汇总)

### 后端(app/ 下,仅 A1 需要)
| 文件 | 变更 |
|---|---|
| `app/api/chat.py` | ChatRequestValidated + session_id;非流式 handler 集成 session load/append/save |
| `app/services/session_memory.py`(如存在) | 无改(复用) |

### 前端(按实现顺序)
| 文件 | 变更 |
|---|---|
| `api.ts` | +session 族 5 方法(listSessions/saveSession/loadSession/sessionInfo/deleteSession);+listMaterials/upsertMaterial/setAvailability/enrichMaterials;+substructureSearch/scaffoldSubstitutes;+ingestTask;+kbChunksBySource/kbIntegrity;+kgRebuild/kgLinkSource;+neo4j 7 方法 |
| `store/slices/searchSlice.ts` | chat 调用携带 session_id;会话切换逻辑(可落新 sessionSlice) |
| `store/types.ts` | 会话状态+actions |
| `components/ChatSessionBar.tsx`(新) | 会话下拉/新建/删除 |
| `components/MaterialsPanel.tsx`(新) | 材料库列表+表单+结构搜索页签(含 A3) |
| `components/AddSourceModal.tsx` | 标识符模式(A4) |
| `components/SourceChunksModal.tsx`(新) | 切块查看(B1) |
| `components/SourcesPanel.tsx` | 操作菜单+链入图谱(B2) |
| `components/DependencyManager.tsx` | +完整性检查(B1)/重建图谱(B2)/Neo4j 工具页(B3) |
| 对应 *.test.* | 每个新组件/store 方法测试 |

## 五、实施步骤与时间表(测试先行,顺序=依赖+价值)
1. **Phase 1(0.5 天)A4+A2 骨架**:ingestTask 封装 + AddSourceModal 模式 → 材料面板列表/表单(api 封装先行,单测)
2. **Phase 2(1 天)A3+A2 完成**:结构搜索页签并入材料面板;enrich 按钮
3. **Phase 3(1 天)A1**:后端 chat+session 打通(单测)→ 前端会话栏与 store(测试)→ 全量回归
4. **Phase 4(0.5 天)B1+B2**:切块弹窗/完整性/重建/挂源
5. **Phase 5(1 天)B3**:neo4j 面板(前置与用户确认方向)
6. **收尾**:tsc + Vitest 全绿 → 分 commits(feat 按功能切) → 推送 → 端到端冒烟(手动:DOI 摄取、材料添加、SMARTS 搜索、会话切换)

## 六、风险矩阵
| 风险 | 等级 | 缓解 |
|---|---|---|
| A1 会话与现有 workspace.chat_history 双轨语义冲突 | 中 | 会话层叠加不迁移旧历史;项目重载时不强制恢复会话(会话为可选上下文层) |
| GET /api/materials 隐藏于 schema 可能未过 auth/中间件 | 低 | 实现前先 curl 冒烟验证鉴权行为 |
| Neo4j 面板投入方向未定 | 中 | Phase 5 前向用户确认(experimental 或核心) |
| session save 写入量(每次对话全量 history) | 低 | 复用现有 ttl;仅非流式端点回写;超长截断沿用现有实现 |
| B 类端点 include_in_schema 差异致契约漂移 | 低 | 以 OpenAPI+源码双读为准;封装时实测 |

## 七、不做(边界)
- 不做材料删除(DELETE 端点本就不存在,材料被引用,删除语义需级联评估——后续单独立项)
- 不做会话云端多设备同步(单用户本地 TTL 存储)
- 不迁移/合并 SQLite kg 与 Neo4j 两套图谱(共存,各留面板)
- C1-C6 不做新 UI(除 C4 并入 A2)

## 八、交付物
- 每 Phase 独立 commit(逻辑分 feat: session-chat / feat: materials-panel / feat: structure-search / feat: ingest-identifier / feat: kb-chunks / feat: kg-maintain / feat: neo4j-panel)
- 后端测试:chat+session 集成新增 ~6-10 用例;全量 ~1900 回归
- 前端:tsc 0 错误 + Vitest 全绿(新增 ~25-35)
- 端到端:DOI 摄取一例、材料添加+SMARTS 搜索一例、会话新建/切换/删除一例、重建图谱一例
