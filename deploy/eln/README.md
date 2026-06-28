# 企业 ELN 部署（方案 D）

FormuMind 在企业场景下的推荐拓扑：

```text
PostgreSQL（元数据：项目 / campaign 索引 / 文献）
        ↕ item_id / sample_refs
Datalab Headless ELN（试验台账 + 训练样品 SSOT）
Redis（Celery + SSE，非业务库）
```

> **说明**：Datalab 内部使用 MongoDB 存储 ELN 数据，这是 Datalab 产品自身架构，**不是** FormuMind 应用层的数据库。FormuMind 仅通过 HTTP API 读写样品 block。

## 部署方式选择（推荐：同网双 Compose）

| 方式 | 适用 | 说明 |
|------|------|------|
| **同 Docker 网络双 Compose**（推荐） | 新建环境、PoC、中小团队 | Datalab 与 FormuMind 各用官方 compose，共享 `formumind-eln` 网络 |
| **独立 ELN 集群** | 已有企业 Datalab | 仅配置 `FORMUMIND_DATALAB_API_URL=https://eln.example.com` |

不推荐把 Datalab 的 MongoDB 合并进 FormuMind 主 compose —— 生命周期、备份、升级应独立管理。

## 快速启动（同网双 Compose）

```bash
# 1. 共享网络
docker network create formumind-eln 2>/dev/null || true

# 2. 启动 Datalab（官方仓库）
git clone https://github.com/datalab-org/datalab.git /opt/datalab
cd /opt/datalab
docker compose --profile prod up -d
# API 默认 :5001，需加入 formumind-eln 网络：
#   docker network connect formumind-eln datalab-api-1

# 3. 启动 FormuMind 企业 overlay
cd /path/to/FormuMind
docker compose -f docker-compose.yml -f docker-compose.eln.yml up -d
```

若 Datalab API 容器名为 `datalab-api-1`，FormuMind 默认 `FORMUMIND_DATALAB_API_URL=http://datalab-api:5001`。

## 环境变量（生产）

```bash
FORMUMIND_ENVIRONMENT=production
FORMUMIND_DB_URL=postgresql+psycopg2://user:pass@postgres:5432/formumind
FORMUMIND_CAMPAIGN_BACKEND=datalab
FORMUMIND_EXPERIMENT_BACKEND=datalab
FORMUMIND_DATALAB_REQUIRED=true
FORMUMIND_DATALAB_API_URL=http://datalab-api:5001
FORMUMIND_CELERY_EAGER=false
FORMUMIND_REDIS_URL=redis://redis:6379/0
```

安装 Postgres 驱动：`pip install -e ".[postgres]"`

## 健康检查

`GET /health` 返回：

- `database.ok` / `database.scheme` — PostgreSQL 连通性
- `datalab.reachable` / `datalab.message` — Datalab 可达性
- `status: degraded` — 当 ELN 必需但 Datalab 不可达

## Datalab 不可达行为

`campaign_backend=datalab` 时：

- **不会** 静默回退 sqlite 台账
- API 返回 **503**，`detail` 含中文修复指引
- 前端 DOE 弹窗显示具体错误

## Datalab Headless API 契约（FormuMind payload）

创建样品时 FormuMind 发送：

| 字段 | 正确值 | 常见错误 |
|------|--------|----------|
| `type` | `"samples"`（字符串） | `["samples"]`（列表） |
| `blocks_obj.*.blocktype` | `"comment"` | `block_type: "generic"` |

自定义 JSON 存放在 comment block 的 `data` 字段（`formumind_params` / `formumind_measurements`）。

开发/CI 仍可使用 `FORMUMIND_CAMPAIGN_BACKEND=sqlite`。
