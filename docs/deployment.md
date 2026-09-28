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
