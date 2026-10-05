"""Unit tests for table_contract (W2-3 / P1-18).

三类化学表分类、caption 匹配、HTML/pipe 归一化、低置信降级、
解析器异常 fail-open、sidecar 持久化、parsing 接线。
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app.services import parsing, table_contract as tc


# ── classification ──────────────────────────────────────────────────────────

def test_classify_recipe():
    kind, conf = tc.classify_table(["组分", "配比 (phr)", "备注"])
    assert kind == "recipe"
    assert conf >= 0.5


def test_classify_performance():
    kind, conf = tc.classify_table(["测试项目", "测试结果", "判定标准"])
    assert kind == "performance"
    assert conf >= 0.5


def test_classify_tds_sds_caption_wins():
    kind, conf = tc.classify_table(["项目", "典型值"], caption="表3 TDS 技术数据")
    assert kind == "tds_sds"
    assert conf >= 0.5


def test_classify_low_confidence_degrades_to_other():
    # 单个弱命中 → 置信度 < 0.5 → other，不瞎猜
    kind, conf = tc.classify_table(["标准"], caption="")
    assert kind == "other"
    assert conf < 0.5


def test_classify_no_keywords():
    kind, conf = tc.classify_table(["A", "B"])
    assert kind == "other"
    assert conf == 0.0


def test_classify_latin_word_boundary():
    # "phr" 按词匹配：spherical 内的 phr 子串不应命中；双命中才定类
    kind, _ = tc.classify_table(["spherical particles"])
    assert kind == "other"
    kind, conf = tc.classify_table(["投料量 (phr)", "树脂组分"])
    assert kind == "recipe"
    assert conf >= 0.5


# ── markdown → blocks ───────────────────────────────────────────────────────

RECIPE_MD = """表 1 配方组成

