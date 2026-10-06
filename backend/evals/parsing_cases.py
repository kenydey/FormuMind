"""The parsing evaluation's documents: built in code from a specification, so each file and its ground truth cannot drift apart.

Every case is a small real file (DOCX, XLSX, PDF, HTML) together with what a faithful parse must keep:

* ``cells`` - every table cell, which must reach the output at all;
* ``rows`` - the cells of each table row, which must still sit on one line (a table flattened to one cell per line has all
  its text and none of its meaning);
* ``order`` - fragments in reading order (a two-column page read straight across the page interleaves the columns);
* ``numbers`` - figures that must survive digit for digit (``35.0`` is not ``35``, and not ``3 5.0``);
* ``units`` - ``50 µm``, ``120 °C``: value and unit stay together;
* ``absent`` - text that must not be in the output (navigation, scripts, the ``Unnamed: 1`` a spreadsheet parser invents);
* ``repeats`` - text that may appear once but not more (a running page header: kept once at most, not on every page).

The documents are synthetic. They are written to look like what coating R&D actually uploads - formulation tables with a
merged group header, a result table that runs over three pages, a spreadsheet with a merged title row, a two-column paper -
but they are not scans of real papers, and a parser that does well here is not thereby good at a real scanned PDF. They
measure whether the structure a born-digital file carries survives parsing, and they make a regression in that visible.

PDF cases use ASCII and Latin-1 only (µ, °, ±, ×): the generator's built-in fonts cannot draw Chinese. Chinese goes through
the DOCX, XLSX and HTML cases.

A generator library that is not installed turns its cases into ``skipped`` (with the reason) instead of failing the run.
"""
from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Truth:
    cells: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    order: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    repeats: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    id: str
    kind: str  # table | reading_order | units | noise
    ext: str
    build: Callable[[], bytes]
    truth: Truth
    needs: tuple[str, ...] = ()  # importable modules the generator uses
    note: str = field(default="", compare=False)


# ── DOCX ─────────────────────────────────────────────────────────────────────────────────────────

FORMULATION = [
    ("组分", "功能", "含量 (wt%)"),
    ("环氧树脂 E-44", "成膜物", "35.0"),
    ("磷酸锌", "防锈颜料", "8.5"),
    ("钛白粉 R-902", "着色颜料", "12.0"),
    ("BYK-333", "流平剂", "0.3"),
    ("聚酰胺固化剂", "固化剂", "18.2"),
    ("二甲苯", "溶剂", "26.0"),
]


def _docx(build: Callable) -> bytes:
    import docx

    document = docx.Document()
    build(document)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _docx_table() -> bytes:
    def build(d):
        d.add_paragraph("表 1 水性环氧底漆配方")
        table = d.add_table(rows=len(FORMULATION), cols=3)
        table.style = "Table Grid"
        for i, row in enumerate(FORMULATION):
            for j, value in enumerate(row):
                table.cell(i, j).text = value
        d.add_paragraph("注：含量按总配方质量计。")

    return _docx(build)


MECH_HEADER = ("样品", "力学性能", "")
MECH_SUBHEADER = ("", "附着力 (级)", "铅笔硬度")
MECH_ROWS = [("A-1", "0", "2H"), ("A-2", "1", "H"), ("B-1", "0", "3H"), ("B-2", "2", "F")]


def _docx_merged_header() -> bytes:
    def build(d):
        table = d.add_table(rows=2 + len(MECH_ROWS), cols=3)
        table.style = "Table Grid"
        table.cell(0, 0).text = "样品"
        merged = table.cell(0, 1).merge(table.cell(0, 2))
        merged.text = "力学性能"
        table.cell(1, 1).text = "附着力 (级)"
        table.cell(1, 2).text = "铅笔硬度"
        for i, row in enumerate(MECH_ROWS):
            for j, value in enumerate(row):
                table.cell(2 + i, j).text = value

    return _docx(build)


ORDER_BEFORE = "首先按配方称量环氧树脂和颜料，高速分散至细度小于 30 µm。"
ORDER_AFTER = "最后加入固化剂并搅拌均匀，熟化 15 min 后施工。"


