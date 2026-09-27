---
name: sources
description: Audit claims against their sources — each statement traced to a passage and graded supported, partial, unsupported, or contradicted. Deliverable is a provenance table, not reassurance. Use for fact-check, "is this actually supported", or before exporting a STORM/dossier draft. For DOI metadata use citations; for methodology referee use peer-review.
summary: 主张↔原文对照审计表（supported / partial / unsupported / contradicted）
category: research
activation_policy: user-controlled
allowed_tools: literature_search, scholar_doi, evidence_synthesis, kb_hybrid
entry: true
license: Apache-2.0
metadata: adapted-from=synthetic-sciences/openscience skills/core/sources
---

# Sources audit（FormuMind）

A sources audit answers one question for every claim: where exactly does this come
from, and does that place say this? For coatings / polymer / silane formulation R&D
the deliverable is a **table**, not a reassurance. Do **not** rewrite the draft unless
the user asks.

## Non-negotiables

1. **Atomize.** Split into claims small enough to be true or false alone.
   "Epoxy A improves wet adhesion by 20% and lowers VOC" is two claims.
2. **Locate, then read.** Find the exact supporting passage (sentence, table cell,
   process window). Read it in context — limitations paragraphs often reverse the claim.
3. **Grade against the wording.**
   - **supported** — source says this, in this scope
   - **partial** — narrower, weaker, conditional, or unit-rounded
   - **unsupported** — no source, or source does not address it
   - **contradicted** — source says otherwise, or correct fact attributed to the wrong paper
4. **Numbers are exact.** Value, units, precision, uncertainty, and conditions must match.
5. **Report, do not repair.** List findings; rewriting is a separate user request.
6. Prefer FormuMind `sources_audit` / claim-check rows when present; fill gaps manually.

## Deliverable shape

```markdown
| # | Claim | Grade | Source / locator | Note |
| 1 | … | supported | [^2] pp. 4 | |
| 2 | … | contradicted | [^1] | source reports dry adhesion only |
Supported N · Partial N · Unsupported N · Contradicted N
Fix first: highest-impact contradicted / unsupported claims.
```

## Division of labour

- **citations** — resolve DOI / OpenAlex metadata before a footnote exists
- **sources** — after claims exist, audit claim ↔ passage
- **publication preflight** — export steel-stamp for unbound refs / placeholders / naked numbers
