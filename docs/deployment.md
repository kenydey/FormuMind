# FormuMind 部署指南

一页生产部署说明。开发环境搭建见 `docs/QUICKSTART.md`。

## 环境要求

- Docker Engine 24+ / Compose v2（生产推荐；本沙箱内核限制容器无法启动，仅做配置校验）
- 或：Python 3.11+、Node 20+、Redis 7（裸机部署）
- 磁盘：`./data` 卷至少 20 GB（SQLite + ColBERT 索引 + 备份）

## 配置

1. 复制环境变量模板并填写：
   ```bash
   cp .env.example .env   # 若无模板，直接新建 .env
   ```
2. 必需项（缺失则后端启动直接 fail-fast，见 `backend/app/startup_checks.py`）：
   - `FORMUMIND_API_TOKEN` —— 生产 bearer 令牌（`docker-compose.prod.yml` 强制开启鉴权）
   - `FORMUMIND_DB_URL` —— 默认 `sqlite:///./data/formumind.db`；多 worker 生产改 `postgresql://…`
   - `FORMUMIND_DATALAB_API_URL` —— 仅当 `FORMUMIND_DATALAB_REQUIRED=true` 时必需
3. 可选项：LLM vendor key 缺失时自动降级为离线实现，不阻塞启动。

## 启动

```bash
# 开发（默认，鉴权关闭，端口全开）
docker compose up -d

# 生产（鉴权强制开、environment=production、前端仅绑 127.0.0.1）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

健康检查：`GET http://localhost:8000/health`（compose healthcheck 已配）。
前端经反向代理对外时，在代理层加 TLS；不要直接把 5173 暴露到公网。

## 端口与凭据

- **Redis（6379）与 Neo4j（7474 / 7687）只发布在 `127.0.0.1`**：宿主机上的脚本照常可用，网络上的其他机器不能直连
  （Redis 没有密码，Neo4j 的默认密码在仓库里是公开的）。确需远程访问，请自行在防火墙 / 隧道后显式发布。
- **Neo4j 密码走 `.env`**：在**第一次**启动 `kg` 之前设置 `FORMUMIND_NEO4J_PASSWORD`。Neo4j 只在初始化 `data/neo4j` 时读取它，
  之后改变量不会改库里的密码——已有安装需先在库里改密码（`cypher-shell` → `ALTER CURRENT USER SET PASSWORD FROM '旧' TO '新'`），
  再设置变量。未设置时沿用旧默认值，保证已初始化的安装不会因升级而连不上；`FORMUMIND_ENVIRONMENT=production` 且启用了
  Neo4j 时，后端启动会为默认密码打一条警告。

## 运维诊断接口（只有 API，没有界面）

这些接口是给运维看的，刻意不做界面。`python scripts/ops_check.py` 一次读完并打印一页（`--strict` 有发现时退出码为 2，可进 cron / 监控）：

| 接口 | 看什么 |
|---|---|
| `GET /api/ops/kb-health` | 知识库：解析器分布、向量覆盖率、空文档率（索引不可用时 `available=false`） |
| `GET /api/ops/evidence-stats` | 证据压缩（第二层 LLM）触发率与节省的 token；进程内计数，重启清零 |
| `GET /api/ops/recommend-stats?days=N` | 推荐被采纳 / 被实验验证的比例（需要 `recommend_outcomes` 表） |
| `GET /api/kb/relevance-shadow/stats` | 主题相关性"影子模式"的拒绝率与重叠度——决定何时把 `kb_relevance_shadow` 切到强制 |
| `GET /api/ops/datalab-orphans` | 回滚时没能从 DataLab 删掉的样本：`PENDING` 待清理、`DONE` 已清、`DEAD` 放弃（需手工删） |
| `POST /api/ops/datalab-orphans/cleanup` | 立即重试 `PENDING`（启动时也会跑一遍；DataLab 未配置 / 不可达时不消耗重试次数） |

## 可选：主题雷达

`formumind.topic_sweep` 周期性检索某个主题，按主题筛选后把相关文献在后台回填知识库。

- 手动触发一次：`POST /api/search/topic-sweep`（`{"query": "...", "project_id": "..."}`，返回 202 任务句柄，
  `GET /api/tasks/{id}` 看 `found` 与 `ingest_task_id`）。
- 周期触发：在 `.env` 设置 `FORMUMIND_TOPIC_RADAR_ENABLED=true` 与 `FORMUMIND_TOPIC_RADAR_TOPICS`（JSON 数组，
  每项 `query` / `project_id` / `cron`（分 时 日 月 周，默认每周一 01:00）/ `total_limit` / `source_types`），
  然后起一个 beat 进程：`docker compose --profile radar up -d`。**每个部署只起一个 beat**，否则每次扫描会触发两遍。
  非法的条目会被跳过并写一条警告，不会让 worker 起不来。

## 备份与恢复

```bash
# 一键备份（SQLite 在线快照，可在服务运行时执行）
bash scripts/backup_db.sh                       # -> ./data/backups/formumind-<时间戳>.db

# 恢复（先停服务；自动校验备份完整性，并先把当前库另存为 pre-restore-*）
docker compose stop backend worker
bash scripts/restore_db.sh ./data/backups/formumind-<时间戳>.db
docker compose start backend worker
```

建议用 cron 每天凌晨备份一次；备份目录在 `./data/backups`，随数据卷一起持久化。

## 常见故障

| 现象 | 排查 |
|------|------|
| 后端启动即退出，日志 `Startup configuration check failed` | 按错误信息补 `.env` 缺失项（token / DB URL / Datalab URL） |
| 日志 `SQLite ... production ... postgresql` 警告 | 单机可忽略；多 worker 生产必须切 Postgres（SQLite 写锁） |
| 前端 502 / 空白 | 先看 `backend` healthcheck 是否 healthy，再看 `docker compose logs backend` |
| 401 UNAUTHORIZED（ELN 相关） | `FORMUMIND_DATALAB_API_TOKEN` 缺失或错误，值放 `.env` |
| 磁盘满（Errno 28） | 清理 `./data/backups` 旧备份；检查 pytest 临时目录 |

## 回滚

代码回滚：`git checkout <上一个 tag/commit>` 后重建镜像。
数据恢复：用 `scripts/restore_db.sh` 恢复最近一次备份。