def _docx_reading_order() -> bytes:
    def build(d):
        d.add_paragraph(ORDER_BEFORE)
        table = d.add_table(rows=len(FORMULATION), cols=3)
        table.style = "Table Grid"
        for i, row in enumerate(FORMULATION):
            for j, value in enumerate(row):
                table.cell(i, j).text = value
        d.add_paragraph(ORDER_AFTER)

    return _docx(build)


UNITS_SENTENCE = "干膜厚度 50 µm，固化条件 120 °C × 30 min，耐盐雾 ≥ 500 h，粘度 85±5 KU，pH 8.5-9.0。"


def _docx_units() -> bytes:
    return _docx(lambda d: d.add_paragraph(UNITS_SENTENCE))


# ── XLSX ─────────────────────────────────────────────────────────────────────────────────────────

SHEET_TITLE = "水性环氧底漆配方表（单位：wt%）"
SHEET_ROWS = [  # non-integral on purpose: a workbook stores 35.0 as 35, so "35.0" is not something a parser can recover
    ("环氧乳液", "36.5", "成膜物"),
    ("磷酸锌", "8.5", "防锈颜料"),
    ("钛白粉", "12.5", "着色颜料"),
    ("流平剂", "0.3", "助剂"),
    ("去离子水", "42.2", "分散介质"),
]
PERF_ROWS = [("附着力 (级)", "0", ""), ("耐盐雾 (h)", "500", "ASTM B117"), ("铅笔硬度", "2H", "")]


def _xlsx_title_row() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "配方"
    ws.merge_cells("A1:C1")
    ws["A1"] = SHEET_TITLE
    ws.append([])  # a blank row between the title and the table, as people leave them
    ws.append(["组分", "含量", "功能"])
    for name, amount, role in SHEET_ROWS:
        ws.append([name, float(amount), role])
    perf = wb.create_sheet("性能")
    perf.append(["项目", "结果", "标准"])
    for item, result, standard in PERF_ROWS:
        perf.append([item, int(result) if result.isdigit() else result, standard or None])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ── PDF ──────────────────────────────────────────────────────────────────────────────────────────

SALT_HEADER = ("Sample", "Zn3(PO4)2 (wt%)", "DFT (µm)", "Salt spray (h)")
SALT_ROWS = [(f"S{i:02d}", f"{4 + i * 0.5:.1f}", str(60 + i), str(300 + 20 * i)) for i in range(1, 46)]


def _pdf(build: Callable) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    build(pdf)
    return bytes(pdf.output())


def _pdf_table_multipage() -> bytes:
    def build(pdf):
        pdf.cell(0, 8, "Table 2. Salt spray results by pigment loading", new_x="LMARGIN", new_y="NEXT")
        with pdf.table(col_widths=(25, 45, 30, 40), text_align="LEFT") as table:
            for values in (SALT_HEADER, *SALT_ROWS):
                row = table.row()
                for value in values:
                    row.cell(value)

    return _pdf(build)


BORDERLESS = [
    ("Resin", "Epoxy E-44", "35.0"),
    ("Hardener", "Polyamide", "18.2"),
    ("Pigment", "Zinc phosphate", "8.5"),
    ("Pigment", "Titanium dioxide", "12.0"),
    ("Solvent", "Xylene", "26.0"),
]
BORDERLESS_HEADER = ("Type", "Material", "Amount (wt%)")


def _pdf_table_borderless() -> bytes:
    def build(pdf):
        pdf.cell(0, 8, "Table 1. Primer formulation", new_x="LMARGIN", new_y="NEXT")
        with pdf.table(col_widths=(35, 60, 40), text_align="LEFT", borders_layout="NONE") as table:
            for values in (BORDERLESS_HEADER, *BORDERLESS):
                row = table.row()
                for value in values:
                    row.cell(value)

    return _pdf(build)


LEFT_COLUMN = [
    "Zinc phosphate protects steel.",
    "It is added at 6 to 10 percent.",
    "Higher loading raises porosity.",
    "Film build was 60 microns.",
]
RIGHT_COLUMN = [
    "Salt spray ran for 500 hours.",
    "Blistering began after 380 hours.",
    "Adhesion stayed at grade 0.",
    "No creep from the scribe.",
]


