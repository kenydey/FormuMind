# Post-162 OpenScience Top-5 — Wave A in progress

Status: **Wave A implementing** (Publication Preflight · Reviewer Fix-loop · MCP→Chat).

## Locked product decisions

- Wiki/STORM **export** gated by publication preflight; draft persist fail-open.
- Chat remains fail-open (findings + optional fix-loop; no send block).
- Custom MCP into chat + session Ask approval ship together in Wave A.
- Smart screening / literature manifest / PaperQA hardening deferred to Wave B/C.

## Wave A deliverables

| ID | Item | Flags |
|----|------|-------|
| A1 | `publication_preflight` + `/api/wiki/preflight/*` + STORM export 409 | `publication_preflight_enabled` (default true) |
| A2 | `reviewer_fix_loop` after `evidence_reviewer` | `evidence_reviewer_fix_loop_enabled` (default false) |
| A3 | MCP skill-docs, `selected_mcp_servers`, approve-session, Composer UI | `mcp_client_enabled` |

## Explicitly out of Wave A

Smart screening, Frozen corpus, PaperQA decoupling, RO-Crate, full AIPOCH Reviewer/ACP.
