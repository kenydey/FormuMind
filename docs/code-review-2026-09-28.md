# FormuMind 全面代码审查报告

- 日期：2026-09-28
- 范围：`backend/app/`（API 路由 + services）、`frontend/src/`（React 组件 + hooks）
- 方式：只读静态审查（grep / AST / tsc / py_compile），未修改任何源代码，未提交 git
- 审查员：后端 / 前端 / 冗余 / 性能，4 路并行

## 总览统计

| 方向 | P0 致命 | P1 严重 | P2 一般 | P3 建议 | 已验证无问题 |
|------|--------|--------|--------|--------|-------------|
| 后端 | 1 | 8 | 7 | 1 | 若干（见文末） |
| 前端 | 0 | 0 | 3 | 9 | tsc 全绿、API 契约全对上 |
| 冗余 | 0 | 1 | 若干 | 若干 | — |
| 性能 | 0 | 5 | 5 | 0（含 6 项确认无碍） | — |
| **合计** | **1** | **14** | — | — | — |

**P0/P1 共 15 项**，其中 11 项由 Wave 1–6 新增代码引入，4 项为既有问题。

---

## P0（1 项）

### B-1. artifact 版本状态机 TOCTOU：并发可回退 finalized，破坏不可变性
- 文件：`backend/app/services/artifact_versions.py:312-318`（`set_version_content`）、`:321-337`（`_transition`/`submit_version`）、`:256-296`（`create_version`）
- 描述：`set_version_content` 先 `_load_version`（无锁），再用过期内存对象做 `status!= staging` 校验后 `_save_version`；`submit_version` 同样 load→改→save 全程无锁；`_LOCK` 只包裹单次文件写。
- 根因：读-校验-写不是原子事务。A 线程 load 到 staging，B 线程 submit 写成 pending，A 再用旧对象保存 → status 被写回 staging、content 被改写。
- 复现（审查员已确认）：A（load 后 sleep 0.3s 再 save）与 B（submit）并发 → 最终 `status=staging`、`content` 被改写，submit 被回退。同理可作用于 `finalize_version`，把 finalized 打回 staging 并改内容。
- 证据：
```python
def set_version_content(version_id: str, content: bytes) -> Version:
version = _load_version(version_id) # 无锁
...
_save_version(version, content=content) # 用 stale 对象校验 status
```
- 修复：按 version/lineage 加锁覆盖"读→校验→写"完整事务；`version.json`/`content.bin` 用临时文件 + `os.replace`；或迁移 SQLite 用 compare-and-swap。`create_version` 的"先写 version 再 append lineage"同样要进同一事务（并发 create 会丢 `version_ids`，产生 orphan version）。

---

## P1（14 项）

### 后端 P1

#### B-2. `api/kg.py:464` — `SimilarFormulationMatch` 未导入，接口必现 NameError
- 文件：`backend/app/api/kg.py:10-22`（import 列表）、`:464`（使用处）
- 描述：相似配方路由在有匹配结果时执行 `SimilarFormulationMatch(...)`，但 import 列表只有 `SimilarFormulationRequest`/`SimilarFormulationResponse`，没有 `SimilarFormulationMatch` → NameError → 500。空结果时被掩盖。
- 根因：漏 import；该类定义在 `app/domain/kg_schemas.py:211`。`'SimilarFormulationMatch' in vars(app.api.kg)` → `False`（已验证）。
- 修复：在 `from..domain.kg_schemas import (...)` 中补 `SimilarFormulationMatch`。

