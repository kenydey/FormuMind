# Kubernetes 生产模板

## 前置条件

- 集群已安装 **ingress-nginx**
- **Datalab ELN** 已部署（建议独立 namespace `datalab`），FormuMind 可通过集群 DNS 访问其 API
- 存储类支持 **ReadWriteMany**（共享 PVC）— 若仅 RWO，请将 worker 固定为 1 副本或禁用 ColBERT 共享索引

## 1. 构建并推送镜像

```bash
docker build -f deploy/production/Dockerfile.backend \
  -t YOUR_REGISTRY/formumind-backend:prod ./backend
docker build -t YOUR_REGISTRY/formumind-frontend:prod ./frontend
docker push YOUR_REGISTRY/formumind-backend:prod
docker push YOUR_REGISTRY/formumind-frontend:prod
```

## 2. 配置 Secret

```bash
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml
# 编辑密码、FORMUMIND_DB_URL、FORMUMIND_REDIS_URL、API Token
kubectl apply -f deploy/k8s/secret.yaml
```

`FORMUMIND_DB_URL` 中的密码须与 `FORMUMIND_PG_PASSWORD` 及 Postgres StatefulSet 一致。

## 3. 修改 Kustomize 镜像与 Datalab URL

编辑 [kustomization.yaml](./kustomization.yaml)：

```yaml
images:
  - name: formumind-backend
    newName: YOUR_REGISTRY/formumind-backend
    newTag: prod
```

编辑 [configmap.yaml](./configmap.yaml) 中的 `FORMUMIND_DATALAB_API_URL`。

## 4. 部署

```bash
kubectl apply -k deploy/k8s
```

## 5. 扩缩容

```bash
# 手动
kubectl -n formumind scale deployment/formumind-backend --replicas=3
kubectl -n formumind scale deployment/formumind-worker --replicas=5

# 或启用 HPA（已包含 hpa.yaml，需 metrics-server）
kubectl -n formumind get hpa
```

Kustomize `replicas` 字段可设初始副本数。

## 健康探针

| 工作负载 | Liveness | Readiness |
|----------|----------|-----------|
| backend | `GET /health` | Python 脚本：`database.ok` 且 Datalab 可达 |
| worker | Redis PING | Redis PING |
| frontend | `GET /` | `GET /` |
| postgres | `pg_isready` | `pg_isready` |
| redis | `redis-cli ping` | `redis-cli ping` |

> `/health` 在 Datalab 不可达时仍返回 HTTP 200（`status: degraded`），因此 readiness 使用 exec 探针解析 JSON。

## Ingress 与 SSE

[ingress.yaml](./ingress.yaml) 包含两个 Ingress：

1. **formumind** — 常规 `/api/`、`/` 路由
2. **formumind-sse** — `/api/tasks/.+/stream`，注解关闭 buffering、86400s 超时

请将 `formumind.example.com` 改为真实域名，并配置 TLS（cert-manager 等）。

裸 Nginx 等价片段见 [nginx-sse-snippet.conf](./nginx-sse-snippet.conf)。

## 共享 PVC

`formumind-shared-data`（RWX）挂载于 backend 与 worker 的 `/app/data`：

- ColBERT 索引目录
- 任务磁盘 fallback（Redis 为主路径，磁盘仅降级）

生产建议：**托管 Redis HA** + 可选去掉 RWX（若不用 ColBERT）。

## 外部 RDS / ElastiCache

1. 不部署 `postgres.yaml` / `redis.yaml`（从 kustomization 移除）
2. 在 `secret.yaml` 设置 `FORMUMIND_DB_URL` / `FORMUMIND_REDIS_URL` 为外部地址

## Datalab 网络

确保 FormuMind Pod 能解析并访问 Datalab Service，例如：

```yaml
FORMUMIND_DATALAB_API_URL: "http://datalab-api.datalab.svc.cluster.local:5001"
```

跨 namespace 访问需 NetworkPolicy 放行。

## 验收

```bash
kubectl -n formumind port-forward svc/formumind-backend 8000:8000
curl -s localhost:8000/health | jq .
# database.ok == true, datalab.reachable == true

# 经 Ingress 提交异步任务，观察 SSE /api/tasks/{id}/stream
```
