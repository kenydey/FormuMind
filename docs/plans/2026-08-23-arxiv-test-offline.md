# arXiv 429 限流 — 测试去网络化

> 日期：2026-08-23 ｜ 状态：待评审

## 一、根因

全量测试跑 `tests/test_api.py::test_research_endpoint` 时日志出现：

```
arXiv search failed: Page request resulted in HTTP 429
(https://export.arxiv.org/api/query?...)
```

有 **2 个「live search」测试直接打真实 arXiv/专利 API**，arXiv 限流（429）时证据为空，
断言 `body["evidence"]` 非空即失败：

| 测试 | 位置 | 打真网络的行为 |
|------|------|----------------|
| `test_research_endpoint` | `test_api.py:36` | `POST /api/research` → 真实 arXiv/专利搜索 |
| `test_research_source_types_live_search` | `test_pipeline.py:54` | `run_research(source_types=["patents","literature"])` |

对比：`test_search_providers.py` / `test_openalex_pagination.py` 已用
`monkeypatch.setattr("app.services.search_providers.httpx.Client", lambda **kw: FakeClient())`
mock 掉网络层 —— 这两个 live 测试没跟上。

## 二、方案

**mock 检索网络层**，让这两个测试只验 pipeline 结构（search→推荐→evidence 组装），
不依赖真实 arXiv/专利 API。

> 注：实测 arXiv 走的是 `arxiv` 库（非 httpx），真正的联邦检索唯一入口是
> `literature.iter_search`（在 CRAG `fallback_node` 调用）。故 mock 目标是
> `app.services.literature.iter_search`，而非方案初稿写的 `httpx.Client`。

- `test_research_endpoint` / `test_research_source_types_live_search`（及同类的
  `test_research_returns_evidence_and_recommendations`）：mock `iter_search` 返回假 Evidence

可选：保留 1 个 opt-in 真实集成测试（`@pytest.mark.skipif` 无网络时跳过），供手动验证
真实 API 连通性，但默认不参与全量套件。

## 三、文件变更清单

| 文件 | 改动 |
|------|------|
| `tests/test_api.py` | `test_research_endpoint` 加 httpx mock fixture |
| `tests/test_pipeline.py` | `test_research_source_types_live_search` 加 httpx mock |

## 四、实施步骤

1. 抽一个 `fake_search_client` fixture（或复用 test_search_providers 的 FakeClient）
2. 两个 live 测试接入 mock，断言 pipeline 结构（evidence 组装、recommended 数量）
3. 跑全量回归，确认无 429、全绿
4. commit + push（SSH）

## 五、风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 失去真实 API 连通性验证 | 中 | 集成回归盲区 | 保留 opt-in live 测试（skipif 无网络）|
| mock 覆盖不全导致假阳性 | 低 | 测试失真 | 对齐现有 FakeClient 模式，mock 到 httpx 层 |

## 六、验收标准

- 全量套件运行无 `arXiv ... HTTP 429` 日志
- 两个测试稳定通过（连续 3 次）
- 全量套件全绿