#### B-3. Smart Collections 并发丢更新 + 非原子写可致整库清空
- 文件：`backend/app/services/smart_collections.py:68-93`（`_load_store`/`_save_store`）、`:153-`（`create_collection`）、`:198-`（`update_collection`）、`:323-`（`refresh_collection`）
- 描述：`_LOCK` 只包裹 `_save_store` 内的 `write_text`；load 与改都在锁外。并发 create 各自读旧版本、各自保存，后写者覆盖先写者。`write_text` 非原子，并发读可读到半截 JSON → `_load_store` 解析失败返回**空 store** → 下次保存把空库写回去，整库清空。
- 根因：load-modify-save 非事务；写非原子；损坏静默放大为空库。
- 复现（已确认）：20 线程并发 `create_collection` → 只剩 1 条；过程中出现 `smart collections load failed: Expecting value`（读到半截文件）。
- 修复：按 project 加锁覆盖完整读-改-写事务；保存用同目录临时文件 + flush/fsync + `os.replace`；多进程部署用文件锁或 SQLite。

#### B-4. Smart Collections 自动刷新锁让"非阻塞" GET list 长时间阻塞
- 文件：`backend/app/api/smart_collections.py:30-40`（`_auto_refresh_worker`）、`:61-63`（`_kick_auto_refresh` 取锁）、`:112-115`（GET list 同步调用）
- 描述：worker 在 `_AUTO_REFRESH_LOCK` 内执行**完整** `refresh_collection`（含外部文献搜索，可达分钟级）；`_kick_auto_refresh` 被 GET list 同步调用，每次需同一把锁。一次刷新在途时，所有后续 GET list 卡在取锁上；锁还是全局的，跨 project 互相阻塞。与注释"后台刷新、不阻塞列表返回"直接矛盾。
- 根因：用同一把锁既做"去重标记"又做"串行化耗时 IO"。
- 修复：in-flight 集合用独立短锁（只保护集合增删）；耗时搜索移出全局锁；持久化阶段再按 project 串行。

#### B-5. MCP `_request` 的 timeout 形同虚设：`readline()` 永久阻塞 + 持有 session 锁
- 文件：`backend/app/services/mcp_client.py:297-341`（`_StdioSession._request`）
- 描述：`while time.time() < deadline:` 循环内调用阻塞式 `self._proc.stdout.readline()`。子进程存活但不输出换行时，线程永久卡在 `readline()`，deadline 检查永远执行不到；且全程持有 `self._lock`（300 行），该共享 session 的其他请求一并饿死。
- 根因：阻塞 IO + 忙等式 deadline 的错误组合；锁粒度覆盖整个等待。
- 证据：
```python
with self._lock: # 300 行
...
deadline = time.time() + timeout_s
while time.time() < deadline:
line = self._proc.stdout.readline() # 阻塞：无换行则永不返回
```
- 修复：用 `selectors`/`select` 按剩余时间等待 stdout 可读，或独立 reader 线程 + Queue；锁只保护写 id 分配，不覆盖等待。

#### B-6. rigor `numeric_consistency`：无引用答案回退全 evidence 池，门禁对无来源数字失明
- 文件：`backend/app/evals/rigor_rubric.py:224-247`（约 232 行 `pool = cited or evidence`）
- 描述：答案有数字但**零引用**时，`cited=[]` 回退到全部 evidence。任一未引用证据含相同数字即判一致。与模块文档"无来源数字记 fail"矛盾；且 `metric_citation_veracity` 对零引用直接给 1.0（约 200 行 `1.0 if not indices`），两者叠加使 CI 门禁完全看不见无引用数字。
- 根因：`pool = cited or evidence` 的 fallback 设计错误。
- 复现（已确认）：零引用答案 + evidence 含相同数字 → `score=1.0, failures=[]`。
- 修复：答案有数字但无有效引用时直接 fail；数字必须绑定到其引用 passage，不回退全池。

