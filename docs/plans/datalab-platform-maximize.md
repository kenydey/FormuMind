# DataLab 平台功能最大化利用方案（FormuMind × pydatalab）

> 状态：待评审 | 日期：2026-09-02 | 范围：FormuMind ↔ DataLab(pydatalab) 集成层
> 前序：`datalab-maximize-audit.md`（数据利用审计，P1-P4 已执行）

## 1. 背景结论（实证）

pydatalab 平台 14 个 API 模块 / ~70 端点，FormuMind 实际只调用 5 个端点
（items CRUD ×4 + files 上传 ×1，后者 2026-09-02 刚修通错路由）。**平台被当成
「带 JSON 块的样品数据库」，约 7% 端点、0% 平台差异化能力被利用。**

已核实的关键平台契约与事实：

| 事实 | 值 |
|---|---|
| 平台 | pydatalab（Chemotion 系 ELN），`datalab-api-1` + Mongo `datalabvue` |
| 暴露面 | **0.0.0.0:5001 公网可达 + TESTING 公开模式（无鉴权）= 安全风险** |
| item 身份 | item_id（自定义 `formumind_cXX_rY_hash`）+ refcode（平台自动 `test:XXXXXX`） |
| 版本控制 | 每次 save-item 自动留版本（HasRevisionControl），版本 API 以 **refcode** 寻址 |
| 关系类型 | parent / child / sibling / is_part_of / other（无 campaign 语义类型） |
| collections | 集合可嵌 items、可挂集合级数据块、组权限 |
| files | 上传/下载/删除已通（FormuMind 侧 2026-09-02 修复） |
| 块体系 | FormuMind 用 CommentBlock 存任意 JSON（绕开平台结构化 schema） |

## 2. 目标与原则

- **叠加模式**：全部方向在现有 items+JSON 块架构上叠加，不迁移、不二选一
- 每方向独立可交付、可验证、可回滚（不做平台 schema 大迁移）
- 尊重 VPS 硬约束（4 核无 AVX2、~2.3GB 可用内存）：方向均为轻量 API 接线，无重计算
- 明确**不做**：把 formumind_* JSON 块迁移到平台结构化 block schema（高成本零收益，P5 弃）

## 3. 方向与优先级

```
P0 安全卫生（0.5天）      P1 项目组织（0.5天）       P2 谱系图（1天）
    TESTING→token 鉴权       campaign→collection        DOE 行谱系 + 关系图
    公网暴露收敛              + 集合级状态块              + 平台原生可视化
        │                        │                          │
P3 版本回看（1天）          P4 文件闭环（0.5天）       （弃）块 schema 迁移
    sync 即版本历史            QC 证书平台下载/预览         P5 明确不做
    对比/恢复入口              + 前端附件直读平台
```

### P0 — 安全卫生（建议最先）
**问题**：datalab-api 公网 0.0.0.0:5001 可达，且 `PYDATALAB_TESTING=true` →
全部写端点（save-item/delete-sample/upload-file）无鉴权公网可调。FormuMind
与 datalab 同机，无需公网暴露。

- 收敛暴露：docker compose 端口映射 `127.0.0.1:5001:5001`（仅本机）
- 鉴权：平台 auth 签发 token（`/auth/token` 类端点）→ FormuMind `httpx` 客户端
  统一加 `Authorization` header；关闭 TESTING
- 改动：`docker-compose*.yml`（端口）、`campaign_store._ensure_client` headers、
  `datalab_client` 各函数透传 token、config 加 `datalab_api_token`
- 风险：TESTING 关闭后 secret key/登录行为变化 → 先本机验证全链路再收敛端口

### P1 — campaign → collection 项目组织化
**价值**：DataLab UI 中每个 DOE campaign 成为可浏览项目集；为 P2 谱系与平台
搜索/导出打底；FormuMind 前端可跳转平台看项目全貌。

- campaign 下发时（`create_from_plan` / `batch_sync` 首行创建）：`POST /collections`
  建 `FM-C{campaign_id} {campaign.name}` → `POST /collections/<id>/items` 批量归入
  DOE 行 items → 可选集合级块存 campaign 状态（`/add-collection-data-block/`）
