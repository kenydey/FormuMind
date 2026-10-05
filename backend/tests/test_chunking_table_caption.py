"""A table's caption belongs to the table's chunk (round-4).

A table is always emitted as its own atomic chunk, so the paragraph above it — ``表1 典型性能`` /
``Table 2. Salt spray results`` — was cut off into a separate chunk. Short captions then fell under the
length floor of the quality gate and vanished; longer ones survived as a chunk with no data in it, while
the table beside it (numbers, no subject) could only be found by its cell values.
"""
from __future__ import annotations

import pytest

from app.services import chunking

TABLE = "| 项目 | 指标 | 单位 |\n| --- | --- | --- |\n| 固体含量 | 65 | % |\n| 粘度 | 1200 | mPa·s |"


def _chunks(md: str):
    return chunking.chunk_markdown(md)


def test_a_caption_stays_with_its_table():
    chunks = _chunks(f"# 技术数据表\n\n表1 典型性能\n\n{TABLE}\n")
    assert len(chunks) == 1
    (chunk,) = chunks
    assert chunk.text.startswith("表1 典型性能")
    assert chunk.text.endswith("mPa·s |")
    assert chunk.block_type == "table"
    assert chunk.heading_path == "技术数据表"


def test_only_the_caption_moves_not_the_paragraph_before_it():
    chunks = _chunks(f"# T\n\n本产品为双组分环氧底漆，适用于钢结构。\n\n表1 典型性能\n\n{TABLE}\n")
    assert [c.block_type for c in chunks] == ["text", "table"]
    assert chunks[0].text == "本产品为双组分环氧底漆，适用于钢结构。"
    assert chunks[1].text.startswith("表1 典型性能")


@pytest.mark.parametrize("caption", ["Table 2. Salt spray results of coatings A–C", "**Table 2.** Salt spray results", "表 3 施工参数"])
def test_other_caption_styles(caption):
    chunks = _chunks(f"# T\n\n{caption}\n\n{TABLE}\n")
    assert len(chunks) == 1
    assert chunks[0].text.startswith(caption)


def test_a_caption_a_parser_promoted_to_a_heading_travels_in_the_heading_path():
    """pymupdf4llm often renders the caption as ``## 表2 …``: it is then a section heading, already carried by
    ``heading_path`` (which retrieval scores and citations show) — nothing to merge."""
    (chunk,) = _chunks(f"# T\n\n## 表2 盐雾试验结果\n\n{TABLE}\n")
    assert chunk.heading_path.endswith("表2 盐雾试验结果")
    assert chunk.text == TABLE


def test_a_sentence_about_a_table_is_not_its_caption():
    chunks = _chunks(f"# T\n\n如表1所示，粘度符合技术要求。\n\n{TABLE}\n")
    assert [c.block_type for c in chunks] == ["text", "table"]
    assert chunks[1].text == TABLE


def test_a_long_paragraph_that_starts_like_a_caption_is_not_one():
    long_para = "表1 " + "说明文字" * 100
    chunks = _chunks(f"# T\n\n{long_para}\n\n{TABLE}\n")
    assert chunks[-1].text == TABLE


def test_a_table_without_a_caption_is_unchanged():
    chunks = _chunks(f"# T\n\n{TABLE}\n")
    assert len(chunks) == 1 and chunks[0].text == TABLE


def test_two_tables_each_take_their_own_caption():
    md = f"# T\n\n表1 典型性能\n\n{TABLE}\n\n表2 施工参数\n\n{TABLE}\n"
    chunks = _chunks(md)
    assert [c.text.splitlines()[0] for c in chunks] == ["表1 典型性能", "表2 施工参数"]


def test_a_code_fence_does_not_take_a_caption():
    chunks = _chunks("# T\n\n表1 说明\n\n```\nx = 1\n```\n")
    assert [c.block_type for c in chunks] == ["text", "code"]


def test_offsets_still_describe_each_chunk():
    chunks = _chunks(f"# T\n\n前言段落内容。\n\n表1 典型性能\n\n{TABLE}\n")
    for c in chunks:
        assert c.offset_end - c.offset_start == len(c.text)
    assert chunks[1].offset_start == chunks[0].offset_end


def test_the_caption_is_searchable_through_the_table_chunk():
    from app.services.kb_retrieval_gate import is_garbage_chunk_text

    (chunk,) = _chunks(f"# T\n\n表1 典型性能\n\n{TABLE}\n")
    assert "典型性能" in chunk.text
    assert not is_garbage_chunk_text(chunk.text)
