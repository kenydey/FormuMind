---
name: peer-review
description: Referee a STORM/dossier draft or formulation evidence answer the way a careful peer reviewer does — BLOCKING vs OBSERVATION, calibrated recommendation. Use for pre-export review, critical appraisal of process claims, or checking whether evidence supports the wording. Does not rewrite the draft (use method-writer / user request). For claim↔passage tables use sources; for DOI metadata use citations.
summary: 审稿纪律 — BLOCKING / OBSERVATION，不自动改稿
category: research
activation_policy: user-controlled
allowed_tools: literature_search, scholar_doi, evidence_synthesis, kb_hybrid
entry: true
license: Apache-2.0
metadata: adapted-from=synthetic-sciences/openscience skills/core/peer-review
---

# Peer review（FormuMind）

Review **whether the evidence supports the claims as written** for coatings /
polymer / silane formulation R&D reports. Deliver a referee report — **do not
rewrite** the manuscript unless the user explicitly asks.

## Non-negotiables

1. **Read the whole artifact** provided (STORM longform, dossier report, or chat draft).
2. **Check, do not just skim.** Numbers, units, process windows, and cited `[^n]` /
   DOIs must match what the sources actually say.
3. **Every point is BLOCKING or OBSERVATION.**
   - **BLOCKING** — central claim unsupported, contradicted, wrong scope, fabricated
     citation, or missing steel-stamp essentials (unbound refs / placeholders).
   - **OBSERVATION** — clarity, missing locator, optional experiments — does not
     overturn the claim by itself.
4. **Locate every issue** (section / heading / `[^n]` / approximate line).
5. **Calibrate the recommendation** from the blocking list:
   - `accept` — no blocking
   - `minor revision` — few fixable blocking items
   - `major revision` — central claims need new evidence or redesign
   - `reject` — core narrative unsupported or contradictory
6. **Constructive.** Each BLOCKING item names the fix or experiment needed.

## Deliverable

```markdown
## Peer review

**Recommendation:** minor revision

### BLOCKING
1. … (location) — fix: …

### OBSERVATION
1. … (location)

### Summary
Supported claims / contested claims / next experiments.
```

## Division of labour

- **publication preflight** — mechanical steel-stamp (refs, placeholders, locators)
- **sources** — claim→passage audit table
- **peer-review** — methodological / evidentiary referee judgment (this skill)