- campaign 元数据落 sqlite（collections 表加 `datalab_collection_id` 列）
- 幂等：按 name 查询 `/search/collections/` 防重复建
- 改动：`campaign_store.py`（下发/删除同步）、迁移 sqlite campaigns 表、存量
  28 行一次性回填脚本
- 风险：collection 删除语义（平台 DELETE collection 级联？）→ 只建不删，删除
  留手动；改名不同步

### P2 — DOE 谱系 + 关系图
**价值**：平台原生 `/item-graph` 可视化 DOE 族谱（campaign 父节点 → 行节点），
配方演化一目了然；也为平台 export 带 related items 铺路。

- 语义：每 campaign 建父样品 item（`formumind_cXX_root`，type=samples，存
  campaign 级 params）→ DOE 行 items 建关系 `is_part_of` 父样品（RelationshipType
  无 campaign 类型，用 `is_part_of` + description 标注 `formumind-campaign`）
- new-sample payload 支持 constituents/relationships（已核实 337-372 行处理）
- 改动：`campaign_store` 下发时建父样品 + 行级关系；存量 28 行补关系脚本
- 风险：平台图遍历深度/性能（28 行小规模无虞）；关系删除语义待实测
- 验证：GET /item-graph/<campaign_root> 返回 28 行节点

### P3 — 实验版本历史回看
**价值**：零采集成本——每次 workbench sync 的 save-item 平台已自动留版本。
接入后 DOE 行参数/测量变更可对比、可恢复，实验全程可审计。

- 读侧：`GET /items/<refcode>/versions/`（refcode 从 item 记录取）→ DOE 行详情
  展示版本数 + 最近变更时间；`/compare-versions/` 对比差异摘要
- 写侧：FormuMind 保留「平台历史版本」概念，不做 restore（避免与本地状态冲突）
- 改动：`datalab_client` 加 2 个读方法；前端 DOE 行详情小面板（版本数+时间线）
- 风险：refcode 前缀 `test:` 随 P0 关闭 TESTING 会变化（正式前缀）→ refcode 需
  读回缓存，不硬编码

### P4 — 文件闭环（QC 证书平台化）
**价值**：QC 报告/附件已能归档进平台（P3 前修），补读侧闭环：附件列表可预览/
下载平台原件。

- 读侧：`GET /files/<file_id>/<filename>`（attachment.note 已记 `[datalab:file_id]`）
- 前端：AttachmentPreview 增加「平台原件」预览/下载按钮（file_id 已解析）
- 改动：`datalab_client.get_file` + 前端小改
- 风险：低（契约已实测）

## 4. 文件变更清单