#### B-7. chat 吞掉 `ReviewerModelError`，Wave 5 的显错契约被架空
- 文件：`backend/app/api/chat.py:364-370`、`:649-655`；`backend/app/services/evidence_reviewer.py:208-243`
- 描述：Wave 5 明确约定：配置 `evidence_reviewer_model` 后调用失败必须抛 `ReviewerModelError`（"不静默回退，避免'以为审了'"）。但 chat 两条路径都是 `except Exception → logger.debug("reviewer skipped")`，最终返回 `evidence_reviewer=None`，客户端无任何感知，随后还照常触发 `_fire_auto_review`。用户拿到"看起来审过"的答案，实际 reviewer 已失败。
- 根因：调用方 broad-except 与被调方显错契约不一致。
- 连带：`api/review_runs.py` 的 `rerun_review` 文档称 "review_answer 永不抛错" 已不成立 → 该路径会直接 500。
- 修复：单独捕获 `ReviewerModelError`，向客户端返回显式的 reviewer error/unavailable 状态（或按产品契约阻断），绝不 debug-only 吞掉。

#### B-8. review run_id 毫秒时间戳碰撞，并发覆盖审计记录
- 文件：`backend/app/services/reviewer_fix_loop.py:203`（`new_review_run`）、`:452`（`run_fix_loop` 内）
- 描述：`run_id = f"{session_key or 'na'}-{int(time.time() * 1000)}"`。同一 session 同一毫秒内并发建 run → 相同 run_id → `save_review_run` 互相覆盖，审计记录丢失。
- 修复：uuid/ulid，或时间戳 + 随机后缀；保存用独占创建/唯一约束。

#### B-9. `run_doe_cycle` 的 `n=20` 恒触发 ValidationError，Top-20 推荐从未真正工作
- 文件：`backend/app/services/doe_cycle_service.py:37-40`；`backend/app/api/formulations.py:93`（`n: int | None = Field(default=None, ge=1, le=12)`）
- 描述：`run_doe_cycle` 调 `RecommendFormulationsRequest(requirement=requirement, n=20)`，但模型约束 `le=12` → 构造即抛 ValidationError → 被 `except Exception` 吞掉 → **恒回退单条 baseline**。DOE 闭环文档写的"Top-20 候选"实际从未生效。这是 live 路径（`worker/tasks.py:1427` ← `api/doe.py:148`）。
- 根因：调用方与模型约束不一致；异常被吞导致静默降级。
- 代码证据：构造 `RecommendFormulationsRequest(requirement=None, n=20)` → `ValidationError`（已验证）。
- 修复：n 改为 ≤12，或放宽模型约束；fail-open 回退应打 error 级日志并告警。
- 疑似连带：同文件 138–143 行把 `_doe_metadata` 嵌套 dict 注入 `factors` 并持久化到 `ExperimentRow.factors`，下游 consumers 是否受影响需运行时验证。

### 性能 P1

#### P-1. `GET /api/org/dashboard` 每次加载全表 JSON 列做聚合
- 文件：`backend/app/api/org.py:17`（路由 `org_dashboard`）
- 证据：`all_measured = session.execute(select(ExperimentRow.measured)).scalars().all()` 全表拉回 Python 聚合；每个 metric 再查一次 top 50；`all_factors`、`campaigns`（含 loop_history 大 JSON）同样全量。
- 根因：无缓存、无分页；`measured`/`factors` 是 JSON 列（`app/db/models.py:54`），无法 SQL 聚合。
- 量化：每次请求 = O(全表行数) JSON 反序列化 + 1 + min(metric数,5) 次查询。
- 修复：dashboard 加 TTL 缓存（60–300s）或物化统计表；关键 metric 拆独立列。

#### P-2. `GET /api/review/runs` 列表：每个 run 读文件 + 重算 artifact 全量 sha256
- 文件：`backend/app/api/review_runs.py:62-95` → `reviewer_fix_loop.py:265`（`_check_stale`）→ `artifact_versions.py:605`（`verify_version`）→ `:226`（`_read_content`）
- 证据：列表最多扫 500 个 run 文件，每个 `load_review_run` → `_check_stale` → `verify_version` → `_content_sha256(_read_content(version_id))`（读整个 `content.bin` 再哈希）。
- 根因：列表页只需摘要，却为每个 run 触发 W5-3 stale 重算；finalized 版本不可变，digest 永不漂移，重算纯属浪费。
- 量化：默认 limit=50 → 50 次 run JSON 读 + 50 次 disposition 读 + 50 次 content.bin 全量读/sha256。
- 修复：artifact digest 按 `version_id` 做进程内缓存（finalized 不可变是 W4-1 已保证的不变式）；或 stale 判定移到写入时，列表只读缓存字段。

