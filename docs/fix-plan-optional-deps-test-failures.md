# 可选依赖安装后 6 个测试失败 — 根因分析与修复方案

日期：2026-09-28
状态：待 Cheng 审阅 → 批准后执行

## 背景

安装 14 个 extras 后，后端全量回归出现 6 个确定性新失败（基线 13 个网络失败不变）。
其余零星失败均为 flaky（单独跑通过，不在本次修复范围）。

---

## 失败 1：`test_parse_plain_text_formats`（真质量回归）

**现象**：中文 `.txt`（"环氧树脂…"）被解析成 mojibake（`çŽ¯æ°§…`）。

**根因**：
- `app/services/parsing.py` 的 `_DOC_TIERS` 顺序为 markitdown → docx → xlsx → text。
- markitdown 安装前，该 tier 因 ImportError 直接返回 None，纯文本走 `_parse_plain`（utf-8 → gbk → latin-1 逐个试解码），中文正常。
- markitdown 安装后，它对 `.txt` 先截胡，内部把 UTF-8 按错误编码解码，输出可打印的乱码，绕过了 `_looks_like_undecoded_binary` 检查（只拦不可打印字符），"成功"返回垃圾。

**修复**（产品代码，1 处）：
- 把 `_DOC_TIERS` 中的 `text` tier 提到 markitdown 之前。
- 纯文本格式（`_ALWAYS_PARSEABLE` = txt/md/csv/html/htm/json/xml）本来就不需要 markitdown 做格式转换；docx/xlsx 的 text tier 返回 None，会继续落到 markitdown，行为不变。
- 效果：恢复 markitdown 安装前的解析行为，中文 .txt 正常。

```python
_DOC_TIERS = (
    ("text", lambda c, e: _parse_plain(c) if e in _ALWAYS_PARSEABLE else None),
    ("markitdown", lambda c, e: _parse_markitdown(c, e)),
    ("docx", ...),
    ("xlsx", ...),
)
```

**风险**：极低。只是把纯文本的解析器优先级恢复到安装前的等效顺序；二进制格式路径不变。

---

## 失败 2：`test_lookup_chemical_tier4_pubchem`（测试隔离）

**现象**：期望 `source == "pubchem"`，实际为 `"pubchempy_compound"`。

**根因**：
- `chemical_lookup.lookup_chemical` 的 resolver 顺序：catalog → pubchem(httpx) → **pubchempy_compound** → pubchempy → pubchem(chemtools) → surechembl。
- 测试 mock 了 catalog、pubchem(httpx)、offline_compounds，但没 mock `pubchempy_compound`（`_lookup_compound_synonyms`）。
- pubchempy 安装前该 tier 自动跳过；安装后它对 "aspirin" 命中，抢在测试想测的 tier4（`_lookup_chemtools`）之前返回。
- 产品行为符合设计（tier 顺序是定的），纯测试隔离问题。

**修复**（仅测试，1 行）：
- 在 `tests/test_chemtools.py::test_lookup_chemical_tier4_pubchem` 中加：
  `monkeypatch.setattr(chemical_lookup, "_lookup_compound_synonyms", lambda q: None)`

**风险**：无。只改测试，不碰产品代码。

---

## 失败 3–6：`test_tech_report.py` 4 个（测试隔离）

**现象**：`test_docx_falls_back_when_no_pandoc_no_docx` 等 4 个测试期望抛 RuntimeError，实际没抛。

**根因**：
- 测试 mock `shutil.which` 返回 None 模拟"无 pandoc"，期望回退到 python-docx 时因"未安装"抛 RuntimeError（测试注释原话："本环境未装"）。
- 现在 python-docx/fpdf2 已安装，`markdown_to_docx`/`markdown_to_pdf` 的 native 回退真实可用，导出成功——产品行为正确，是测试假设过期。

**修复**（仅测试）：
- 给这 4 个测试加一个 fixture，用 `sys.modules["docx"] = None` / `sys.modules["fpdf"] = None` 让 `import docx` / `from fpdf import FPDF` 抛 ImportError，模拟"未安装"环境。
- 4 个测试的 mock 意图不变（无 pandoc + 无 native 后端 → RuntimeError）。

**风险**：无。只改测试，不碰产品代码。

---

## 执行计划

1. 改 `app/services/parsing.py`：`_DOC_TIERS` 重排序（text 提前）。
2. 改 `tests/test_chemtools.py`：加 1 行 mock。
3. 改 `tests/test_tech_report.py`：加 fixture 屏蔽 docx/fpdf import。
4. 跑 6 个测试 + 相关解析/lookup/report 全量测试，确认全绿。
5. 不 commit、不 push（另行授权）。

## 不做的

- 不动 tier 的业务顺序（pubchempy 优先是设计）。
- 不修 markitdown 库本身的编码 bug（库外问题，产品层绕过更稳）。
- flaky 测试（RuntimeSecrets 单例污染）不在本次范围。
