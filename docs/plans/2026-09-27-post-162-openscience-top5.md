# Post-162 OpenScience Top-5 — Wave B in progress

Status: **Wave A done (PR #164)** · **Wave B implementing** (PaperQA · Frozen Manifest · Light Screening).

## Locked product decisions

- Wiki/STORM **export** gated by publication preflight; draft persist fail-open.
- Chat remains fail-open (findings + optional fix-loop; no send block).
- Custom MCP into chat + session Ask approval ship together in Wave A.
- Wave B: PaperQA decoupling, literature manifest / frozen corpus, light smart screening.
- RO-Crate / full AIPOCH Reviewer/ACP / Evidence bench deferred to **Wave C**.

## Wave A deliverables

| ID | Item | Flags |
|----|------|-------|
| A1 | `publication_preflight` + `/api/wiki/preflight/*` + STORM export 409 | `publication_preflight_enabled` (default true) |
| A2 | `reviewer_fix_loop` after `evidence_reviewer` | `evidence_reviewer_fix_loop_enabled` (default false) |
| A3 | MCP skill-docs, `selected_mcp_servers`, approve-session, Composer UI | `mcp_client_enabled` |

## Wave B deliverables

| ID | Item | Flags |
|----|------|-------|
| B2 | `paperqa_engine` decoupled; chat LLM / OpenAI-compat; fail-open | `paperqa_enabled` (default true) |
| B1 | `literature_manifest` + freeze + dossier/STORM prefer frozen; preflight `corpus*` | `literature_manifest_enabled` (true); `frozen_corpus_required_for_export` (false) |
| B3 | Light screening heuristics → manifest; optional auto-freeze | `literature_screening_enabled` (false); `screening_auto_freeze` (false) |

## Explicitly out of Wave B (Wave C)

RO-Crate, full Literature Library / PDF evidence runner, full AIPOCH Reviewer/ACP, Evidence golden bench.