#### P-3. 每次聊天请求从磁盘重读全部 SKILL.md（无缓存）
- 文件：`backend/app/api/chat.py:551` → `evidence_synthesis.py:55` → `chat_skills.py:126`（`skill_prompt_block`）→ `:117`（`get_chat_skill`）→ `:99`（`list_chat_skills`）→ `:56`（`_load_skill_dir`）
- 证据：`list_chat_skills` 每次调用都重新 glob + `read_text`；`skill_prompt_block` 对每个 skill id 都触发一次全量 list（含 `.formumind-install.json` 读）。
- 根因：prompt 构建路径（每条聊天消息）上无缓存；skills 数 N、选中 M → 每请求约 N×(1+M) 次文件读 + frontmatter 解析。
- 修复：`list_chat_skills` 加进程内缓存（mtime 失效或在 skill 安装/卸载时显式失效——`skill_install.py` 已是集中写入点）。

#### P-4. OpenAlex 多 arm 检索：串行请求 + 每个 arm 新建 httpx.Client
- 文件：`backend/app/services/literature.py:896-907` → `search_providers.py:392`（`search_openalex`）
- 证据：`for arm in primary:` 串行（precise → recall，条件触发 broad）；每个 arm `with httpx.Client(...)` 新建（TCP+TLS 重复握手），页间也串行。
- 量化：一次搜索 = 2–3 arm × ceil(limit/25) 页全串行；并行化预计省 40–60% 墙钟（需 profiling 验证）。
- 修复：arms 用 ThreadPool 并行（`iter_search` 已有 per-source 并行模式可仿照）；注意 OpenAlex broad_boolean 限流 5 req/s，并行加信号量。

#### P-5. OA 全文补全：逐条串行 enrich（每条 = 下载+解析+索引）
- 文件：`backend/app/services/literature_oa_enrich.py:99-140`
- 证据：`for it in selected:`（上限 40）内 `enrich_search_results([ev], max_docs=1,...)` 逐条串行；每条 = HTTP 下载 PDF → 解析 → chunk → embed → 写库。
- 量化：limit 40 时最坏 40×单篇耗时（数秒/篇 → 数分钟）。
- 修复：ThreadPool 并发（`kb_ingest` 已有 fetch 并发 + index 串行的成熟模式，注意 SQLite 写串行化）。

### 冗余 P1

#### R-1. CJK FTS 四件套 ×3 处完全复制（字符级一致）
- 文件：`backend/app/services/agent_memory.py:62,73,77,97`、`backend/app/services/source_fts.py:47,58,62,82`、`backend/app/services/wiki/fts.py:34,203,208,228`
- 描述：`_cjk_expand`（9行）、`_token_to_match`（22行）、`flush_latin`（7行）、`_match_query`（12行）三处字符级一致。
- 根因：各自演进，已出现分叉（wiki 版 `_token_to_match` 多 1 行）。
- 修复：抽到共享模块（如 `services/cjk_fts.py`）。

---

## P2（后端 7 + 前端 3，摘要）

