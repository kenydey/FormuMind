# MinerU 升级页批量提交 — hybrid 解析提速实施计划

> **目标**：把 hybrid 管道里「逐页串行 MinerU 往返」改成「一次批量提交」，将多图表 PDF 的解析时间从 27–45 分钟降到 5–8 分钟（预期 5–10×）。
> **架构**：非扫描升级页全部切片成单页 PDF 后，用 SDK 已内置的 `extract_batch` 一次提交，MinerU 服务端并行处理、单轮轮询；扫描页仍走本地 OCR 优先的既有路径。
> **技术栈**：Python 3.11 · mineru-open-sdk 0.2.5 · PyMuPDF/pymupdf4llm · pytest

---

## 一、背景与量化依据

上一批 `anticorrosion_coating` 深度研究入库（112 篇，99.3 分钟）的实测：

| 指标 | 值 |
|------|----|
| 解析占总耗时 | **93.8%**（下载 3.4% / 向量化 4.8%）|
| 文献 PDF 平均 | 217s，最慢 `arxiv 2404.05601v2` **2723s（45min）** |
| 单页 MinerU 服务端解析 | **~3 秒**（提交后两次轮询即 done）|
| 单页串行往返开销 | **2–3 分钟**（POST→PUT→poll→zip）|

根因：`hybrid_parse.parse()` 第 394 行 `for page in selected:` 逐页串行调用 `_escalate_page`，每页一次独立 MinerU 往返。10 个升级页 = 10 × ~2.7min ≈ 27–45min。**慢的是网络/上传往返，不是解析计算。**

## 二、架构对比

### 现状（串行逐页）

```
parse()
 └─ for page in selected:                    # 逐页串行
      ├─ page.looks_scanned → 本地 RapidOCR（快）→ 低置信才回退 MinerU 单页
      └─ else               → _escalate_page(单页)
                                └─ mineru_cloud.parse_bytes(单页)
                                     └─ POST→PUT→poll→zip   (~2-3min/页 × N)
```

### 目标（批量 + 扫描页保留）

```
parse()
 ├─ scanned_sel（扫描页，罕见）→ 逐页本地 OCR → 低置信才回退 MinerU 单页   # 保留
 └─ cloud_sel（表格/图页）     → 切片全部为单页 PDF
                                   └─ mineru_cloud.parse_pages_batch([p1..pn])  # 一次 POST
                                        └─ SDK extract_batch → 服务端并行 → 单轮 poll
                                             └─ 按页 _render_blocks（视觉路由不变）
```

## 三、核心设计决策

1. **只批量非扫描页**（表格/图页），扫描页保留原逐页逻辑——本次慢的是表格/图页，扫描页已有本地 OCR 快速路径，改动最小、风险最低。
2. **SDK 已内置 `extract_batch`**（`mineru/client.py:256`）：一次 `POST /file-urls/batch` 提交 N 个文件，`_yield_batch` 按提交顺序产出结果；`state=="done"` 才有内容，`state=="failed"` 为失败页。**零新增依赖。**
3. **按页降级语义不变**：批量结果按索引映射回页码，`None`/失败页保留本地文本，与今日 `_escalate_page` 返回 `None` 的行为完全一致。
4. **熔断语义迁移**：`mineru_max_page_failures` 熔断器只在扫描页的逐页循环中保留（那里仍可能 N 次失败）；批量路径一次调用失败即整体降级本地文本——断网时只付 1 次批量超时，不再付 N 次，本就优于旧行为。
5. **统一 `ocr` 标志**：批量页全是非扫描页，`ocr` 统一为 False，无需 `file_params` 逐页覆盖。
6. **缓存（方案 C）暂不纳入本次**：`prune_mineru_cache=True` 维持现状（VPS 磁盘约束），仅批量路径沿用 `_cache_load/_store` 钩子（默认跳过），未来要开只需改一个开关。

## 四、文件变更清单

