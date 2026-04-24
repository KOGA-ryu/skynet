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

## 2026-04-24 — Packet Claim Acknowledges Visible Review Surface

Status: accepted

Reason:

The live shell proof exposed a mismatch between the visible operator workflow
and the persisted gate state. A reviewer could claim a packet in the shell, but
the backend still left the gate at `needs_review`, which meant approve could
never become available through the real UI.

Decision:

Claiming a packet in the storage-backed shell now records acknowledgement of the
currently visible diff and evidence review surface for the claiming reviewer and
recomputes the persisted gate state. This keeps the live shell path aligned
with the explicit workflow used in Milestone 1: claim -> inspect visible review
surface -> approve.

Alternatives rejected:

- Require a separate hidden or future-only acknowledgement control before
  approve.
- Leave the shell blocked and treat approve as unreachable in the live path.

Downstream consequences:

- The shell claim action now has meaningful review-state side effects, not just
  queue assignment.
- Gate state remains Rust-backed and persists immediately after claim.
- Future stricter review policy must be mirrored explicitly in both the backend
  gate contract and the shell surfaces.

## 2026-04-24 — Approve Is Strict; Reject And Rework Stay Available When Claimed

Status: accepted

Reason:

Milestone 1 proved the live shell review loop, but the remaining Phase 4 work
still allowed policy drift between backend mutations and the shell's action
state. The repo needed one explicit rule for when each review action is allowed.

Decision:

`approve` is the strict action: it requires an `in_review` packet claimed by the
session reviewer and a persisted gate state with `approve_enabled = true`.
`reject` and `rework` remain available on a claimed `in_review` packet when the
required review fields are loaded and note policy passes, even if stale, dirty,
or blocker state still blocks approval.

Alternatives rejected:

- Block reject and rework behind the same stale/dirty/blocker rules as approve.
- Keep separate shell-service and cleanroom action checks.

Downstream consequences:

- Backend mutation-time errors use stable review precondition codes.
- The shell renders backend policy instead of inventing its own action logic.
- Blocker handling is explicit: blockers block approve, but are preserved on
  reject and rework artifacts instead of silently preventing those actions.