| 组分 | 配比 |
|---|---|
| 环氧树脂 | 100 |
| 固化剂 | 20 |
"""


def test_blocks_from_markdown_pipe_table():
    blocks = tc.blocks_from_markdown(RECIPE_MD)
    tables = [b for b in blocks if b.type == "table"]
    assert len(tables) == 1
    assert tables[0].caption == "表 1 配方组成"
    assert tables[0].page_idx == 0


def test_caption_english_table():
    md = "Table 2 Curing performance\n\n| Property | Result |\n|---|---|\n| Hardness | 2H |\n"
    blocks = tc.blocks_from_markdown(md)
    assert blocks[0].caption == "Table 2 Curing performance"


def test_caption_after_table():
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n表 5 测试汇总\n"
    blocks = tc.blocks_from_markdown(md)
    assert blocks[0].caption == "表 5 测试汇总"


def test_no_table_lines_without_separator():
    # 单行 |x| 不是表格（无分隔行）→ 不建 table 块
    blocks = tc.blocks_from_markdown("a | b\nc | d\n")
    assert [b for b in blocks if b.type == "table"] == []


def test_page_marker_tracking():
    md = "<!-- page:2 -->\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    blocks = tc.blocks_from_markdown(md)
    assert blocks[0].page_idx == 1  # 0-based → page_no 2


def test_empty_markdown():
    assert tc.blocks_from_markdown("") == []
    assert tc.blocks_from_markdown("   \n\n") == []


# ── extraction ──────────────────────────────────────────────────────────────

def test_extract_pipe_table_end_to_end():
    assets = tc.extract_tables("sid", tc.blocks_from_markdown(RECIPE_MD), parser="docling")
    assert len(assets) == 1
    a = assets[0]
    assert a.table_id == "sid#p01-00"
    assert a.kind == "recipe"
    assert a.headers == ["组分", "配比"]
    assert a.rows == [["环氧树脂", "100"], ["固化剂", "20"]]
    assert a.caption == "表 1 配方组成"
    assert a.provenance["parser"] == "docling"
    assert a.page_no == 1


def test_extract_html_table():
    html = (
        "<table><tr><th>性能</th><th>结果</th></tr>"
        "<tr><td>硬度</td><td>2H</td></tr></table>"
    )
    block = tc.MdBlock(type="table", page_idx=0, html=html, caption="表 2 性能对比")
    assets = tc.extract_tables("sid", [block], parser="mineru")
    assert len(assets) == 1
    a = assets[0]
    assert a.headers == ["性能", "结果"]
    assert a.rows == [["硬度", "2H"]]
    assert a.kind == "performance"


def test_extract_native_mineru_block_duck_typed():
    class FakeMinerUBlock:
        type = "table"
        page_idx = 2
        text = ""
        html = ("<table><tr><th>成分</th><th>配比</th></tr>"
                "<tr><td>环氧树脂</td><td>100</td></tr></table>")
        caption = "表 1 配方组成"

    assets = tc.extract_tables("sid", [FakeMinerUBlock()], parser="mineru")
    assert len(assets) == 1
    a = assets[0]
    assert a.page_no == 3  # page_idx(0-based) + 1
    assert a.caption == "表 1 配方组成"
    assert a.kind == "recipe"
    assert a.table_id == "sid#p03-00"


def test_extract_skips_non_table_blocks():
    blocks = [tc.MdBlock(type="text", text="hello"),
              tc.MdBlock(type="equation", text="x^2")]
    assert tc.extract_tables("sid", blocks) == []


def test_extract_fail_open_on_garbage_blocks():
    class BadBlock:
        type = "table"
        page_idx = "bogus"  # int() raises → block skipped, not fatal

    assets = tc.extract_tables("sid", [object(), {"type": "table"}, None, BadBlock()])
    assert assets == []


def test_to_from_dict_roundtrip():
    a = tc.TableAsset(table_id="s#p01-00", source_id="s", page_no=1,
                      caption="表1", kind="recipe",
                      headers=["组分"], rows=[["树脂"]], raw_markdown="|x|")
    b = tc.TableAsset.from_dict(a.to_dict())
    assert b == a


# ── sidecar persistence ─────────────────────────────────────────────────────

def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    assets = tc.extract_tables("doc-1", tc.blocks_from_markdown(RECIPE_MD))
    path = tc.save_tables("doc-1", assets)
    assert path is not None and path.exists()
    loaded = tc.load_tables("doc-1")
    assert len(loaded) == 1
    assert loaded[0].table_id == assets[0].table_id
    assert loaded[0].headers == ["组分", "配比"]


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    assert tc.load_tables("nope") == []


def test_save_fail_open_on_unwritable_dir(tmp_path, monkeypatch):
    # ``/proc/...`` is only unwritable on Linux; a path under a *file* is unwritable everywhere.
    from app.services._fsutil import unwritable_path

    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(unwritable_path(tmp_path)))
    assets = tc.extract_tables("s", tc.blocks_from_markdown(RECIPE_MD))
    assert tc.save_tables("s", assets) is None  # 不抛错


# ── parsing.py wiring ───────────────────────────────────────────────────────

def test_wiring_attaches_tables_in_memory_only(tmp_path, monkeypatch):
    # F-3: _maybe_extract_tables 只做内存抽取，不再写字节-hash sidecar
    #（那种写入永远读不到）。落盘由 persist_table_sidecar 以 source UUID 为键。
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    result = parsing.ParseResult(RECIPE_MD, "docling")
    assert result.tables == []
    out = parsing._maybe_extract_tables(result, b"fake-bytes")
    assert len(out.tables) == 1
    assert out.tables[0].kind == "recipe"
    # 旧键（字节 sha256）下无文件
    key = hashlib.sha256(b"fake-bytes").hexdigest()
    assert tc.load_tables(key) == []


def test_persist_table_sidecar_keyed_by_source_uuid(tmp_path, monkeypatch):
    # F-3 回归：sidecar 以 source UUID 为键写入，load_tables(doc.id) 可读。
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    result = parsing.ParseResult(RECIPE_MD, "docling")
    out = parsing._maybe_extract_tables(result, b"fake-bytes")
    parsing.persist_table_sidecar("source-uuid-1", out.tables)
    loaded = tc.load_tables("source-uuid-1")
    assert len(loaded) == 1
    assert loaded[0].source_id == "source-uuid-1"
    assert loaded[0].table_id.startswith("source-uuid-1#")
    assert loaded[0].headers == ["组分", "配比"]


def test_persist_table_sidecar_fail_open(tmp_path, monkeypatch):
    # 空 source_id / 空表不写、不抛错。
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    parsing.persist_table_sidecar("", [])
    parsing.persist_table_sidecar("source-uuid-1", [])
    assert tc.load_tables("source-uuid-1") == []


def test_wiring_disabled_by_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    monkeypatch.setattr(parsing, "get_settings",
                        lambda: SimpleNamespace(table_extract_enabled=False))
    result = parsing.ParseResult(RECIPE_MD, "docling")
    out = parsing._maybe_extract_tables(result, b"fake-bytes")
    assert out.tables == []


def test_wiring_fail_open_on_extraction_error(monkeypatch):
    monkeypatch.setattr(tc, "blocks_from_markdown",
                        lambda md: (_ for _ in ()).throw(RuntimeError("boom")))
    result = parsing.ParseResult(RECIPE_MD, "docling")
    out = parsing._maybe_extract_tables(result, b"fake-bytes")
    assert out.tables == []  # 不抛错，原结果完好
    assert out.markdown == RECIPE_MD
