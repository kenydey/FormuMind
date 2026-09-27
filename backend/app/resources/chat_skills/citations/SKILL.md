---
name: citations
description: Resolve-before-cite — every bibliography entry must come from a live Crossref/OpenAlex/arXiv/PubMed record; audit fabricated or mismatched DOIs. Use when attaching sources to claims, repairing footnotes, or checking a draft's references. Not for discovering what to read (use literature-review).
summary: 先解析再引用 — DOI/OpenAlex 核实后才进脚注
category: research
activation_policy: user-controlled
allowed_tools: literature_search, scholar_doi, evidence_synthesis
entry: true
license: Apache-2.0
metadata: adapted-from=synthetic-sciences/openscience skills/core/citations
---

# Citations（FormuMind）

A citation is a claim that a specific document says a specific thing. Language models
produce plausible references that do not exist. The rule is absolute for formulation R&D:

**Nothing enters the footnotes that was not resolved from a live record.**

## Non-negotiables

1. **Resolve before you cite.** Every entry comes from a record fetched now (DOI at
   Crossref, work at OpenAlex, arXiv id). Author / year / title / venue / DOI are
   copied from that record — never recalled or “completed” from memory.
2. **Read before you attribute.** Cite a paper for a process window or numeric claim
   only after the retrieved snippet (or abstract) actually supports it. A title is not
   evidence of what a paper found.
3. **One canonical identifier per entry.** Prefer DOI; arXiv id for preprints without one.
4. **Prefer the published version** over the preprint when both exist; say so if numbers
   came from the preprint.
5. **Retraction / correction awareness.** If DOI verification reports `retracted_work`
   or `corrected`, do not treat the claim as established — mark **无据 / contested**.
6. **Missing evidence → say so.** Write **无据 / unavailable** rather than inventing a
   closest paper.

## Workflow

1. Collect candidate claims that need sources.
2. For each claim, retrieve or select a matching Evidence / KB chunk / literature hit.
3. Emit `[^n]` only for anchors that exist in the provided source list.
4. If a DOI appears in the prose, it must survive Crossref verification (`status=ok`).
5. End with a short 「引用边界」: which claims remain unverified.

## Coatings / formula context

When citing cure temperature, wt%, adhesion MPa, or silane process windows, the
numeric claim and the citation must come from the same source passage — never stitch
a number from memory onto an unrelated DOI.