### 后端 P2
- **B-10** `literature_screening.py:541-542`：screening evaluate 非法年份 → 未映射 500（`int(year_min)` 无 try；同文件另三个端点都映射了 `ValueError→400`，唯独 evaluate 漏了）
- **B-11** `reviewer_fix_loop.py:322,370-379`：自动 reviewer `_AUTO_STATE` 无界增长（无 TTL/上限/清理）
- **B-12** `mcp_client.py:379-386`：MCP session 无生产级清理，子进程驻留（lifespan shutdown 未关闭 MCP；`_reset_client_cache` 明确"测试隔离用"）
- **B-13** `api/smart_collections.py:83-85` + `services/smart_collections.py:224-229`：PATCH schedule 部分更新语义被破坏（`model_dump()` 非 `exclude_unset` 覆盖 interval_hours）；filters PATCH 全量替换清空未提供字段
- **B-14** `citation_date_guard.py:96-115`：域名白名单子串匹配（`example.com` 命中 `evil-example.com`），边界不安全
- **B-15** `literature.py:1151`：`search_round_deadline_s` 未在 Settings 声明，恒为 fallback 240s 且不可配置
- **B-16** `rerank_plugin.py:81-97,145-152`：cross-encoder 每次 rerank 全量重载模型（GB 级权重），无 `_MODEL_CACHE`

### 前端 P2
- **F-1** `HubCollectionsPane.tsx`（`doCreate`）：新建集合后把 POST 响应（无 `snapshot_count`/`last_snapshot` 字段，后端 `create_collection` 直接 `return dict(col)`，见 `services/smart_collections.py:183`；这两个字段只在 `_summarize()` 里加，`services/smart_collections.py:126-150`）塞进 `CollectionSummary[]` 列表 → 该行 snapshots 显示空白，直到手动刷新。修复：doCreate 后调一次 detail，或前端补默认值。
- **F-2** `ReviewerAuditModal.tsx`（`loadList`）：从 ReviewerCard 打开时未传 `projectId`（`ReviewerCard.tsx` 未透传），后端 `GET /api/reviews/runs`（`api/review_runs.py:68-72`）`project_id` 缺失 → 过滤跳过 → 返回全项目审计记录（数据越界）。
- **F-3**（疑似）`ManifestDetailPanel.tsx`：`new Set(frozen.item_ids)`，`item_ids` 缺失（旧数据/损坏）时白屏；正常路径后端必写，标疑似。修复：`new Set(frozen.item_ids?? [])`。

---

## P3（摘要）

### 后端 P3
- **B-17** `api/ingest.py:110-121`：文件上传先全量读入内存再做大小检查，大文件可致内存耗尽（DoS）。修复：分块读累计字节数，超限即停 413。

### 前端 P3（9 项）
- **F-4** 多处异步加载无取消/序号守卫（ReviewerCard / ManifestDetailPanel / LiteratureFreezeStrip / MemoryPanel），快速切换时旧请求覆盖新数据；`TableBadges.tsx` 的 `cancelled` 模式可作范本。
- **F-5** `LiteratureFreezeStrip.tsx`：回滚用 `document.getElementById` 命令式读 select，应改为受控组件。
- **F-6**（疑似）`ArtifactVersionsPanel.tsx`：`STATUS_TONE[v.status]` 无兜底，未知 status 渲染异常；加 `?? STATUS_TONE.staging`。
- **F-7** `McpApprovalDialog.tsx`：`request!.id` 非空断言多余（已有守卫）。
- **F-8** `McpApprovalCenter.tsx`：`toRequest` 计算了从未展示的 `requested_at`。
- **F-9** ReviewerCard"重审"：review 直接通过时后端 `run_fix_loop` 返回 `{"rounds": 0}` 不含 `run_id`（`reviewer_fix_loop.py:427-432`），前端回退重载旧 run，用户点完无变化易困惑；加 toast 提示。
- **F-10** `HubCollectionsPane.tsx`：`load` 未 useCallback，靠 eslint-disable 压住。
- **F-11** `ResearchPanel.tsx`：传给 ReviewerCard 的 question 可能为空 → 后端 400；空时禁用"重审"按钮。
- **F-12** `HubCollectionsPane.tsx`：`doDelete` 无防抖，双击打 404 弹错。

