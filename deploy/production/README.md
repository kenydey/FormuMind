# Docker Compose 生产模板

## 前置条件

1. 创建共享网络并启动 **Datalab ELN**（见 [../eln/README.md](../eln/README.md)）：

```bash
docker network create formumind-eln
# 在 /opt/datalab 启动官方 compose，并将 API 容器接入 formumind-eln
docker network connect formumind-eln datalab-api-1
```

2. 复制环境变量：

```bash
cp deploy/production/.env.example .env
# 编辑 FORMUMIND_PG_PASSWORD、FORMUMIND_DATALAB_API_URL、API Token 等
```

## 构建与启动

```bash
docker compose -f deploy/production/docker-compose.prod.yml up -d --build
```

入口：**http://localhost:8080**（`FORMUMIND_HTTP_PORT` 可改）

## 水平扩缩容

Compose v2 使用 `--scale`（非 Swarm 的 `deploy.replicas`）：

```bash
docker compose -f deploy/production/docker-compose.prod.yml up -d \
  --scale backend=3 \
  --scale worker=4
```

- **backend**：多副本由 Nginx `upstream backend:8000` 轮询（Docker DNS 解析全部容器）
- **worker**：共享同一 Redis 队列，自动竞争任务
- **SSE**：Worker 写 Redis Pub/Sub，任意 backend 副本可订阅 — **无需会话粘滞**

## 共享卷

| 卷名 | 挂载点 | 用途 |
|------|--------|------|
| `formumind_shared_data` | `/app/data` | ColBERT 索引、任务磁盘 fallback、上传缓存 |
| `postgres_data` | PG 数据目录 | FormuMind 业务库 |
| `redis_data` | Redis AOF | 队列持久化（建议生产改用托管 Redis） |

## 健康检查

- **backend**：除 HTTP 200 外，还要求 `database.ok` 且 Datalab 可达（readiness 脚本）
- **worker**：Redis `PING`
- **nginx**：代理 `/health`

## Nginx SSE 片段

完整配置见 [nginx/formumind.conf](./nginx/formumind.conf)。核心：

```nginx
location /api/tasks/ {
    proxy_pass http://formumind_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 86400s;
    add_header X-Accel-Buffering no;
}
```

## 生产检查清单

- [ ] `FORMUMIND_CELERY_EAGER=false`
- [ ] `FORMUMIND_CAMPAIGN_BACKEND=datalab` + `FORMUMIND_DATALAB_REQUIRED=true`
- [ ] `FORMUMIND_DB_URL` 指向 Postgres（compose 已自动组装）
- [ ] Datalab API 在 `formumind-eln` 网络可达
- [ ] Redis 健康；Worker ≥ 2
- [ ] 提交闭环任务，SSE 在扩缩 backend 后仍正常

## 外部托管 DB/Redis

在 `.env` 中覆盖：

```bash
FORMUMIND_DB_URL=postgresql+psycopg2://user:pass@rds.example.com:5432/formumind
FORMUMIND_REDIS_URL=redis://:pass@elasticache.example.com:6379/0
```

并从 compose 中移除 `postgres` / `redis` 服务（或 profile 禁用）。
