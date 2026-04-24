# Cleanroom Milestone Ledger

Last updated: 2026-04-24

This file is the active execution ledger for the cleanroom roadmap. Every new
implementation packet should reference a phase and milestone from
[ROADMAP.md](ROADMAP.md).

## Current Checkpoint

### Current Phase

Phase 4: Review Queue and Cleanroom Actions

### Current Milestone

Milestone 1: One packet enters, one clean node exits

### Working Surfaces

- Rust cleanroom core under `src/`
- SQLite-backed packet lifecycle and replay
- CLI-backed Codex cloud adapter
- live shell runtime under `src/shell/`
- Qt host and QML operator shell under `qt/`

### Locked Decisions

- Raw source is immutable.
- Promotion is review-gated.
- Traceability must survive promotion.
- Approve is stricter than reject or rework.
- The shell must keep critical workflow truth visible.
- Codex cloud work is bounded by packet files, schema files, and explicit
  output contracts.

### Open Decisions

- Packet versioning semantics for rework attempts.
- Post-approval metadata richness and relationship edge taxonomy.
- Real stale/dirty derivation beyond the current persisted gate inputs.

### Risks

- UI implementation outruns backend gate truth.
- Codex cloud task resolution remains dependent on recent-task discovery.
- Review semantics blur if reject and rework reuse the same downstream path.
- Metadata pressure arrives before approval semantics are fully locked.

### Next Cuts

1. Keep stale/dirty as explicit persisted inputs until a dedicated derivation
   packet is ready.
2. Preserve one backend gate engine while the shell continues to render its
   truth.
3. Keep the storage-backed packet lifecycle stable while work shifts to later
   operator-shell and metadata work.

## Milestone Status

### Milestone 1: One Packet Enters, One Clean Node Exits

Status: complete

Proof status:

- [x] raw source stored
- [x] parsed nodes stored
- [x] markers stored
- [x] packet stored
- [x] cloud result stored
- [x] validation stored
- [x] review decision stored
- [x] wiki node stored
- [x] replay bundle works
- [x] shell-driven review proof

### Milestone 2: Real Review Shell

Status: complete

Proof status:

- [x] queue visible in live shell
- [x] diff visible in live shell
- [x] evidence visible in live shell
- [x] blockers visible in live shell
- [x] gate state visible in live shell
- [x] review action outcome persisted from shell

### Milestone 3: Revisitability

Status: not started

Proof status:

- [ ] trail summary
- [x] packet lineage
- [x] source lineage
- [x] review lineage
- [ ] easy resume point

## Phase Status

### Phase 0: Lock Doctrine

Status: in progress

- [x] canonical roadmap
- [x] doctrine doc
- [x] decisions log
- [x] risk register
- [ ] one-sentence and one-diagram operator packet

### Phase 1: Cleanroom Pipeline Skeleton

Status: complete enough for milestone work

- [x] raw ingestion
- [x] parsed node model
- [x] preflight marking
- [x] reversible trim map
- [x] bounded packet builder
- [x] validation object
- [x] review packet object
- [x] approved packet object
- [x] wiki node promotion object

### Phase 2: Persistence and Replay

Status: complete enough for milestone work

- [x] SQLite persistence
- [x] audit event stream
- [x] replay bundle loader
- [x] review queue table
- [x] packet history reconstruction

### Phase 3: Real Cloud Worker Integration

Status: in progress

- [x] packet serializer
- [x] Codex cloud task submission adapter
- [x] task polling and status handling
- [x] output normalization into internal result schema
- [x] failure quarantine path
- [x] verified task-id resolution against a live environment

### Phase 4: Review Queue and Cleanroom Actions

Status: in progress

- [x] claim flow
- [x] approve action
- [x] reject action
- [x] rework action
- [x] gate predicate engine
- [x] review note policy enforcement
- [x] blocker handling policy

### Phase 5: Shell UI Skeleton

Status: in progress

- [x] panel doctrine reflected in fixture types
- [x] fake packet fixtures
- [x] shell layout runtime
- [ ] splitter behavior
- [x] review action bar

### Phases 6-8

Status: phase 6 in progress, later phases not started

- [x] shell review integration
- [ ] metadata and subject linking
- [ ] visitable history and learning trails
