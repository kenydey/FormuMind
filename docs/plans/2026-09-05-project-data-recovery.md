# 修复计划 v2：项目数据加载异常 + 会话入库 + 数据归属根治
日期：2026-09-05 · 状态：待评审 · 作者：Hermes

## 一、现象
项目「含聚合物、乳液或树脂的镁合金钝化」(1d10717c) 界面只有配方/DOE，
**资料(sources)与对话(chat_history)为空**，刷新后仍空。要求：会话以后
**入项目数据库并保存可调阅**。

## 二、诊断结论(全实证)

### 2.1 数据库实况
| 项 | 值 | 含义 |
|---|---|---|
| projects.payload.chat_history | **全库 4 项目均 0** | 曾被空覆盖,全库普遍 |
| projects.payload.sources | **全库均 0** | 同上 |
| projects.payload.leaderboard | 目标 3 条 ✓ | 配方可见 |
| projects.updated_at | 目标 08:02:02 | 最后一次全量覆盖 |
| source_documents(51)/chunks(2834) | **project_id 全 NULL** | 语料完整,全局无归属 |
| **Redis 会话** | **1 个**(chat-1788593408160,6 条) | 9/5 早对话「镁合金主要类型…」尚在 |
| Redis 持久化 | **rdb=关, aof=关** | **重启即丢会话** |
| 会话键结构 | `chat_session:{id}` / `chat_history:{id}` | **无 project_id 字段** |
| SQLite 表 | **无任何 chat/session 表** | 会话从未入项目库 |

### 2.2 根因链(代码级)
1. **全量 JSON 覆盖**：`project_store.update()` `row.payload = model_dump()` —— 每次 autosave
   整条覆盖，无合并/版本/审计。
2. **空 state 覆盖必然发生**：zustand persist 不含 chat/sources(刷新即空)；**多标签页无同步**，
   任一旧标签 autosave → 空覆盖；一旦覆盖 load 空→存空循环，**永不恢复**。
3. **会话架构缺陷(A1 遗留)**：会话存 **Redis**(key 仅 session_id，**无 project_id 关联**，
   **零持久化**)；`GET /api/sessions/list` 全局列表无项目过滤；会话"可调阅"依赖 Redis 存活。
   → 项目数据库里根本没有会话，项目界面"对话区"自然恢复不出任何内容。
4. **数据归属断裂**：51 源文档全局无 project_id；项目证据列表只存在于易覆盖的 payload。
5. 已排除：后端无清理逻辑；测试 conftest 指向独立 DB；git 对照非近期提交回归。

### 2.3 可恢复性(诚实声明)
- ❌ 1d10717c payload 的 chat_history/sources 历史：无副本，**不可恢复**。
- ✅ Redis 1 个会话(6 条消息)：**可迁回项目库**(内容完整，属 1d10717c)。
- ✅ 知识库语料 51 源/2834 chunks、requirement、3 条配方：完好。

## 三、修复方案(根治，8 Phase)

### Phase 1 · 后端：合并式更新(杜绝空覆盖)【P0】
- `ProjectUpdateRequest` 解析 `exclude_unset`：**只更新请求出现的键**，未传保持现值。
- chat_history/sources "非空→空" 需显式标记，否则记 warning 并拒绝。
- 测试：空 workspace PUT 不清 payload；部分字段 PUT 只动部分。

### Phase 2 · payload 版本历史 + 回滚【P0】
- 新表 `project_payload_history`(project_id, version, payload, cause, created_at)。
- update 事务内先存旧 payload 快照(version+1，每项目保留 50 版)。
- 端点：`GET /api/projects/{id}/history` + `POST /api/projects/{id}/rollback/{version}`。
- **任何覆盖事故可回滚——升级不丢数据的兜底**。

