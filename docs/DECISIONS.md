# Decisions Log

Every major decision should be recorded once and updated with a superseded
marker if it later changes.

## 2026-04-23 — Project Identity Reset

Status: accepted

Reason:

The repo had drifted into a mix of legacy wiki-control-plane documentation and
new cleanroom Rust implementation. The project needed one canonical identity.

Decision:

`skynet` is now documented as a review-gated cleanroom knowledge-work system
with a dense operator shell, not as a generic wiki tooling repo.

Alternatives rejected:

- Keep the old wiki-build roadmap as the main story.
- Split cleanroom work into a separate repo before doctrine was frozen.

Downstream consequences:

- Roadmap and task tracking must reference cleanroom phases and milestones.
- Review semantics become first-class architecture, not incidental workflow.
- Shell work must be evaluated against visible truth, not visual polish.

## 2026-04-23 — CLI-Backed Codex Adapter First

Status: accepted

Reason:

The public Codex CLI surface documents `codex cloud exec`, `codex cloud list
--json`, and `codex apply`, which is sufficient for bounded task automation
without inventing a private integration.

Decision:

The first real cloud worker uses a CLI-backed adapter that writes packet and
schema files into the repo, submits bounded tasks, polls task status, applies
the resulting diff, and validates the produced JSON artifact.

Alternatives rejected:

- direct SDK integration first
- artifact fetching through undocumented CLI behavior
- in-memory handoff without repo-file artifacts

Downstream consequences:

- `.cleanroom/` becomes a first-class local artifact area
- environment ID and CLI auth become operator prerequisites
- task resolution remains dependent on documented list/poll/apply flows
