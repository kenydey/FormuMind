# Post-162 OpenScience Top-5 — Wave C implemented

Status: **Wave A/B merged** · **Wave C implemented** (citation expand · provenance · citations skill · ChEBI).

## Locked product decisions

- Wiki/STORM **export** gated by publication preflight; draft persist fail-open.
- Chat remains fail-open.
- Wave C: OpenAlex citation expand + retraction directionality, structured provenance, Citations skill, ChEBI connector.
- Deferred further: RO-Crate, full Literature Library / PDF runner, full ACP Reviewer, Evidence golden bench.

## Wave A / B (done)

See prior PRs #164 / #165.

## Wave C deliverables

| ID | Item | Flags |
|----|------|-------|
| C1 | `expand_citations` + `notice_kind` in `scholar_helpers` | `citation_expand_enabled` (true) |
| C2 | `evidence_provenance` on ChatResponse + UI strip | `evidence_provenance_enabled` (true) |
| C3 | `citations` Chat Skill (resolve-before-cite) | `chat_skills_runtime_enabled` |
| C4 | ChEBI enrichment on chemistry connector | `connectors_builtin_enabled` |

Plan: [`2026-09-27-wave-c-citation-provenance.md`](./2026-09-27-wave-c-citation-provenance.md).