### Phase 3 · 会话入库(SQLite, 与项目关联)—— 本次新增核心【P0】
**目标：会话成为项目数据的一部分，永续可调阅，不依赖 Redis 存活。**
- **新表**(主库 formumind.db)：
  - `chat_sessions`(id TEXT PK, project_id TEXT 索引, title, created_at, updated_at,
    message_count, has_context) —— project_id 关联项目；
  - `chat_messages`(id TEXT PK, session_id 索引, project_id, role, content, seq,
    meta_json, created_at) —— 消息全量落库(不截断)。
- **实现**：`SessionMemoryService` 存储后端 Redis → SQLite(接口不变：
  save/load/info/delete/list 语义对齐)；Redis 保留仅作实时热缓存(可选)或直接退役。
  - save_chat_session 请求增加 `project_id`(前端传 activeProjectId)；
  - list 支持 `?project_id=` 过滤(项目内会话列表)；
  - 会话 title 落库(现 Redis 无 title，列表无法展示标题)。
- **迁移**：现有 Redis 会话 → 按内容挂回 1d10717c 写入 SQLite(1 个会话,6 条)。
- **前端**：ResearchPanel 会话列表/恢复改走项目会话 API(切项目即见该项目历史会话)；
  payload.chat_history **退役**为"最近 N 条镜像"(仅回话上下文展示,非存储权威)。
- 测试：会话 save/load/delete/list by project；消息完整不丢；重启后仍在。

### Phase 4 · 前端：防覆盖 + 本地缓存兜底【P0】
- persist 加入 sources/chat_history 轻量镜像(≤30 条)：刷新即有内容，load 成功以服务端为准。
- loadProject 空 payload 而本地有值 → 不覆盖本地 + 提示(防空循环)。
- saveProject dirty 语义：state 无变化不 PUT。
- 多标签页：storage event 监听 + 竞态提示。

### Phase 5 · 数据归属：文档 ↔ 项目【P1】
- source_documents.project_id 语义显式化(global 视图)：项目资料面板 = 项目证据 +
  全局知识库文档列表(51 源现在就该显示)。
- ingest/task 补 project_id 透传(检查修正)。
- payload 加 `schema_version`。

### Phase 6 · UI：项目 id 显示【P1】
- 项目卡/历史列表标题旁显示 id 前 8 位短码(全 id 于 title 可复制)。

### Phase 7 · 恢复目标项目可见性【P0 随 3/5 交付】
- 会话区：Redis 会话迁回 → 项目对话可见(6 条可回放)。
- 资料区：全局知识库视图列出 51 源。
- 证据/对话的 8:02 前历史：如实标注自 9/5 起(不可恢复)。

### Phase 8 · 回归 + 提交
- 后端全量相关测试 + 前端 tsc/vitest 全绿；每 Phase 独立 commit，完成后推送 main。

## 四、文件变更清单
| 层 | 文件 | 变更 |
|---|---|---|
| 后端 | app/db/models.py | + chat_sessions/chat_messages/project_payload_history |
| 后端 | app/db/project_store.py | update 合并语义、history 快照/回滚 |
| 后端 | app/services/session/memory_service.py | Redis → SQLite 实现(接口不变) |
| 后端 | app/api/session.py | save+project_id、list?project_id、title |
| 后端 | app/api/projects.py | history/rollback 端点 |
| 后端 | app/domain/project_workspace.py | exclude_unset、schema_version |
| 后端 | 迁移脚本 | Redis 会话 → SQLite(挂 1d10717c) |
| 后端 | tests/ | + 会话入库/项目过滤/空覆盖/回滚(≥10) |
| 前端 | projectSlice.ts | persist 镜像、dirty、空 payload 保护、storage 监听 |
| 前端 | chatSessionSlice.ts / api.ts | 会话 API 带 project_id、项目内列表 |
| 前端 | ResearchPanel.tsx | 会话区接项目会话数据源 |
| 前端 | 项目卡/SourcesPanel | id 短码、全局知识库视图 |

