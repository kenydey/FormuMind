"""The HTML tag stripper must not be a CPU trap (round-4 fuzz finding).

``html_to_markdown`` falls back to a regex tag stripper whenever trafilatura is missing or comes back with almost
nothing — and what it strips is whatever HTML a user uploads or a URL returns. Its patterns (``<(script|style).*?>.*?</\\1>``,
``<table\\b.*?</table\\s*>``, ``\\s+\\n``) retry from every start position when the closing part is absent. Measured before the
fix: **2000 unclosed ``<script>`` tags (16 KB) took more than 20 s** (cubic), a truncated download with one unclosed
``<script>`` and a few thousand ``>`` took minutes, 300 KB of whitespace took 149 s, 8000 unclosed ``<table>`` 2 s and
growing quadratically. Each is now one pass; the bounds below are generous (the old code needed minutes) so they
hold on a slow CI machine too.
"""
from __future__ import annotations

import random
import re
import sys
import time

import pytest

from app.services import parsing


@pytest.fixture(autouse=True)
def _no_trafilatura(monkeypatch):
    """Exercise the regex fallback: with trafilatura importable it would answer first."""
    monkeypatch.setitem(sys.modules, "trafilatura", None)


def _seconds(fn, *args) -> float:
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


ADVERSARIAL = {
    "unclosed script tags": "<script>" * 20_000,
    "unclosed style tags": "<style>" * 20_000,
    "unclosed script then a page of tags": "<script>" + "<p>text</p>" * 30_000,
    "unclosed tables": "<table>" * 20_000,
    "a long whitespace run": " " * 300_000 + "x",
    "a long tab/space run": ("\t " * 150_000) + "x",
    "lone angle brackets": "<" * 300_000,
    "angle brackets and words": "< a" * 100_000,
    "many tiny tables": "<table><tr><td>1</td></tr></table>" * 10_000,
    "one huge table": "<table>" + "<tr><td>1</td><td>2</td></tr>" * 30_000 + "</table>",
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_html_is_stripped_in_bounded_time(name):
    elapsed = _seconds(parsing.html_to_markdown, ADVERSARIAL[name])
    assert elapsed < 8.0, f"{name}: {elapsed:.1f}s"


# ── the answers did not change on ordinary pages ────────────────────────────────


def test_script_and_style_are_dropped_but_their_neighbours_stay():
    html = "<p>keep one</p><script type='x'>var a = 1 < 2;</script><STYLE>p{color:red}</STYLE><p>keep two</p>"
    out = parsing.html_to_markdown(html)
    assert "keep one" in out and "keep two" in out
    assert "var a" not in out and "color" not in out


def test_an_unclosed_script_does_not_swallow_the_rest_of_the_page():
    out = parsing.html_to_markdown("<p>before</p><script>var x;<p>after</p>")
    assert "before" in out and "after" in out


def test_a_closing_tag_with_spaces_is_still_a_closing_tag():
    assert "hidden" not in parsing.html_to_markdown("<p>shown</p><script>hidden</script ><p>also shown</p>")


def test_tables_still_become_pipe_tables():
    out = parsing.html_to_markdown(
        "<p>intro</p><table><tr><th>Property</th><th>Value</th></tr><tr><td>Solids</td><td>65 %</td></tr></table><p>outro</p>"
    )
    assert "| Property | Value |" in out and "| Solids | 65 % |" in out and "intro" in out and "outro" in out


def test_an_unclosed_table_is_left_to_the_tag_stripper():
    out = parsing.html_to_markdown("<p>a</p><table><tr><td>cell</td></tr>")
    assert "a" in out and "cell" in out


def test_two_tables_with_prose_between_them():
    html = (
        "<table><tr><td>a</td><td>1</td></tr></table><p>between</p>"
        "<table><tr><td>b</td><td>2</td></tr></table>"
    )
    out = parsing.html_to_markdown(html)
    assert "| a | 1 |" in out and "| b | 2 |" in out and "between" in out and out.index("between") > out.index("| a | 1 |")


def test_blank_lines_collapse_as_before():
    assert parsing.html_to_markdown("<p>one</p><p>two</p>") == "one\n two"


@pytest.mark.parametrize("seed", range(5))
def test_the_whitespace_collapse_is_the_same_function_as_the_old_regex(seed):
    """``re.sub(r"\\s+\\n", "\\n", text)``, minus the quadratic backtracking: equal on arbitrary mixes of whitespace."""
    rng = random.Random(seed)
    alphabet = [" ", "\t", "\n", "\r", "\f", " ", "　", "a", "b", "<", ">"]
    for _ in range(4000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        assert parsing._collapse_whitespace_before_newline(text) == re.sub(r"\s+\n", "\n", text), repr(text)


def test_table_spans_match_the_first_closing_tag_and_skip_what_they_consumed():
    html = "x<table>a<table>b</table>c</table>y<table>d</table>"
    spans = list(parsing._table_spans(html))
    assert [html[s:e] for s, e in spans] == ["<table>a<table>b</table>", "<table>d</table>"]
