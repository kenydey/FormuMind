"""Adversarial text sweep (round-4): which text-processing function is a CPU trap?

Calls every function in ``app.services`` / ``app.pipeline`` / ``app.domain`` / ``app.evals`` that looks like pure
text processing (a name that suggests parsing / splitting / extracting / matching, a first parameter that takes a string,
nothing else required, and not a name that suggests I/O) with 6000-character strings built from one repeated character
or token — spaces, tabs, newlines, ``<``, ``(``, ``[``, ``"``, ``|``, ``*``, ``#``, digits, CJK, ``(a`` pairs, a heading
followed by padding … — under a 6 s alarm, and reports anything that took more than 0.7 s. A regex that retries from every
start position when its closing part is missing is quadratic or worse in exactly these inputs.

Run from ``backend/``::

    python ../scripts/audit/text_sweep.py

Entries that are network round trips (a PubChem / OpenAlex lookup whose "text" is a SMILES or a DOI) show up as slow too:
read the name before chasing one. What it found (all fixed, see ``test_html_fallback_linear.py``,
``test_chunking_linear.py``, ``test_numeric_regex_linear.py``): an HTML tag stripper that was cubic on unclosed ``<script>``
tags, a heading regex cubic on trailing spaces, the numeric gate quadratic on a long digit run. To measure scaling rather than a
single point, call the suspect with 2x and 4x the size: a ratio of about 4 per doubling is quadratic.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import os
import pkgutil
import re
import signal
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)
sys.path.insert(0, os.getcwd())  # backend/
os.chdir(tempfile.mkdtemp(prefix="textsweep-"))  # functions that write relative paths write them here
os.environ.setdefault("FORMUMIND_ENVIRONMENT", "test")
TEXT_PARAMS = {"text","md","markdown","html","s","string","query","q","raw","content","snippet","line","title","answer","body","cell","value","header","caption","name","message","msg","sentence","paragraph","doc","document","formula","smiles","note","notes","description","label","expr","expression","unit","units","token","tokens_text","prompt"}
GOOD = re.compile(r"parse|split|chunk|extract|clean|normali[sz]e|strip|token|detect|find|match|sanitiz|escape|slug|format|render|markdown|classif|score|count|facet|rewrite|segment|redact|mask|truncat|summar|numbers|units?_|_units?|cas|formula|smiles|cite|citation|claim|sentence|keyword|lang|word|char|ratio|garbage|gate|header|cell|caption|heading|table")
BAD = re.compile(r"save|write|persist|store|ingest|download|fetch|send|post|upload|delete|remove|create|insert|update|index|embed|call|run|execute|submit|dispatch|publish|enqueue|open|load|read|start|stop|kill|spawn|login|auth|token_|install|register|train|fit|optimi|recommend|search|retriev|request|connect|probe|check_|ping|sync|commit|migrate|seed|bootstrap|reset|clear|drop|apply|link|merge|import|export|generate|llm|chat|complete|answer_|ask")
class TO(Exception): pass
signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TO()))
def adv(n):
    return {
        "spaces": " " * n + "x", "tabs": "\t" * n + "x", "nl": "\n" * n, "lt": "<" * n, "paren": "(" * n, "bracket": "[" * n,
        "brace": "{" * n, "quote": '"' * n, "pipe": "|" * n, "star": "*" * n, "under": "_" * n, "tick": "`" * n, "dollar": "$" * n,
        "backslash": "\\" * n, "hash": "#" * n, "dash": "-" * n, "digit": "1" * n, "dot": "." * n, "comma": "," * n, "colon": ":" * n,
        "a": "a" * n, "A1": "A1" * (n // 2), "cjk": "盐" * n, "mix": "(a" * (n // 2), "heading_ws": "# a" + " " * n + "b",
        "num_words": "1 " * (n // 2) + "x", "url": "http://" + "a" * n, "semi": ";" * n, "ab": "ab" * (n // 2) + "!", "eq": "=" * n,
    }
mods = []
for pkg in ("app.services", "app.pipeline", "app.domain", "app.evals"):
    try: p = importlib.import_module(pkg)
    except Exception: continue
    for m in pkgutil.walk_packages(p.__path__, pkg + "."):
        mods.append(m.name)
slow = []; calls = 0; skipped_mods = 0
for name in sorted(set(mods)):
    try: mod = importlib.import_module(name)
    except Exception: skipped_mods += 1; continue
    for fname, fn in inspect.getmembers(mod, inspect.isfunction):
        if fn.__module__ != name: continue
        if BAD.search(fname) or not GOOD.search(fname): continue
        try: sig = inspect.signature(fn)
        except Exception: continue
        params = list(sig.parameters.values())
        if not params: continue
        first = params[0]
        if first.name not in TEXT_PARAMS and not (first.annotation in (str, "str")):
            continue
        if any(p.default is inspect._empty and p is not first and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in params[1:]):
            continue
        for label, s in adv(6000).items():
            calls += 1
            signal.alarm(6); t0 = time.perf_counter()
            try: fn(s)
            except TO: slow.append((name, fname, label, "TIMEOUT>6s@6k"))
            except BaseException: pass
            finally: signal.alarm(0)
            dt = time.perf_counter() - t0
            if dt > 0.7 and not (slow and slow[-1][:3] == (name, fname, label)):
                slow.append((name, fname, label, f"{dt:.2f}s@6k"))
print("modules:", len(set(mods)), "skipped:", skipped_mods, "calls:", calls)
for row in slow: print(row, flush=True)
print("slow:", len(slow))
