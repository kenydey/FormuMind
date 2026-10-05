"""A heading line must not be a CPU trap for the chunker (round-4 sweep finding).

``chunking._HEADING_RE`` was ``^(#{1,6})\\s+(.+?)\\s*#*\\s*$``: a lazy title in front of two adjacent ``\\s*`` around an optional
``#*``. A heading followed by a long run of spaces — the padding a layout-preserving PDF conversion produces — was retried from
every position of the run, cubically: a ``# a`` line with 20,000 trailing spaces took longer than 20 s, and ``chunk_markdown``
runs on every document that is ingested. ``_parse_heading`` gives the same answers in linear time (differential test below
against the original pattern). The same sweep found ``table_normalize``'s bracket-suffix pattern quadratic on a cell made of
opening brackets.
"""
from __future__ import annotations

import random
import re
import time

import pytest

from app.services import chunking, table_normalize

OLD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _old(line: str):
    m = OLD_HEADING.match(line)
    return None if m is None else (len(m.group(1)), m.group(2).strip())


@pytest.mark.parametrize("seed", range(4))
def test_parse_heading_answers_exactly_as_the_old_pattern_did(seed):
    rng = random.Random(seed)
    alphabet = ["#", " ", "\t", "a", "b", "\r", " ", "C", "　"]
    for _ in range(60_000):
        line = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        assert chunking._parse_heading(line) == _old(line), repr(line)


@pytest.mark.parametrize(
    "line, expected",
    [
        ("# Title", (1, "Title")),
        ("## 固化剂 ##", (2, "固化剂")),
        ("###### deep", (6, "deep")),
        ("####### seven", None),
        ("#nospace", None),
        ("# C#", (1, "C")),  # the old pattern strips trailing hashes even without a space before them
        ("# ###", (1, "#")),
        ("#   ", (1, "")),
        ("# ", None),
        ("plain", None),
    ],
)
def test_the_known_corner_cases(line, expected):
    assert chunking._parse_heading(line) == expected == _old(line)


@pytest.mark.parametrize(
    "doc",
    [
        "# a" + " " * 200_000 + "b",
        "# a" + " " * 200_000,
        "## " + " " * 200_000 + "x ##" + " " * 100_000,
        "# a" + " #" * 100_000,
        ("# h\n\n" + " " * 50_000 + "\n") * 20,
    ],
)
def test_headings_with_long_whitespace_runs_are_chunked_in_bounded_time(doc):
    start = time.perf_counter()
    chunking.chunk_markdown(doc)
    assert time.perf_counter() - start < 5.0


def test_sections_still_split_on_headings():
    sections = chunking._split_sections("intro\n\n# A\n\ntext a\n\n## B ##\n\ntext b")
    assert [path for path, _ in sections] == ["", "A", "A > B"]


def test_a_cell_of_opening_brackets_is_normalised_in_bounded_time():
    start = time.perf_counter()
    table_normalize._norm_name_key("(" * 60_000)
    table_normalize._norm_name_key("粘度 " + "（" * 60_000)
    assert time.perf_counter() - start < 3.0


def test_a_bracket_suffix_on_a_property_name_is_still_dropped():
    assert table_normalize._norm_name_key("粘度 (25 °C)") == table_normalize._norm_name_key("粘度")
    assert table_normalize._norm_name_key("固含量（质量分数）") == table_normalize._norm_name_key("固含量")
