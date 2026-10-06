"""``parsing._parse_xlsx_tables`` - the spreadsheet tier in front of MarkItDown (round-5; found by the parsing evaluation).

MarkItDown reads a sheet through pandas, which takes the first row for the header. A formulation sheet starts with a merged
title and a blank row, so its header became ``Unnamed: 1``, a row of ``NaN`` followed, the real header was demoted to a data
row and every empty cell read ``NaN`` - in the indexed text and in the table the contract turns into properties.
"""
from __future__ import annotations

import datetime
import io

import pytest
from app.services import parsing, table_contract

openpyxl = pytest.importorskip("openpyxl")


def _book(fill) -> bytes:
    workbook = openpyxl.Workbook()
    fill(workbook)
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def _parse(content: bytes) -> str:
    text = parsing._parse_xlsx_tables(content)
    assert text is not None
    return text


def _formulation(workbook) -> None:
    sheet = workbook.active
    sheet.title = "配方"
    sheet.merge_cells("A1:C1")
    sheet["A1"] = "水性环氧底漆配方表（单位：wt%）"
    sheet.append([])
    sheet.append(["组分", "含量", "功能"])
    sheet.append(["环氧乳液", 36.5, "成膜物"])
    sheet.append(["磷酸锌", 8.5, "防锈颜料"])
    notes = workbook.create_sheet("性能")
    notes.append(["项目", "结果", "标准"])
    notes.append(["附着力 (级)", 0, None])
    notes.append(["耐盐雾 (h)", 500, "ASTM B117"])


def test_a_merged_title_is_one_line_above_the_table_and_the_real_header_is_the_header():
    text = _parse(_book(_formulation))
    lines = text.splitlines()
    assert "## 配方" in lines and lines.count("水性环氧底漆配方表（单位：wt%）") == 1
    assert "| 组分 | 含量 | 功能 |" in lines and lines[lines.index("| 组分 | 含量 | 功能 |") + 1] == "| --- | --- | --- |"
    assert lines.index("水性环氧底漆配方表（单位：wt%）") < lines.index("| 组分 | 含量 | 功能 |")
    assert "Unnamed" not in text and "NaN" not in text


def test_an_empty_cell_is_empty_not_nan():
    text = _parse(_book(_formulation))
    assert "| 附着力 (级) | 0 |  |" in text.splitlines()


def test_the_table_contract_now_sees_the_real_header():
    markdown = _parse(_book(_formulation))
    assets = table_contract.extract_tables("", table_contract.blocks_from_markdown(markdown), parser="openpyxl")
    assert [a.headers for a in assets] == [["组分", "含量", "功能"], ["项目", "结果", "标准"]]
    assert assets[0].rows[0] == ["环氧乳液", "36.5", "成膜物"]


def test_it_is_the_tier_parse_document_uses_for_a_workbook():
    result = parsing.parse_document(_book(_formulation), "xlsx")
    assert result.parser == "openpyxl" and result.ok


def test_notes_under_a_table_are_lines_not_rows():
    def fill(workbook):
        sheet = workbook.active
        sheet.append(["指标", "数值"])
        sheet.append(["固含", 52.5])
        sheet.append(["注：测试温度 23 °C"])

    lines = _parse(_book(fill)).splitlines()
    assert lines[-1] == "注：测试温度 23 °C" and "| 注：测试温度 23 °C |" not in "\n".join(lines)
    assert lines.index("| 固含 | 52.5 |") < lines.index("注：测试温度 23 °C")


def test_blocks_separated_by_a_blank_row_are_separate_tables():
    def fill(workbook):
        sheet = workbook.active
        sheet.append(["组分", "含量"])
        sheet.append(["树脂", 40.5])
        sheet.append([])
        sheet.append(["性能", "结果"])
        sheet.append(["硬度", "2H"])

    text = _parse(_book(fill))
    assert text.count("| --- | --- |") == 2


def test_a_group_of_rows_merged_down_repeats_its_label_in_each_row():
    def fill(workbook):
        sheet = workbook.active
        sheet.append(["颜料", "名称", "含量"])
        sheet.append(["防锈", "磷酸锌", 8.5])
        sheet.append([None, "三聚磷酸铝", 6.5])
        sheet.merge_cells("A2:A3")

    lines = _parse(_book(fill)).splitlines()
    assert "| 防锈 | 磷酸锌 | 8.5 |" in lines and "| 防锈 | 三聚磷酸铝 | 6.5 |" in lines


def test_columns_nobody_filled_are_dropped():
    def fill(workbook):
        sheet = workbook.active
        sheet.append(["组分", None, None, "含量"])
        sheet.append(["树脂", None, None, 40.5])

    assert "| 组分 | 含量 |" in _parse(_book(fill)).splitlines()


def test_a_single_column_is_text_not_a_table():
    def fill(workbook):
        sheet = workbook.active
        sheet.append(["第一条说明"])
        sheet.append(["第二条说明"])

    text = _parse(_book(fill))
    assert "|" not in text and "第一条说明\n第二条说明" in text


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (35.0, "35"),  # a workbook stores 35.0 as 35: there is no trailing zero to recover
        (0.1 + 0.2, "0.3"),
        (1e-05, "0.00001"),
        (1234567.5, "1234567.5"),
        (True, "TRUE"),
        (datetime.date(2026, 10, 6), "2026-10-06"),
        (datetime.datetime(2026, 10, 6, 14, 30), "2026-10-06 14:30"),
        ("两行\n文字", "两行 文字"),
    ],
)
def test_cells_are_written_the_way_a_reader_sees_them(value, shown):
    def fill(workbook):
        sheet = workbook.active
        sheet.append(["a", "b"])
        sheet.append(["x", value])

    assert f"| x | {shown} |" in _parse(_book(fill)).splitlines()


def test_a_pipe_in_a_cell_does_not_split_it():
    def fill(workbook):
        sheet = workbook.active
        sheet.append(["a", "b"])
        sheet.append(["x|y", "z"])

    assert "| x\\|y | z |" in _parse(_book(fill)).splitlines()


def test_a_workbook_with_nothing_in_it_leaves_the_cascade_to_the_next_tier():
    assert parsing._parse_xlsx_tables(_book(lambda workbook: None)) is None


def test_garbage_is_not_an_error():
    assert parsing._parse_xlsx_tables(b"PK\x03\x04 not a workbook") is None
    assert parsing._parse_xlsx_tables(b"") is None


def test_a_file_too_large_to_hold_cell_by_cell_goes_to_the_streaming_converters(monkeypatch):
    content = _book(_formulation)
    monkeypatch.setattr(parsing, "_XLSX_TABLES_MAX_BYTES", 10)
    assert parsing._parse_xlsx_tables(content) is None
    monkeypatch.undo()
    monkeypatch.setattr(parsing, "_XLSX_TABLES_MAX_SHEET_BYTES", 10)  # a small file declaring a huge sheet
    assert parsing._parse_xlsx_tables(content) is None
    monkeypatch.undo()
    monkeypatch.setattr(parsing, "_XLSX_TABLES_MAX_CELLS", 3)
    assert parsing._parse_xlsx_tables(content) is None


def test_without_openpyxl_the_tier_steps_aside(monkeypatch):
    import sys

    content = _book(_formulation)
    monkeypatch.setitem(sys.modules, "openpyxl", None)  # ``import openpyxl`` raises ImportError
    assert parsing._parse_xlsx_tables(content) is None