### 性能 P2/P3（5 项）
- **P-6** `kb.py:375-379` `chunks_by_source` 无分页 + 前端 `SourceDetailModal.tsx:107` 全量渲染（600 chunk 无虚拟化）。
- **P-7** `literature.py:1160-1174` `_notify`：每个 source 每页完成都全量 `_merge_filter_rank` 重排；应只发增量，最终再排一次。
- **P-8** `experiments.py:1245,1289`：experiments 搜索全表 Campaign 拉回 Python 扫描 JSON 列；应 SQL `json_extract` 过滤或建 FTS。
- **P-9**（疑似）`evidence_synthesis.py:70-79` → `mcp_skill_docs.py:187`：聊天请求触发 MCP probe（子进程探测），疑似无缓存；probe 结果应按 server+generation 缓存。
- **P-10** `connectors_builtin.py:180-187`：`gather_connector_evidence` 串行调 connector（目前仅 2 个，影响小）。

---

## 冗余清单

### 死代码（后端，grep -rnw 验证零引用）
**P2：**
- `backend/app/worker/tasks.py:218,231,249,260,271,292`：`TaskManager.submit_optimization/submit_loop/submit_comprehensive_research/submit_recommend/submit_search/submit_dependency_install` 6 个方法零调用（任务分发已迁至 `app/api/_dispatch.py`）。
- `backend/app/services/simulator.py`（整模块，含 `:55 simulate_cure`）：全仓库无 import。

**P3**（每条经 `grep -rnw` 确认除定义行外零引用，共 40+ 处，列出代表）：
- `app/agents/base.py:14` `ExpertAgent`；`app/api/ingest.py:42` `BatchIngestResponse`；`app/db/campaign_store.py:270` `_delete_campaign_meta`；`app/db/database.py:245` `_ensure_source_acquisition_column`（自称向后兼容别名）；`app/db/migrate.py:78` `migrate_json_if_needed`；`app/db/product_store.py:206` `_link_structure`；`app/domain/chat_schemas.py:89` `StructureContext`；`app/domain/formulation_gate.py:554` `parse_llm_recommended`；`app/domain/knowledge.py:679` `variant_formulations`；`app/domain/material_catalog.py:143` `seed_specs`；`app/domain/objective_contract.py:68,143` `primary_objective_metric`/`measurements_dict_for_row`；`app/domain/project_spec.py:260` `formulation_from_materials`；`app/domain/schemas.py:433,907` `IngestResult`/`AgentReviewRequest`；`app/domain/tradeoff_schemas.py:78` `RecommendMeta`；`app/pipeline/multimodal_fusion.py:73` `run_multimodal_kg_fusion`；`app/pipeline/workflow.py:247` `run_research_graph_stream`；`app/services/connectors_builtin.py:60` `set_connector_enabled`；`app/services/deep_research/query_expander.py:68` `build_search_queries`；`app/services/domain_tagging.py:167` `profile_or_none`；`app/services/embodiment_drafts.py:463` `_placeholder_from_names`；`app/services/engines/adapters/baybe_objective_builder.py:46` `build_objective`；`app/services/engines/adapters/baybe_space_builder.py:95` `build_genome_searchspace`；`app/services/evidence_reviewer.py:356` `flag_bare_numerics`；`app/services/external_alternatives.py:125,134` `_cid_from_smiles`/`_cid_from_name`；`app/services/factor_suggest.py:99` `suggest_factors_as_doe`；`app/services/formulation_explain.py:15` `_objective_metrics`；`app/services/kb_ingest.py:550` `_ingest_one`；`app/services/kg/entity_linker.py:33` `_element_entity_id`；`app/services/kg/formulation_linker.py:29` `link_experiment_to_kg`；`app/services/kg/prompts.py:47` `evidence_has_unresolved_trade`；`app/services/kg/relation_extractor.py:336` `relation_type_values`；`app/services/literature.py:188` `_resolve_search_query`；`app/services/llm.py:761,1827` `iter_chat_with_tools_stream`/`_offline_synthesis`；`app/services/llm_trace.py:85` `shutdown_tracer`（main.py lifespan 未调用）；`app/services/mcp_approval.py:126` `clear_policies`；`app/services/mcp_session_grants.py:99,106` `revoke_session`/`list_grants`；`app/services/pdf_downloader.py:94` `_google_patents_url`；`app/services/ro_crate_export.py:31` `_sha256_text`；`app/services/skill_install.py:58` `ReviewFinding`；`app/services/training.py:128,135,262` `_kfold_r2`/`_conformal_q90`/`_metrics_for`；`app/services/wiki/draft_save.py:24` `is_query_draft_path`；`app/services/wiki/storm_outline.py:235` `_OutlineLLM`；`app/worker/tasks.py:345` `_progress_cb`。

