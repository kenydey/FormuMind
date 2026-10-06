# Evaluation harness

Four labelled suites that score what the application *does*, as numbers, so a change can be read as better or worse instead
of argued about. Until this existed the repository had about 4,900 tests of whether code runs and no measure of whether retrieval
finds the right document, whether a parsed table is still a table, or whether the optimiser beats picking at random.

```
cd backend
python -m evals                                    # all four suites, ~25 s, offline, deterministic
python -m evals --suite retrieval,qa               # some of them
python -m evals --out report.json --baseline evals/baselines/baseline.json --fail-on-regression
python -m evals --update-baseline                  # after a change you meant to make
python -m evals --suite optimization --engines botorch-ei   # the slow engine, on its own (needs the `bo` extra)
```

Everything runs against a throw-away knowledge base (`evals/env.py` swaps the process-wide database, stores and settings
cache, and puts them back). Nothing needs the network, a key or a model, so the suites run in CI. The CI job `evals` is
non-blocking: it reports the numbers and the movement against the baseline, it does not gate a merge.

## The suites

| suite | what is run | what is scored |
|---|---|---|
| **retrieval** | 52 short coating-R&D documents (44 Chinese, 8 English, 11 topic clusters that deliberately share vocabulary) ingested through `ingest_document_tx`; 86 queries asked through `kb_index.retrieve_evidence(mode="hybrid")` - the path chat takes | success@1/3/5, recall@5/10, MRR@10, graded nDCG@10 with a bootstrap interval, per query category (`lexical`, `paraphrase`, `numeric`, `hard_negative`, `crosslingual`, `multi_doc`); for the 10 unanswerable queries, whether the retriever's confidence separates them from answerable ones (AUROC) |
| **qa** | the same retrieval, read as "what reaches the answerer" | fact recall@1/3/5 (the figures and conditions a correct answer needs, from the dataset), full-context@k, context precision@5, and the empty-evidence gate: how often retrieval returns nothing for an unanswerable question (chat refuses outright then) and for an answerable one |
| **parsing** | 12 small real files generated from a specification (`parsing_cases.py`): DOCX tables with a merged header, a table between two paragraphs, an XLSX with a merged title row, PDFs with a table over two pages, a borderless table, two columns read straight across, units, a running header, an HTML page with navigation | per case: table cells present, table rows still on one line, reading order, figures digit for digit, value and unit adjacent, nothing that should be absent (`Unnamed: 1`, `NaN`, a script, a header repeated on every page) |
| **optimization** | each engine in `services/optimizer.py` runs the suggest/observe loop for 30 experiments, 10 seeds, with and without 3 % measurement noise, on Branin, Hartmann-3, Hartmann-6 and a formulation-shaped ridge | normalised regret of the best point found (0 at the optimum, 1 at a random point's average), and the *paired* advantage over random search with a bootstrap interval |

Every suite is also run against references, because a number alone means little: the retrieval and QA suites against a plain
BM25 (own tokenizer, no shared code with the application) and a seeded random order; parsing against the repository's own
last-resort tiers (python-docx paragraphs, the openpyxl dump, pypdf text, a tag stripper); optimisation against uniform random
points and a shifted Halton sequence. "Below the reference" is a regression or a missing extra.

## Baseline and tolerance

`evals/baselines/baseline.json` stores about sixty headline numbers, each with the direction that is better and a tolerance:
0.03 for retrieval, QA and parsing (deterministic, so any movement is a change of behaviour; the tolerance only keeps a one-query
flip in a nine-query category from reading as a regression), 0.05 for optimisation (seeded, but a new Optuna release changes its
sampler). `python -m evals` prints what is worse, better, unchanged, new; `--fail-on-regression` turns "worse" into a failing
exit status. The numbers depend on the installed libraries (jieba, markitdown, pymupdf4llm, optuna - versions are in the report's
`meta`); the baseline was taken with the pins in `requirements.txt` and without the embedding and BoTorch extras. With the same
library versions the numbers are reproducible across machines: the first CI run produced exactly the 58 numbers of a local run.
`tests/test_evals_retrieval_qa.py` pins the retrieval and QA numbers to the baseline to within 0.005, so a baseline that lags the
datasets or the code cannot be committed unnoticed.

## What the harness has found so far

All fixed in the commit that introduced the suite that found it; each fix has its own regression test.

* **The BM25 tokenizer treated blanks as terms and never lowercased text with Chinese in it.** `ISO` in a Chinese question never met
  `(iso` in the English document that answers it; every query containing a space matched every text containing one. Numeric queries
  answered by an English document: recall@10 0.71 → 1.00; overall success@1 0.70 → 0.74.
  (`hybrid_search._tokenize`, `tests/test_hybrid_tokenizer.py`)
* **The first page of a PDF took its layout from the PDF parsed before it.** pymupdf's layout model caches under
  `(doc.name, page.number)` and a document opened from bytes has no name: after any one-page PDF the next PDF's page 1 came back
  blank or in the wrong order (a one-page PDF followed by a three-page one: page 1 with 0 characters of 119), and the assembler skipped
  the blank page silently. Parsing mean case score 0.65 → 0.91 on this one defect.
  (`pdf_local._layout_name`, `tests/test_pdf_local_layout_cache.py`)
* **A spreadsheet's title row became `Unnamed: 1`, empty cells became `NaN`, and the real header became a data row** - in the indexed
  text and in the table the contract normalises (its columns were `['水性环氧底漆配方表（单位：wt%）', 'Unnamed: 1', 'Unnamed: 2']`
  instead of `['组分', '含量', '功能']`). Case score 0.75 → 1.00 (the two junk patterns present → none; the cells and rows were all there, in the wrong places).
  (`parsing._parse_xlsx_tables`, `tests/test_xlsx_tables.py`)
* **The BM25 channel indexed Chinese only as jieba words, and the application's retriever trailed a plain BM25 over character pairs.**
  jieba reads `耐盐雾性能` as `耐盐` / `雾` / `性能`, so a question for `盐雾` shared no token with the document that answers it, and
  `拉开法附着力试验的合格数值` did not return the cross-cut adhesion document at all: the one rare word both texts share (`合格`) decided
  the ranking. Without the vector channel the application's success@1 was 0.737 against 0.816 for the reference. The tokenizer now
  indexes the overlapping character pairs of each Chinese run besides the words: success@1 0.737 → 0.816, nDCG@10 0.771 → 0.800
  (paired bootstrap of the nDCG difference over the 76 answerable queries: +0.03, 95 % interval +0.01 to +0.05), MRR@10 0.814 → 0.863, paraphrase success@1 0.40 → 0.67, QA fact recall@1 0.66 → 0.73. The same change caches the cut:
  jieba costs about 2.5 ms per 700-character chunk and every query repeated it for every chunk of the scan - about 3 s a query over 1,500
  chunks before, 0.2 s after (through `hybrid_search_scored`). Variants tried on the way, same data
  (`scripts/audit/bm25_tokenizer_variants.py` reproduces the table, with the intervals): jieba's search mode 0.789 / nDCG 0.793,
  words + pairs + single characters 0.842 / 0.810 (one hard-negative question lost), characters + pairs without jieba 0.855 / 0.823 -
  better on this corpus, dropped because it throws away the dictionary on a corpus of 52 documents that I wrote.
  (`hybrid_search._tokenize`, `tests/test_hybrid_tokenizer.py`, `tests/test_hybrid_search.py`)
* (side effect) The API fuzzer was sending its hostile input to the real SureChEMBL, OpenAlex, DuckDuckGo and USPTO; it now shares the
  test suite's network guard.

## What it says about the current state (not fixed; measured)

Numbers from `python -m evals` at the last baseline refresh; see the baseline for the exact values.

* **Retrieval confidence carries no information.** The hybrid retriever normalises scores by the best hit, so the top hit's
  relevance is a constant (0.3 here) whatever was asked. Abstention separability (AUROC) is 0.50, against 0.87 for a plain BM25's
  per-term score. Together with the QA suite's finding that retrieval **never returns nothing** (0 of 10 unanswerable questions got an
  empty result, so chat's `if not citations` gate never fires on a populated knowledge base), refusing to answer rests entirely on the
  LLM claim check.
* **Cross-language questions are still retrieved poorly, paraphrases less so:** success@5 0.44 for the nine cross-language questions
  (a plain BM25 gets 0.56; it was 0.33 before the character pairs), success@1 0.22 (0.44); paraphrase success@1 0.67 (0.53 for the
  reference). The vector channel is not exercised by this offline suite (`config.vector_channel` says whether it was present); the
  Docker image has it, so the cross-language numbers are probably a floor - not measured. Part of what is left is the scoring, not the
  tokens: with the same tokens and a Lucene-style IDF the application's tokenization reaches 0.56 on the cross-language success@5
  (one question of nine) - `rank_bm25.BM25Okapi` gives a term that is in more than half the documents a positive floor instead of its
  negative IDF. Not changed: it is the scoring of every question the application answers, and it moves one question here.
* **The default optimiser is no better than random search.** At 30 experiments the numpy-UCB engine (what runs when neither Optuna
  nor BoTorch is installed) ends with regret 0.186 against 0.184 for random points (paired advantage −0.001, clearly ahead on 0 of 4
  functions). Optuna's TPE: 0.079, clearly ahead on 3 of 4 (hartmann6 1.00 win rate). BoTorch GP-EI (3 seeds): 0.012. With 3 % noise:
  0.201 / 0.102 / 0.029. The Docker image installs both extras, so there the default is BoTorch; a bare `pip install` gets the numpy engine.
* **Running page headers survive PDF parsing** (the header text is in the output once per page): the `pdf-running-header` case is
  the only parsing case below 1.0. Whether a real header, with a rule and another font, is dropped by the layout model was not measured.

## Limits - read before trusting a number

* **The datasets are synthetic.** The documents and questions were written for this harness, in the vocabulary of the topic they
  cover; the parsing files are generated, not scanned. Lexical overlap between a question and its document is probably higher than in
  real use, so `lexical` is easy and the absolute numbers are optimistic. What the harness is good for is *difference*: the same
  data before and after a change, the application against a plain BM25.
* **Small.** 86 questions, 12 parsing cases, 4 test functions. A category of nine questions moves by 0.11 when one answer flips.
  The bootstrap intervals in the report say how much a mean can be trusted; the tolerances above say how much movement is called a change.
* **No model in the loop.** Answer quality (is the answer faithful, does it cite the right evidence) needs a live LLM and is not
  measured offline. `qa.score_answers` scores fact recall, refusals of unanswerable questions and answers to answerable ones for any
  `answerer(question, snippets) -> text`; run it with `python -m evals --suite qa --answerer package.module:function`.
* **No scanned documents, no OCR, no Chinese PDFs** (the generator's built-in fonts cannot draw Chinese; Chinese is covered by the
  DOCX, XLSX and HTML cases).
* The optimiser study uses standard test functions, not coating data: it says whether an engine searches well, not whether it
  searches this lab's response surface well.

## Adding to it

* **A query or document**: append a line to `datasets/retrieval_queries.jsonl` / `retrieval_corpus.jsonl`. `relevant` maps document id to a
  grade (3 answers it, 2 useful, 1 related); `facts` are the strings (or lists of acceptable variants) the context must contain.
  `tests/test_evals_datasets.py` checks the invariants (ids exist, facts occur in the gold document, categories are known).
* **A parsing case**: add a builder and a `Truth` to `parsing_cases.py`. A generator library that is not installed turns its cases into
  `skipped`, not a failure.
* **A benchmark function or an engine**: `benchmarks.py` (unit cube, maximised, known optimum) / `suites/optimization.py:engines`.
* After a change that moves numbers on purpose: `python -m evals --update-baseline` and commit the file with the change.
