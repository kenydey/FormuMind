# FormuMind 生产部署 Manifest 草稿

面向 **必须 Datalab ELN + 多 Worker/多副本** 场景。包含 Docker Compose 与 Kubernetes 两套模板。

## 目录结构

```text
deploy/
├── eln/README.md              # Datalab 叠加说明（已有）
├── production/
│   ├── README.md              # Compose 生产指南
│   ├── .env.example
│   ├── Dockerfile.backend     # 含 psycopg2 的后端镜像
│   ├── docker-compose.prod.yml
│   └── nginx/formumind.conf   # 边缘 Nginx（SSE + API + SPA）
└── k8s/
    ├── README.md              # Kubernetes 指南
    ├── kustomization.yaml
    ├── namespace.yaml
    ├── configmap.yaml
    ├── secret.example.yaml
    ├── pvc.yaml               # 共享 RWX + Postgres/Redis RWO
    ├── postgres.yaml
    ├── redis.yaml
    ├── backend-deployment.yaml
    ├── worker-deployment.yaml
    ├── frontend-deployment.yaml
    ├── ingress.yaml           # ingress-nginx + SSE 注解
    ├── hpa.yaml               # 自动扩缩容
    └── nginx-sse-snippet.conf # 裸 Nginx 参考片段
```

## 架构要点

| 组件 | 作用 | 多副本 |
|------|------|--------|
| **PostgreSQL** | FormuMind 业务库 | 单主库（建议托管 RDS） |
| **Redis** | Celery + SSE Pub/Sub | 单 HA 实例（必须可达） |
| **Datalab** | ELN SSOT（内部 MongoDB） | 独立集群，HTTP API |
| **Backend** | FastAPI | 水平扩展 |
| **Worker** | Celery | 水平扩展 |
| **Nginx / Ingress** | 入口 + SSE 禁缓冲 | 2+ 副本可选 |

## 快速选择

| 环境 | 使用 |
|------|------|
| PoC / 中小团队 VM | `deploy/production/docker-compose.prod.yml` |
| 企业 K8s | `deploy/k8s/` + `kubectl apply -k` |

详细步骤见各子目录 README。