**疑似死代码（需人工确认）：**
- `backend/app/worker/tasks.py:1703` `run_collection_refresh_task`：celery 任务，`celery_app.py:60` beat_schedule 已注释；Wave 6 已改走 GET list 顺带后台刷新。若确认不用 beat，删除。
- 已排除、**不是**死代码：`table_contract.py:222,231,242`（HTMLParser 回调）；`tasks.py:1492 run_molscribe_recognize_task`（`send_task("formumind.molscribe_recognize")` 跨进程按名调用）。

### 死代码（前端）
**P2：**
- `frontend/src/components/WikiBrowserModal.tsx`：整个文件零 import（`grep -rnw WikiBrowserModal` 仅定义行）。
- `frontend/src/api/domains/*.ts`：28 个 facade 值（`chatApi`、`chemistryApi`、`doeApi`…）仅被 `api/index.ts` 转 export，**无任何组件消费**（组件统一用 `api` 对象）；29 个 `*Api` 类型别名零引用。唯一例外：`collectionsApi` 被 `HubCollectionsPane.tsx` 直接使用。

**P3：**
- `SourceTypePicker.tsx:93` `isLocalEvidence`；`charts/chartUtils.ts:33,80` `useChartDimensions`/`formatNumber`；`hooks/useTaskCancel.ts:13` `CancelableTaskKind`；`api/types.ts:70` `ChemicalLookupResult`。
- 前端未使用 import：0 个（`tsconfig` 开了 `noUnusedLocals/noUnusedParameters`，编译期已强制）。

### 无用 import（后端，AST 精确扫描，P3）
30 个真残留（建议删除），代表：`api/chat.py:120,499`（`kb_index` 两处）、`api/doe.py:13,15,21`（`json`、`uuid`、`BaybeCampaignEngine`）、`api/ingest.py:14,15,22,23`（`BackgroundTasks`、`run_in_threadpool`、`ingest_file`、`ingest_files_batch`、`ParserUnavailable`）、`services/llm.py:19`（`optional_import`）等。有意保留（`# noqa: F401`，勿删）：`worker/celery_app.py:73`（副作用注册）、`services/rag.py:195`、`services/optimizer.py:130`、`services/engines/doe_registry.py:20`、`services/simulator.py:47`（可选依赖探测）。

### 重复逻辑（P2，代表）
- `_match_catalog(cas, smiles, name)`：`external_alternatives.py:56` vs `literature_alternatives.py:18`，11 行完全一致。
- `_cache_get`/`_cache_put` ×3：`chemical_lookup.py:20,31`、`external_alternatives.py:36,47`、`surechembl_client.py:54,65`。
- `optimizer.py` 四个适配类 `best`/`ranked` 方法体相同；`campaign_store.py:831` vs `:986` `get_experiments` 一致；`_escape_like` ×3（entity/material/product store）；`entity_store.py:250` vs `:337` 内嵌 `_merge` 闭包一致；`api/settings.py:345` vs `:390`；`db/campaign_store.py:52` vs `services/notebooklm.py:66` `_run_async`；`neo4j_kg.py:554` vs `:581`。
- 前端：`NotebookLMPanel.tsx` vs `ProjectNotebookLMModal.tsx`（登录流程 UI 4 处重复）；`FormulationModeSelector.tsx:43-70` vs `WikiChatModeSelector.tsx:84-111`（28 行 radio 组）；`api/methods.ts:173-190` vs `api/types.ts:80-98`；`ConnectorsSettingsPanel.tsx` vs `SkillsSettingsPanel.tsx`；`HubGraphPane.tsx:130-171` vs `HubWikiPane.tsx:399-437`（42 行）。

