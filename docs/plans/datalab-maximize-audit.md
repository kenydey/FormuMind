# DataLab 利用度审计 + 最大化方案（2026-09-02）

> 代码级 + 数据级实证（非推测）。范围：DataLab(pydatalab ELN) 在 FormuMind 中的实际角色、断链根因、最大化路径。

## 1. DataLab 是什么（设计定位）

- **pydatalab ELN**（datalab-org，Mongo 存储），FormuMind 通过 Headless HTTP API 读写（`/new-sample/` `/get-item-data/` `/save-item/` `/delete-sample/` `/upload/`），无 OAuth 直连（Mongo users=0）。
- 设计拓扑（deploy/eln/README）：**DataLab = 试验台账 + 训练记录 SSOT**；PostgreSQL/SQLite = FormuMind 元数据索引投影；Redis = Celery/SSE。
- 代码双实现：`DatalabCampaignStore`/`SqliteCampaignStore`、`DatalabExperimentStore`/`SqlExperimentStore`；配套 Saga 回滚、孤儿清理 outbox（datalab_orphan_cleanup）、reconciliation 对账、integrity 巡检——集成层已多轮加固，**代码成熟度 ~95%**。

## 2. 实际发挥（2026-09-02 运行态实证）

| 维度 | 实测结果 | 结论 |
|------|---------|------|
| Mongo 数据 | datalabvue: **29 items**（全 samples），item_versions 29 | 有真实写入历史 |
| 内容构成 | 28 个 DOE 行样品（formumind_c14~c17_r1-8，`params` 块 + **`measurements` 块全 null 模板**）；**1 个训练记录**（formumind_exp_d151c210，salt_spray **780h**，label wb:1:local_c1_r1，8/30 写入） | 1 条真数据 + 28 个空壳 |
| Campaign 后端 | settings `auto` + `FORMUMIND_DATALAB_REQUIRED=true` + 可达 → `DatalabCampaignStore`（campaign_store.py:886-896 **auto 有探测**）| ✅ **台账真在用 datalab**（17 campaigns，14-17 带 28 sample_refs 指向 datalab） |
| Experiment 后端 | settings `auto` → `get_experiment_store`（store.py:375-395）**auto 无探测分支** → 落 `SqlExperimentStore` | ❌ **训练记录后端 = sqlite** |
| 训练数据 | registry total_records=**0**（sqlite 视角）；datalab 那 1 条 780h 记录 sqlite 读不到 | 数据横幅「0 条」诚实，但真数据被晾 |
| measurements 表 | 0 行 | QC/实测从未落库 |
| QC/附件 | 无 qc 数据表行、experiment_attachments=0 | QC 通道从未有真实流 |

## 3. 断链根因（按因果排序）

1. **experiment store 的 auto 语义与 campaign 不对称**：campaign_store.py:898 有 `auto → 探测 datalab`，store.py:375 的 experiment auto 直接落 sqlite（config 注释承诺「auto 探测 Datalab→回退」，实现没给）。→ 台账(datalab) 与训练数据(sqlite) **分居两库**。
2. **两条训练写入路径分裂**：DOE 行测量回填（写 sample 的 `formumind_measurements` 块）与训练记录直写（`formumind_training` 块）是两套结构；registry 只认后者。28 个 DOE 行即使填了测量也不会自动进训练库。
3. **测量回填无人走**：28 个 measurements 块全是 null——「实测结果怎么进系统」的端到端路径从未被用户实际执行（workbench 行编辑→保存→回灌训练的闭环缺引导或未接通）。
4. **QC 报告通道闲置**：qc.py/qc_ingest/qc_report + `/upload/` 存在，但 0 数据、OCR 未接。
5. **历史残留**：8/30 datalab 模式写入的 29 items 在双后端分裂后成为「datalab 有、sqlite 无」的孤儿资产。

## 4. 最大化方案（叠加、零新依赖、按序）

### P1 修复后端选择不对称（半天）— 让 DataLab 名副其实
- `get_experiment_store` auto 分支补 datalab 探测（对齐 campaign_store.py:898 模式）：auto + 可达 → DatalabExperimentStore；auto + 不可达 → sqlite + 告警（保留兜底）。
- `.env.host` 显式 `FORMUMIND_CAMPAIGN_BACKEND=datalab`、`FORMUMIND_EXPERIMENT_BACKEND=datalab`（消除 auto 隐式行为，日志可确认）。dev/prod 行为一致化。
- 验证：重启后 `training-status` 应读到 datalab 那 1 条 780h → **total_records=1**（横幅首次真实反映数据）。

### P2 测量回填闭环接通（1-2 天）— 让 DOE 行数据能进训练库
- DOE 行 sample 的 `formumind_measurements` 块（28 个空模板）→ workbench 行保存时非空测量 → 自动生成/更新 `formumind_training` 记录（或新增「回灌训练」动作触发 registry.add），消除两套块分裂（统一以 measurements 块为源，training 记录为派生）。
- 前端：行状态「已填测量 ×/未填」+ 一键回灌 + 成功后横幅转绿（复用数据横幅）。
- 这直接复用 8/30 数据飞轮 CSV 导入链路（import-csv 已写 registry），把 datalab 行变成 CSV 之外的第二供料口。

### P3 QC 报告自动供料（1-2 天）— 最大量真实数据入口
- 你手上的 QC 证书/检测报告（PDF）→ 上传绑定台账行（qc.py + datalab `/upload/` source_document_id 已有）→ RapidOCR 提取实测值（salt_spray/adhesion/cost 等）→ 填 `formumind_measurements` 块 → 走 P2 回灌。
- 这是把「17 个历史 campaign + 证书库」变训练数据的现实路径（P2 的自动上游）。

### P4 单一后端收敛清理（半天）
- 以 datalab 为 SSOT：reconciliation.py 扩展一次同步——把 8/30 残留 29 items 的索引补回 sqlite（sample_refs/experiment rows），删重复孤儿；此后数据横幅与 DataLab 内容一致。

### P5 生产容器验证（0.5-1 天）
- docker-compose + .eln.yml 已设 datalab 后端但生产镜像未验证（frontend/worker 已删）。重建一键拉起冒烟，确认企业拓扑（PG 元数据 + DataLab SSOT + Redis）真实可运行。

## 5. 预期效果（P1-P3 后）
- 训练数据：0 → 数十~数百（QC 批量）真实盐雾/附着力值
- 横幅/寻优/推荐从「预测器回声」转「实验真收敛」（机制公式被真实数据校准的 blend 层 w=n/(n+8) 开始生效）
- DataLab 从「挂着的 ELN」变成名副其实的试验台账 SSOT：DOE 下发→实验→测量→训练→再推荐的闭环单源流转

## 6. 不建议
- 不引入新 ELN/不迁移 PostgreSQL（现状 PG 仅在容器拓扑；SQLite 元数据投影够用，迁移是运维噪音）
- 不做「前端直接嵌 DataLab UI」——保持 Headless API 单通道，避免双 UI 状态分裂
- 不建议在 P2 前先铺 QC OCR——回填管道不通，OCR 数据无出口
