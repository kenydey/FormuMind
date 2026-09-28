# 向量检索规模天花板评估（2026-09-28，实测）

> Wave 4 交付物：只出数字和决策，不做迁移。

## 压测方法

脚本：`backend/scripts/bench_vector_ceiling.py`。合成归一化 float32 向量，
模拟 SQLite 里 JSON 反序列化后的 embedding 列表；测 `hybrid_search.py`
实际使用的两条 cosine 路径：

- **loop 路径**：`kb_index._dot` 逐 chunk 纯 Python 点积（ANN 门关闭时的生产路径）
- **matrix 路径**：float32 `mat @ q`（ANN stage-2 路径）

每规模 30 次查询取 p50/p95；内存为矩阵本身 + 进程 RSS 峰值。
维度取线上两个真实值：384（MiniLM-L6，默认英文）/ 512（bge 中文）。

## 压测数字（本机实测）

| dim | chunks | loop p50 | loop p95 | mat p50 | mat p95 | 矩阵内存 | 说明 |
|-----|--------|----------|----------|---------|---------|----------|------|
| 384 | 10k    | 255 ms   | 334 ms   | 0.3 ms  | 0.6 ms  | 15 MB    | |
| 384 | 50k    | 1164 ms  | 1212 ms  | 1.8 ms  | 2.4 ms  | 73 MB    | |
| 384 | 100k   | 2361 ms  | 2428 ms  | 3.5 ms  | 14.7 ms | 147 MB   | RSS 峰值 ~1.7 GB |
| 512 | 10k    | 310 ms   | 341 ms   | 0.3 ms  | 0.6 ms  | 20 MB    | |
| 512 | 50k    | 1591 ms  | 1673 ms  | 2.2 ms  | 2.9 ms  | 98 MB    | |
| 512 | 100k   | 3140 ms  | 3542 ms  | 4.9 ms  | 9.4 ms  | 195 MB   | RSS 峰值 ~2.2 GB |

另：`kb_index._dot` 注释中已有历史实测——5000 chunks / 384d ≈ 62 ms，
与本次线性外推（10k → 255 ms）趋势一致。

## 三个天花板（按约束强度排序）

1. **召回截断（最硬）**：`kb_search_scan_limit = 5000`（`config.py:768`）。
   `hybrid_search_scored` 只从 chunk store 取前 5000 个 chunk 参与打分，
   超出部分静默丢弃（仅一条 warning 日志）。向量再快也够不着第 5001 个 chunk。
2. **延迟**：loop 路径约 25–30 µs/chunk（384d）。5000 chunks ≈ 150 ms；
   10k ≈ 300 ms；触及 ANN 门 p95 阈值 800 ms 约在 25k chunks。
   matrix 路径 100k 仍 < 15 ms，但需先把 JSON list 物化成矩阵
   （5000/384d 时物化成本反而高于 loop，见 `_dot` 注释）。
3. **内存**：embedding 以 JSON list 存 SQLite，反序列化后每个 float
   约 33 字节 Python 对象开销；100k × 512d ≈ 1.7–2.2 GB RSS。
   矩阵本身只占 195 MB，**内存杀手是存储格式，不是 matmul**。

## 线上实际水位

本地库实测：`document_chunks` 309 条，其中带 embedding 的 **0 条**
（embedding 默认关闭，`embedding_model` 为空 → 当前 hybrid 实为纯 BM25）。

- 离 5000 scan 上限：约 **16 倍**余量。
- 即使现在全量开 embedding，309 chunks 的 loop 延迟 < 10 ms，可忽略。

## 决策：维持现状，不迁移

撑得住。Qdrant / pgvector 这类外部向量库当前是过度工程：
瓶颈不在向量计算（matrix 路径 100k 都够快），而在 5000 的 scan 上限
和 JSON 存储格式——这两个都是改配置/改存储格式能解决的问题，
不需要引入外部服务。

## 监控点（触发迁移/优化的信号）

| 信号 | 来源 | 阈值 | 动作 |
|------|------|------|------|
| chunk 总数接近 scan 上限 | `chunk_store.counts()` / 日志 | ≥ 4500（0.9 × 5000，ANN 门 near-cap 线） | 评估调高 `kb_search_scan_limit`，或开 ANN 门 |
| hybrid p95 持续超标 | `hybrid_latency_stats()` 环形统计 | p95 ≥ 800 ms（`kb_hybrid_ann_p95_ms`）持续 | ANN 门会自动开；若仍高，考虑存储格式优化 |
| scan 截断 warning 频发 | 日志 `hybrid_search scan at cap` | 每日出现 | 同第一条 |
| embedding 开启后内存 | 进程 RSS | > 2 GB | embedding 改 blob/parquet 列存，而非 JSON |
| 1k 回归测试变红 | `test_vector_ceiling.py` | CI 失败 | 按本文复测，定位退化 |

## 将来真要迁时的方案要点（不含实施）

1. 首选仍是进程内：把 ANN 门默认打开 + 候选池调大（`kb_hybrid_ann_candidate_pool`），
   BM25 预过滤后只对 top-N 做 matrix cosine——100k 规模 < 15 ms，无需外部服务。
2. 若语料破百万或要多进程共享：Qdrant（`qdrant-client` 本地模式先行，
   无需独立服务）或 pgvector（已有 Postgres 时）。
3. 无论哪条路，先把 embedding 存储从 JSON list 换成二进制 blob：
   内存降 ~8 倍，这是性价比最高的单项优化。