### 无用依赖
**P2：**
- 后端 `pyproject.toml`：`neomodel>=4.2` 声明但全仓库零 import（实际用 `neo4j` 驱动）；反向问题：`neo4j` 被 import 但 `pyproject.toml` **未声明**（仅 `requirements.txt:52` 有）→ `pip install -e.` 会漏装。
- 前端 `package.json`：16 个 dependencies 逐个 grep 全部有 import，无残留。

### 注释/调试残留
- TODO/FIXME：后端 3 处（`db/campaign_types.py:24`、`middleware/api_auth.py:212`、`services/publication_preflight.py:34` 为正则误命中）；前端 0 处。
- `console.log`/`debugger`：前端 0 处；后端 `print(` 仅合法 CLI 输出；大段注释代码：无。

---

## 已验证无问题项（避免误报）

- 前端 `npx tsc --noEmit` 全绿；API 契约逐个核对全部对上（artifacts/reviews/manifest/collections/mcp/session-plans/memories/tech-reports）。
- 内存泄漏：轮询中心 `setInterval` 均清理；`URL.createObjectURL` 配对 `revokeObjectURL`。
- 未处理 promise：Wave 文件无裸 `.then(` 无 catch。
- 性能：前端 bundle 已治理（manualChunks + React.lazy，无 lodash 全量）；`async def` 内无 `time.sleep` 阻塞事件循环；`get_settings.cache_clear()` 只在配置变更路径；embedding 已批量；`query_aware_compression` tier2 单次批量调用。
- 后端：`recommend_diversity.py` 的 `Evidence` 未导入不触发运行时 NameError（`from __future__ import annotations`）；`publication_preflight` 状态机、`mcp_approval`（fail-closed）、SQL 全参数化均无问题；370 文件编译通过。

---

## 建议修复优先级

1. **P0 B-1**（artifact TOCTOU）：并发即可破坏 finalized 不可变，最先修。
2. **P1 B-3 + B-4**（Smart Collections 存储层）：丢数据 + 锁阻塞，一并重构（原子写 + 事务锁）。
3. **P1 B-7**（reviewer 静默失败）+ **B-6**（rigor 门禁失明）：信任链问题，同批。
4. **P1 B-9**（doe n=20）+ **B-2**（kg NameError）：小改动，顺手修。
5. **P1 B-5**（MCP 超时）+ **B-8**（run_id 碰撞）+ **B-12**（MCP 清理）：MCP 相关一并处理。
6. **性能 P1**：P-3（skills 缓存）改动最小先做；P-2（stale 缓存）利用 finalized 不可变 invariant；P-1（dashboard TTL）；P-4/P-5 并发化先 profiling。
7. **冗余**：R-1（CJK FTS 合一）防分叉；死代码按 P2→P3 分批删除；`neomodel`/`neo4j` 依赖声明修正。

## 方法局限

- 纯静态分析；P-4 的"40–60%"为估算，已标注需 profiling 验证。
- 引用统计把注释/字符串中的词也算作"使用"（保守策略，避免误报死代码）。
- 动态调用（getattr/字符串/celery 任务名/框架回调）已人工排除；"疑似"项需运行时验证。
- 本次审查只读，未做任何代码修改；修复需按 Cheng 流程（根因分析 → Markdown 方案审阅 → 执行 → 全绿测试）进行。