def _two_columns(interleaved: bool) -> bytes:
    def build(pdf):
        def put(x: float, row: int, text: str) -> None:
            pdf.set_xy(x, 30 + 8 * row)
            pdf.cell(85, 8, text)

        if interleaved:  # the page is drawn line by line across both columns, as many word-processor exports do
            for i in range(len(LEFT_COLUMN)):
                put(15, i, LEFT_COLUMN[i])
                put(110, i, RIGHT_COLUMN[i])
        else:  # a column at a time, as a typesetter writes it
            for i, text in enumerate(LEFT_COLUMN):
                put(15, i, text)
            for i, text in enumerate(RIGHT_COLUMN):
                put(110, i, text)

    return _pdf(build)


PDF_UNITS_SENTENCE = "Dry film thickness 50 µm, cure 120 °C x 30 min, viscosity 85±5 KU, cross-hatch 0 grade."


def _pdf_units() -> bytes:
    return _pdf(lambda pdf: pdf.multi_cell(0, 8, PDF_UNITS_SENTENCE))


RUNNING_HEADER = "FormuMind Technical Note TN-114 - Confidential"
RUNNING_BODY = [
    "Page one: the primer was applied at 60 microns dry film.",
    "Page two: salt spray exposure reached 500 hours.",
    "Page three: adhesion was checked by cross-hatch after immersion.",
]


def _pdf_running_header() -> bytes:
    from fpdf import FPDF

    class Paged(FPDF):
        def header(self):
            self.set_font("Helvetica", size=8)
            self.cell(0, 6, RUNNING_HEADER, new_x="LMARGIN", new_y="NEXT")

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", size=8)
            self.cell(0, 6, f"Page {self.page_no()} of 3", align="C")

    pdf = Paged()
    for body in RUNNING_BODY:
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        pdf.set_y(40)
        pdf.cell(0, 8, body)
    return bytes(pdf.output())


# ── HTML ─────────────────────────────────────────────────────────────────────────────────────────

HTML_ROWS = [("环氧树脂", "35.0", "成膜物"), ("磷酸锌", "8.5", "防锈颜料"), ("钛白粉", "12.0", "着色颜料")]
HTML_PARAGRAPHS = [  # article-sized: a converter that extracts the main text only does so for a page with some
    "水性环氧底漆以环氧乳液作为成膜物，配合水性胺类固化剂，在钢铁表面形成致密的防腐涂层，兼顾附着力与耐水性。",
    "防锈颜料以磷酸锌为主，添加量通常在百分之六到十之间，过高会增加涂膜孔隙率并降低耐盐雾性能。",
    "施工前应将两个组分按比例混合并熟化十五分钟，干膜厚度控制在六十微米左右，常温下表干时间约为两小时。",
]
HTML_COOKIE = "Cookie settings"
HTML_TRACKER = "var tracker"