## 五、测试与验收
1. 空 workspace PUT → GET 数据仍在(Phase 1)
2. 模拟旧标签页覆盖 → 被拒/不丢(Phase 1/4)
3. **对话发送 → SQLite 立即可查(项目内)；重启后端 Redis 清空 → 会话仍可调阅(Phase 3)**
4. 会话列表按项目过滤：项目 A 看不到项目 B 会话(Phase 3)
5. 目标项目打开：资料面板 51 源 + 会话区 1 会话 6 条可见(Phase 7)
6. rollback 端点恢复任意历史版本(Phase 2)
7. 项目卡显示 id 短码(Phase 6)
8. 全量回归:后端相关测试绿 + 前端 tsc 0 + Vitest 全绿

## 六、风险矩阵
| 风险 | 等级 | 缓解 |
|---|---|---|
| memory_service 换存储影响 A1 前端调用 | 中 | 接口语义不变；前端同步改 project_id 参数,联调验证 |
| 消息全量落库体积 | 低 | 单条 <2KB,项目内万级消息 <20MB;可后续归档 |
| 合并语义与现有全量 PUT 兼容 | 低 | exclude_unset 向后兼容 |
| Redis 退役残留(实时上下文检索依赖) | 中 | 保留 Redis 热缓存层,SQLite 为权威;检查 chat 检索读源 |
| 恢复会话归属误判 | 低 | 按会话内容人工确认后挂 1d10717c |

## 七、实施顺序与预估
P1→P2(后端 1h) → **P3 会话入库(2h,核心)** → P4(前端 1.5h) → P6(0.5h) →
P5(1h) → P7 恢复(0.5h) → P8 全量回归+提交(1h)

## 八、待确认
1. 会话存储切换策略：**SQLite 权威 + Redis 仅热缓存**(推荐) vs 完全弃 Redis 会话?
2. payload.chat_history 退役为"最近 30 条镜像"(权威在 chat_messages)——可接受?
3. 现有唯一 Redis 会话(镁合金类型对话 6 条)确认挂回 1d10717c?

## 九、执行记录(2026-09-05 v2 全量完成)
- **P1 合并式更新**: ProjectUpdateRequest.workspace 改 dict + store.update presence-based
  合并(未出现键保持); sources 非空→空覆盖拒绝(warning); 测试 test_update_rejects_sources_nonempty_to_empty。
- **P2 版本历史**: project_payload_history 表 + update 前快照(每项目 50 版裁剪) +
  GET /history + POST /rollback/{version}(回滚先快照当前); 测试 test_payload_history_snapshot_and_rollback。
- **P3 会话入库**: chat_sessions/chat_messages 表(主库) + memory_service Redis→SQLite 权威
  (Redis 仅热缓存写穿, TTL 24h 仅缓存); session API save/list 支持 project_id + title;
  payload.chat_history 退役为镜像(chat_messages 重建, GET/update 实时);
  UI 会话列表按项目过滤 + 显示服务端标题。测试 test_chat_sessions_sqlite(8 例)。
  ⚠️ 存量 Redis 会话(chat-1788593408160, 6 条)在迁移前已 TTL 过期丢失 —— 实证零持久化缺陷,
  无法恢复; 迁移脚本保留 scripts/migrate_redis_sessions_to_sqlite.py。
- **P4 前端防覆盖**: persist 加 sources(50)/chatHistory(30) 本地镜像; saveProject dirty
  去重(内容无变化不 PUT); loadProject 后端空+本地有 → 保留本地显示(warn)。
- **P5 数据归属**: kb/sources?project_id 本就含全局文档(project_id OR NULL) ——
  前端 SourcesPanel 新增「📚 知识库文档」区(kbSources(activeProjectId)), 51 源语料
  在项目资料面板可见 —— 资料可见性不再依赖易覆盖的 payload.sources。
- **P6 UI**: 项目卡标题旁 id 前 8 位短码(title 属性含完整 id 可复制)。
- **P7 恢复**: 项目 KB 51 文档可见(实测); 对话历史 8:02 前不可恢复(如实)。
- 验证: 后端 30+8 passed; 端到端 curl 全通(会话 save/load/项目隔离/镜像重建/KB 51/rollback);
  前端 tsc 0 + Vitest 200(198+2 新)全绿。