| 文件 | 变更 |
|------|------|
| `backend/app/services/mineru_cloud.py` | 新增 `parse_pages_batch(contents, ext, timeout) -> list[MinerUDocument | None]` |
| `backend/app/services/hybrid_parse.py` | 新增 `_escalate_pages_batch`；`parse()` 拆分 `scanned_sel`/`cloud_sel`，后者走批量 |
| `backend/app/config.py` | 新增 `mineru_batch_timeout_s: float = 1800.0` |
| `backend/tests/test_mineru_cloud.py` | fake SDK 增加 `extract_batch`；新增批量单测 |
| `backend/tests/test_hybrid_parse.py` | `cloud_on` fixture 改为 mock `parse_pages_batch`；更新/新增用例 |

**不新增依赖**；`mineru_max_pages_per_doc`、`mineru_page_timeout_s`、视觉路由、chunk 管道全部不动。

---

## 五、实施任务（TDD，逐任务提交）

### Task 1：`mineru_cloud.parse_pages_batch` 实现

**文件**：`backend/app/services/mineru_cloud.py`

在 `parse_bytes` 之后新增。要点：

```python
def parse_pages_batch(
    contents: list[bytes], *, ext: str = "pdf", timeout: float | None = None,
) -> list[MinerUDocument | None]:
    """N 个单页 PDF 一次批量提交；返回与输入等长、按序的结果，失败页为 None。"""
    available, hint = mineru_available()
    if not available:
        return [None] * len(contents)
    ext = (ext or "").lower().lstrip(".")
    if ext not in SUPPORTED_EXTS:
        return [None] * len(contents)

    settings = get_settings()
    limit = int(settings.mineru_max_upload_mb) * 1024 * 1024
    results: list[MinerUDocument | None] = [None] * len(contents)
    accepted: list[int] = []
    paths: list[str] = []

    for i, content in enumerate(contents):
        if len(content) > limit:
            logger.warning("mineru: page %d exceeds upload limit — skipping", i)
            continue
        if not settings.prune_mineru_cache:
            cached = _cache_load(_cache_key(content, ext=ext, ocr=False))
            if cached is not None:
                results[i] = cached
                continue
        handle, path = tempfile.mkstemp(suffix=f".{ext}")
        with os.fdopen(handle, "wb") as fh:
            fh.write(content)
        paths.append(path)
        accepted.append(i)

    if not paths:
        return results

    try:
        import mineru
        token = str(effective_setting(settings, "mineru_api_key") or "")
        wait = float(settings.mineru_timeout_s if timeout is None else timeout)
        client = mineru.MinerU(token=token, base_url=settings.mineru_base_url)
        docs = list(client.extract_batch(paths, timeout=int(wait)))
    except mineru.AuthError as exc:
        logger.error("mineru: token rejected (%s)", exc); return results
    except mineru.QuotaExceededError as exc:
        logger.error("mineru: daily quota exhausted (%s)", exc); return results
    except mineru.TimeoutError as exc:
        logger.warning("mineru: batch timed out after %ss (%s)", wait, exc); return results
    except mineru.MinerUError as exc:
        return degrade_return(logger, exc, "mineru batch failed", results)
    except Exception as exc:
        if _is_auth_failure(exc):
            logger.error("mineru: token rejected (HTTP 401/403)"); return results
        return degrade_return(logger, exc, "mineru batch call failed", results)
    finally:
        for p in paths:
            try: os.unlink(p)
            except OSError: pass

    for idx, doc in zip(accepted, docs):
        if doc is None or getattr(doc, "state", "done") != "done":
            continue
        images = {}
        for image in getattr(doc, "images", None) or []:
            images[getattr(image, "path", "")] = image.data
            images[getattr(image, "name", "")] = image.data
        nd = _normalise(doc, images)
        results[idx] = nd
        if not settings.prune_mineru_cache:
            _cache_store(_cache_key(contents[idx], ext=ext, ocr=False), nd)
    return results
```