| 文件 | P0 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|
| docker-compose*.yml（端口收敛） | ✅ | | | | |
| backend/app/config.py（datalab_api_token） | ✅ | | | | |
| backend/app/db/campaign_store.py（鉴权头/collection 同步/父样品） | ✅ | ✅ | ✅ | | |
| backend/app/db/datalab_client.py（token 透传/新读方法） | ✅ | ✅ | ✅ | ✅ | ✅ |
| backend/app/db/models.py + 迁移（collection_id 列） | | ✅ | | | |
| backend/app/api/experiments.py（campaign 详情返回 collection/refcode） | | ✅ | ✅ | ✅ | |
| backend/tests/*（契约测试） | ✅ | ✅ | ✅ | ✅ | ✅ |
| frontend/src/components/*（DOE 行详情版本/附件面板） | | | | ✅ | ✅ |
| 存量回填脚本 scripts/dev/（28 行归 collection+父样品） | | ✅ | ✅ | | |

## 5. 实施步骤与时间表

| 步骤 | 内容 | 耗时 | 依赖 |
|---|---|---|---|
| P0.1 | 本机验证 token 鉴权链路（关 TESTING → 全 API 带 token smoke） | 0.5d | 无 |
| P0.2 | compose 端口收敛 127.0.0.1 + 部署 | 0.25d | P0.1 |
| P1.1 | collection 创建/归入客户端方法 + 幂等 | 0.25d | 无 |
| P1.2 | campaign 下发/删除同步 + 存量回填 | 0.25d | P1.1 |
| P2.1 | 父样品创建 + is_part_of 关系（下发时） | 0.5d | P1（父样品可独立） |
| P2.2 | 存量行补关系 + 图验证 | 0.25d | P2.1 |
| P3.1 | 版本读 API + DOE 行版本面板 | 1d | P0（refcode 稳定） |
| P4.1 | 文件读回 + 前端预览 | 0.5d | files 上传已通 |
| Σ | | ~4 天 | |

## 6. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| TESTING 关闭致全线 401/secret key 变化 | 中 | 高 | P0 先本机全链路 smoke 再收敛端口；保留回滚开关 |
| refcode 前缀随环境变化（test:→正式） | 高 | 低 | refcode 读回缓存不硬编码；P3 依赖 P0 先行 |
| collection 删除级联语义未知 | 中 | 中 | 只建不删，删除留平台手动 |
| 平台关系删除/更新 API 契约未实测 | 中 | 中 | P2 先小批量验证再全量回填 |
| 版本数据量增长 | 低 | 低 | 28 行 × sync 频率，量级可忽略 |
| 平台升级 API 漂移 | 低 | 中 | 全部调用收敛在 datalab_client，单一改动面 |

## 7. 验证方式（每个 P 交付标准）

- P0：关 TESTING 后无 token 请求 401 / 带 token 全链路 200；5001 仅本机可达
- P1：Mongo collections 集合出现 FM-C14…C17 4 个集合，28 items 归入正确；
  campaign 删除后行与集合关系不残留（只建不删）
- P2：`GET /item-graph/<root>` 返回 28 节点；平台 UI 关系图可浏览
- P3：sync 后版本数 +1；compare-versions 差异正确
- P4：附件列表平台原件可下载，内容哈希与上传一致
- 每个 P：相关 pytest 全绿 + 真实 HTTP smoke + 浏览器端到端点击

## 8. 建议路线

P0 → P1 → P2 连续执行（共享 campaign_store 改动面，一次 dev 验证周期）；
P3/P4 视前端迭代窗口排后。P5（schema 迁移）明确不做。

## 9. 执行记录（2026-09-02）

**P0 安全卫生 ✅** — compose 端口收敛 127.0.0.1 + TESTING=false + 高熵 SECRET_KEY +
注入服务用户/api_keys（key 存 /root/datalab/.fm_api_key）。FormuMind 全调用点
DATALAB-API-KEY 头（commit 8096083）。**深坑**：平台写路径权限过滤器用
PyObjectId，存量 items creator_ids(string) 永不匹配 → 归属迁移为 ObjectId；
切换中间态 404 曾误触发 list_rows auto-prune 清空 c14 sample_refs（已重建）。
QC 归档在鉴权模式下实测成功。测试隔离：conftest 后端限 sqlite（5f1bd7f）。

**P1 campaign→collection ✅** — 下发时自动建 formumind_campaign_{id} 集合
（幂等/自动重建/失败吞掉），sqlite 加 datalab_collection_id 列，存量 c14-17
回填完成：Mongo 4 collections、28/29 items 挂载（4eae8b6）。
验证：campaigns API 返回 collection_id；item-graph?collection_id= 真实可用
（c14: 6 节点 8 边）。

**P2 谱系 → 收敛 ✅** — 「父样品人造谱系」实现层证伪弃置：
1) 平台无运行时关系写 API（仅创建时 constituents；save-item 显式剔除
relationships）；2) DOE 行是并列变体非层级组成，人造 parent 边=虚假语义；
3) 平台 Vue UI 容器本就禁用（无 OAuth 登录链，API 又已收敛本机）——「平台
UI 浏览图」在当前部署不可达。替代交付：collection 组织（P1）+ 图 API 数据
就绪（P2 验证）+ campaign 详情暴露 collection_id（154b21a）。若未来要真实
谱系（如 DOE 轮次父子），需平台补关系端点，属 pydatalab 侧开发。

**P3/P4 未做**（原方案即排后）：P3 版本回看（refcode 寻址已稳定 test: 前缀）、
P4 文件读回闭环（上传侧已通）。建议下轮做，或先跑通真实业务数据。