def _html_table() -> bytes:
    body = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in HTML_ROWS)
    intro = "".join(f"<p>{text}</p>" for text in HTML_PARAGRAPHS)
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>配方说明</title>
<style>td {{ padding: 2px }}</style><script>{HTML_TRACKER} = 1;</script></head>
<body><nav><a href="/">Home</a> <a href="/docs">Docs</a> <a href="#">{HTML_COOKIE}</a></nav>
<article><h1>水性环氧底漆</h1>{intro}
<table><thead><tr><th>组分</th><th colspan="2">用量与功能</th></tr></thead><tbody>{body}</tbody></table>
<p>配方中磷酸锌的用量与盐雾试验结果相关，详见下一节。</p></article>
<footer>© 2026 FormuMind</footer></body></html>"""
    return page.encode("utf-8")


# ── the set ──────────────────────────────────────────────────────────────────────────────────────


def _rows(header: tuple, rows: list) -> tuple[tuple[str, ...], ...]:
    return (tuple(header), *(tuple(r) for r in rows))


def _cells(header: tuple, rows: list) -> tuple[str, ...]:
    return tuple(c for row in _rows(header, rows) for c in row if c)


def cases() -> list[Case]:
    formulation_rows = FORMULATION[1:]
    return [
        Case("docx-table", "table", "docx", _docx_table,
             Truth(cells=_cells(FORMULATION[0], formulation_rows), rows=_rows(FORMULATION[0], formulation_rows),
                   numbers=tuple(r[2] for r in formulation_rows)),
             ("docx",), "a formulation table between a caption and a note"),
        Case("docx-merged-header", "table", "docx", _docx_merged_header,
             Truth(cells=("样品", "力学性能", "附着力 (级)", "铅笔硬度", *(c for r in MECH_ROWS for c in r)),
                   rows=(("附着力 (级)", "铅笔硬度"), *MECH_ROWS)),
             ("docx",), "a group header merged over two columns"),
        Case("docx-reading-order", "reading_order", "docx", _docx_reading_order,
             Truth(order=(ORDER_BEFORE, "环氧树脂 E-44", "聚酰胺固化剂", ORDER_AFTER),
                   rows=_rows(FORMULATION[0], formulation_rows)),
             ("docx",), "a paragraph, a table, a paragraph: the table stays between them"),
        Case("docx-units", "units", "docx", _docx_units,
             Truth(units=("50 µm", "120 °C", "30 min", "≥ 500 h", "85±5 KU", "8.5-9.0")),
             ("docx",), "micro sign, degree sign, multiplication, >=, plus-minus"),
        Case("xlsx-title-row", "table", "xlsx", _xlsx_title_row,
             Truth(cells=(SHEET_TITLE, "组分", "含量", "功能", *(c for r in SHEET_ROWS for c in r),
                          "项目", "结果", "标准", "附着力 (级)", "耐盐雾 (h)", "500", "ASTM B117", "铅笔硬度", "2H"),
                   rows=(("组分", "含量", "功能"), *SHEET_ROWS, ("耐盐雾 (h)", "500", "ASTM B117")),
                   numbers=tuple(r[1] for r in SHEET_ROWS),
                   absent=("Unnamed", "NaN")),
             ("openpyxl",), "a merged title row, a blank row, a second sheet with empty cells"),
        Case("pdf-table-multipage", "table", "pdf", _pdf_table_multipage,
             Truth(cells=_cells(SALT_HEADER, SALT_ROWS), rows=_rows(SALT_HEADER, SALT_ROWS),
                   numbers=tuple(r[1] for r in SALT_ROWS[:10]), units=("DFT (µm)",)),
             ("fpdf",), "45 rows over two pages, the header repeated"),
        Case("pdf-table-borderless", "table", "pdf", _pdf_table_borderless,
             Truth(cells=_cells(BORDERLESS_HEADER, BORDERLESS), rows=_rows(BORDERLESS_HEADER, BORDERLESS),
                   numbers=tuple(r[2] for r in BORDERLESS)),
             ("fpdf",), "columns aligned by position only, no ruling lines"),
        Case("pdf-two-column-ordered", "reading_order", "pdf", lambda: _two_columns(False),
             Truth(order=(*LEFT_COLUMN, *RIGHT_COLUMN)),
             ("fpdf",), "text drawn a column at a time"),
        Case("pdf-two-column-interleaved", "reading_order", "pdf", lambda: _two_columns(True),
             Truth(order=(*LEFT_COLUMN, *RIGHT_COLUMN)),
             ("fpdf",), "text drawn line by line across both columns"),
        Case("pdf-units", "units", "pdf", _pdf_units,
             Truth(units=("50 µm", "120 °C", "30 min", "85±5 KU")),
             ("fpdf",), "units in a born-digital PDF"),
        Case("pdf-running-header", "noise", "pdf", _pdf_running_header,
             Truth(cells=tuple(RUNNING_BODY), repeats=(RUNNING_HEADER,)),
             ("fpdf",), "a header on every page: kept once at most"),
        Case("html-table", "table", "html", _html_table,
             Truth(cells=("组分", "用量与功能", *(c for r in HTML_ROWS for c in r)),
                   rows=tuple(HTML_ROWS), numbers=tuple(r[1] for r in HTML_ROWS),
                   absent=(HTML_TRACKER, HTML_COOKIE)),
             (), "a table with a colspan header, navigation and a script around it"),
    ]