> 异常处理与 `_extract` 对齐（AuthError/QuotaExceededError/TimeoutError/MinerUError/401-403），全部降级为 `None` 而非抛出。

### Task 2：`parse_pages_batch` 单测

**文件**：`backend/tests/test_mineru_cloud.py`

- fake SDK `_Client` 增加 `extract_batch(self, sources, **kw)`：记录 `module.batch_calls`，返回 `_FakeResult` 的迭代器（可指定 per-file state）。
- 用例：
  1. `test_parse_pages_batch_submits_one_call` — 3 页 → `len(sdk.batch_calls) == 1` 且传入 3 个路径。
  2. `test_parse_pages_batch_maps_failures_per_page` — 结果 `[done, failed, done]` → 返回 `[doc, None, doc]`。
  3. `test_parse_pages_batch_deletes_temp_files` — 调用后无临时文件残留。
  4. `test_parse_pages_batch_degrade_when_sdk_raises` — `MinerUError` → 全 `None`。
  5. `test_parse_pages_batch_serves_cache_hits` — `prune=False` 时二次调用命中缓存不触网。

**验证**：`pytest tests/test_mineru_cloud.py -v`（新增用例绿，既有用例不回归）。

### Task 3：`hybrid_parse` 批量改造

**文件**：`backend/app/services/hybrid_parse.py`

新增 helper（放在 `_escalate_page` 之后）：

```python
def _escalate_pages_batch(
    content: bytes, pages: list[pdf_local.LocalPage]
) -> dict[int, str]:
    """非扫描升级页一次性批量提交；失败页回退本地文本。"""
    page_pdfs: list[bytes] = []
    order: list[int] = []
    for page in pages:
        p = pdf_local.page_as_pdf(content, page.page_no)
        if p:
            page_pdfs.append(p)
            order.append(page.page_no)
    if not page_pdfs:
        return {}
    documents = mineru_cloud.parse_pages_batch(page_pdfs)
    upgraded: dict[int, str] = {}
    for page_no, doc in zip(order, documents):
        if doc is None or not doc.blocks:
            continue
        rendered = _render_blocks(doc.blocks, page_label=f"p.{page_no}")
        if rendered:
            upgraded[page_no] = rendered
    return upgraded
```

改造 `parse()` 的升级段（`if selected:` 之后）：

```python
    scanned_sel = [p for p in selected if p.looks_scanned]
    cloud_sel = [p for p in selected if not p.looks_scanned]
    upgraded: dict[int, str] = {}

    # 扫描页：保留逐页本地 OCR → 低置信回退 MinerU（含熔断器）
    # （原 for 循环体只作用于 scanned_sel，逻辑不变）

    # 非扫描页：一次批量提交
    if cloud_sel:
        upgraded.update(_escalate_pages_batch(content, cloud_sel))
```

保留 `mineru_max_pages_per_doc` 上限、`prewarm()`、`mineru_pages` 计时注解、`assemble` 尾部逻辑不变。日志行仍输出 `hybrid: N/M pages escalated (S succeeded)`，`N=attempted` 语义改为「已尝试升级页数」。

### Task 4：`hybrid_parse` 单测更新

**文件**：`backend/tests/test_hybrid_parse.py`

- `cloud_on` fixture：`parse_bytes` → 改为 mock `parse_pages_batch`，返回按输入页数生成的 `MinerUDocument` 列表，并把收到的页数记入 `sent`。
- 更新受影响的既有用例（`test_only_the_qualifying_pages_reach_the_cloud`、`test_escalation_is_capped_per_document`、`test_a_failed_escalation_keeps_the_local_page`、`test_escalated_pages_keep_their_page_numbers`、`test_headings_reach_heading_path_after_escalation`）。
- 新增用例：
  1. `test_multiple_figure_pages_are_one_batch_call` — 3 个图页 → `parse_pages_batch` 被调 1 次、传入 3 页（断言「一次调用」）。
  2. `test_batch_failure_keeps_local_text` — 批量返回全 `None` → 本地文本仍在、页标记齐全。
  3. `test_scanned_pages_stay_out_of_the_batch` — 扫描页走本地 OCR，不进入 `parse_pages_batch`。
  4. `test_batch_results_map_back_to_correct_pages` — 结果乱序/部分失败时页码映射正确。

