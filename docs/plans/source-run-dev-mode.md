# 开发调试模式「源码运行」可行性评估

- **状态**：待评审
- **日期**：2026-09-01
- **背景**：FormuMind 开发迭代中，Docker 每次改代码要重建镜像（backend 3.8GB × 频繁构建 + 磁盘 94% 满），用户想改源码运行减少空间与重建频率

## 一、结论先行：✅ 可行，但要分级

**「backend + worker + frontend 三件套源码运行」完全可行且收益最大**；**基础设施（redis/neo4j/molscribe/datalab）保持容器化**是最优解——它们不随代码变化，容器化反而是资产。

| 组件 | 现内存 | 源码化 | 理由 |
|---|---|---|---|
| **backend** (FastAPI) | 1.04GB | ✅ **强烈建议** | host venv 已完整（uvicorn/celery/rdkit/baybe/colbert 全在），只缺 neo4j 驱动 |
| **worker** (Celery) | 140MB | ✅ 建议 | 与 backend 同 venv，一条命令启动 |
| **frontend** (Vite) | 8MB | ✅ 建议 | node_modules 271M 完整，`vite dev` 热更新，改代码即生效 |
| **redis** | 4MB | 🔵 保持容器 | host 有 redis-server 但容器零配置更省心；不随代码变 |
| **neo4j** (KG) | 160MB | 🔵 保持容器 | host 无 neo4j，装 500MB+ 不值；数据在容器卷里 |
| **molscribe** | 611MB | 🔵 **必须容器** | 依赖特殊（torch 2.3.0+cpu 独立环境），host venv 会冲突 |
| **datalab** (api+db) | 158MB | 🔵 保持容器 | 独立数据后端（campaign/experiment 数据），与主代码解耦 |
| **freellmapi** | 46MB | 🔵 保持容器 | 边缘服务，不动 |

**核心收益**：backend/worker/frontend 改动 → 重启进程即生效（秒级），不再 3.8GB 镜像重建 + 磁盘从 94% 降到 ~70%。

## 二、当前环境侦察结果（实测）

- **内存**：6.3GB 总，容器已占 ~2.2GB（backend 1.04G 是最大头），可用 2.7GB。**backend 移出容器直接释放 1GB+**
- **磁盘**：94% 满（51/57G），Docker 镜像 22.5GB 是主要占用
- **host venv** `/root/FormuMind/backend/.venv`：fastapi/uvicorn/celery/rdkit/baybe/colbert/rank_bm25/jieba **全部可用**，仅缺 `neo4j` 驱动（pip 一条命令）
- **host redis**：`/usr/bin/redis-server` 存在
- **frontend**：node_modules 271M 完整，vite 可运行

## 三、架构对比

```
现在（全容器）                    源码模式（推荐）
┌─────────────────────┐          ┌─────────────────────┐
│ backend  1.04GB ◀───┼──重建──┐  │ backend  host uvicorn│◀──改代码→重启进程(秒)
│ worker    140MB     │        │  │ worker   host celery │
│ frontend    8MB     │        │  │ frontend vite dev    │◀──热更新(即时)
│ redis      4MB      │        │  ├─────────────────────┤
│ neo4j    160MB      │        │  │ redis    (容器,不动)  │
│ molscribe 611MB     │        │  │ neo4j    (容器,不动)  │
│ datalab   158MB     │        │  │ molscribe(容器,不动)  │
│ freellmapi 46MB     │        │  │ datalab  (容器,不动)  │
└─────────────────────┘          └─────────────────────┘
改 backend 代码: 重建3.8GB镜像≈3-6min  改代码→重启 uvicorn≈2s
```

## 四、实施步骤（评审通过后执行）

1. **backend host 化**：
   - `pip install neo4j`（补唯一缺的驱动）
   - 从 compose 复制环境变量（`FORMUMIND_*`）到 host `.env`——用现有 `./data/.env` 机制（config.py 已支持）
   - 停 `formumind-backend-1` 容器，host 起 `uvicorn app.main:app --port 8000`
2. **worker host 化**：同 venv 起 `celery -A app.worker.celery_app.celery_app worker --queues=celery --pool=solo`
3. **frontend host 化**：`npx vite dev --port 5173`（代理指向 localhost:8000）
4. **验证**：health 200 + 全量测试 + 端到端（结构图上传链路）
5. **基础设施不动**：redis/neo4j/molscribe/datalab/freellmapi 容器照常

## 五、风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| 内存不够（host venv + 容器同时跑） | 中 | backend 移出容器释放 1GB，净增负微乎其微；swap 5.5G 兜底 |
| host 环境与容器环境不一致（依赖版本漂移） | 中 | 用**同一 venv**（已与容器同 torch 2.3.0+cpu）；部署前先跑全量测试 |
| neo4j 驱动缺失导致 KG 功能挂 | 低 | 明确 pip 补装；KG 是容器服务，驱动只需连得上 |
| 端口冲突（容器还开着 backend:8000） | 低 | 停容器后再起 host 进程，或用 8001 过渡 |
| 改回容器模式麻烦 | 低 | 一键 `docker compose up -d` 即可回滚，host 进程 kill 掉 |

## 六、空间/时间量化收益

| 指标 | 现在 | 源码模式 | 收益 |
|---|---|---|---|
| 改 backend 代码生效 | 3-6 分钟（重建镜像） | ~2 秒（重启进程） | **~100x** |
| 改前端代码生效 | 重建 + 部署 | vite 热更新即时 | **即时** |
| Docker 磁盘占用 | 22.5GB | ~18GB（停 2 个大镜像） | -4.5GB |
| 磁盘使用率 | 94% | ~80% | 安全余量 |
| 全量测试 | 容器内跑（慢） | host venv 直接跑 | 快且直观 |

## 七、推荐结论

**建议实施「半容器」模式**：代码三件套（backend/worker/frontend）源码运行，基础设施（redis/neo4j/molscribe/datalab）保持容器。这是开发期最优解——既不损失容器化基础设施的便利，又消除最频繁的构建痛点。正式发布时一键切回全容器。