**验证**：`pytest tests/test_hybrid_parse.py tests/test_mineru_cloud.py -v`。

### Task 5：配置 + 全量回归 + 真实小批量验证

- `config.py` 加 `mineru_batch_timeout_s: float = 1800.0`（`.env.example` 同步注释说明，可选）。
- 全量：停 dev 服务后 `pytest tests/ -q`（按项目惯例需先 `stop_all.sh`，避免 sqlite lock）。
- 真实小批量：起服务后，对一篇含 3+ 图表的 arXiv PDF 跑一次 `kb_ingest`，观察日志 `hybrid: N/M pages escalated` + `parse=…(hybrid+mineru:N)` 耗时，确认从串行 2-3min/页降到批量后显著缩短、且 `mineru:` 页数正确。

### Task 6：提交推送

```bash
git add backend/app/services/mineru_cloud.py backend/app/services/hybrid_parse.py \
        backend/app/config.py backend/tests/test_mineru_cloud.py backend/tests/test_hybrid_parse.py
git commit -m "perf(parse): hybrid 升级页改批量提交 MinerU（extract_batch），多图表 PDF 5-10× 提速"
git push origin main   # SSH: git@github.com:kenydey/FormuMind.git
```

---

## 六、风险矩阵

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| R1 | SDK `extract_batch` 结果顺序/部分失败状态与文档不符 | 中 | 页错位/丢页 | 单测覆盖按索引映射 + 真实小批量核对页号 |
| R2 | MinerU 批量配额口径变化（按批 vs 按页计费） | 中 | 配额意外耗尽 | 保留 `max_pages_per_doc` 上限；小批量灰度观察 quota |
| R3 | 批量超时（1800s）比单页更敏感，慢网下整批失败 | 低 | 该文档升级页全降级本地 | 失败页保留本地文本（不丢内容）；`mineru_batch_timeout_s` 可调 |
| R4 | 一次上传 N 页，单点失败影响整批 | 低 | 需整批重试 | 按页映射 `None`，本地文本兜底；后续可叠加缓存 |
| R5 | 既有测试（`cloud_on` fixture）大量失效 | 高（必然）| 测试红灯 | Task 4 同步更新 fixture 与断言 |
| R6 | 扫描页逐页循环改动引入回归 | 低 | 扫描件解析异常 | 扫描页路径不改逻辑，仅分组；`test_scanned_pages_stay_out_of_the_batch` 兜底 |

## 七、回滚

- 代码级：`git revert` 该 commit 即回到串行逐页。
- 运行时：无新增 feature flag（改动被 `mineru_available()` 与 `mineru_max_pages_per_doc` 既有开关约束）。如需「一键回退到串行」的运维开关，可在 Task 5 加 `mineru_batch_enabled: bool = True` 注册进 `env_flags.py`（低风险，建议按需）。

## 八、时间表（预估 2.5–3h）

| 任务 | 内容 | 估时 |
|------|------|------|
| 1–2 | parse_pages_batch + 单测 | 50min |
| 3–4 | hybrid_parse 改造 + 单测更新 | 70min |
| 5 | 配置 + 全量回归 + 真实小批量验证 | 30min |
| 6 | commit + push | 10min |

---

**待确认点**（评审时请定）：
1. 是否接受「批量失败→整批降级本地文本」的语义（当前已是最优：断网只付 1 次超时）？
2. 是否要 `mineru_batch_enabled` 运维开关（一键回退串行）？建议加。
3. 方案 C（MinerU 缓存）本次不做、留作后续，是否同意？
