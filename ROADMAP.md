# Cleanroom Project Roadmap

This project is a cleanroom knowledge-work system with two tightly coupled
parts:

1. A staged processing pipeline that acquires raw material, parses it, marks
   it, routes hard cases to cloud work, validates results, sends them through
   review, and promotes only stamped outputs into the clean wiki.
2. A dense operator shell UI that makes this pipeline inspectable, reviewable,
   and controllable without hiding truth behind modal clutter.

The system is not a generic notes app and not a general chatbot wrapper. It is
a review-gated knowledge factory with a terminal-grade shell.

For active phase and milestone tracking, use
[ROADMAP_TASKLIST.md](ROADMAP_TASKLIST.md). For doctrine and operating
contracts, use the docs packet under [docs/](docs).

## Core Mission

Build a system that can take messy source material such as conversations,
documents, notes, and research trails, then convert it into clean, reviewable,
traceable knowledge nodes.

The wiki only receives work that has passed through:

`raw -> parsed -> marked -> cloud/local work -> validation -> review -> stamp -> promotion`

## Success Criteria

A successful first version does all of the following:

- stores raw source immutably
- parses source into stable structural units
- lets a local model perform preflight marking and reversible trimming
- sends only bounded packets to Codex/cloud for hard summaries or edits
- validates all cloud output before human review
- presents packet, evidence, diff, blockers, and actions in a dense shell UI
- allows approve, reject, and rework with explicit gates
- promotes only approved work into the clean wiki with metadata and lineage
- supports replay of packet history later

## Not In Scope Early

- broad autonomous research behavior
- uncontrolled wiki writing
- automatic admission of model output into the wiki
- large-scale ontology invention by the model
- aesthetic-first UI work detached from workflow truth
- full multi-user collaboration as an early requirement

## Non-Negotiable Laws

### Data Law

Raw source is immutable. Nothing edits raw.

### Promotion Law

The clean wiki only receives reviewed and stamped work.

### Traceability Law

Every promoted node must link back to packet lineage, source lineage, and
review lineage.

### Review Law

Approve is stricter than reject or rework.

### UI Law

Critical state must remain visible. The shell must not hide workflow truth
behind modal-only flows.

### Context Law

The system must preserve enough lineage that a future session can answer: what
was this, where did it come from, what changed, and why was it accepted.

## Strategic Architecture

### Track A: Pipeline Core

Responsible for correctness, lineage, validation, and promotion.

### Track B: Operator Shell UI

Responsible for inspectability, review flow, status visibility, and
workstation-grade interaction.

### Track C: Knowledge Admission and Wiki Shaping

Responsible for metadata, subject links, relationship edges, and clean-node
promotion after approval.

The tracks evolve in parallel, but Track A is the truth source. Track B exposes
Track A. Track C consumes approved outputs from Track A.

## Phase Map

### Phase 0: Lock Doctrine

Goal: freeze the project identity, stage model, review doctrine, and shell
philosophy.

Outputs:

- project identity doc
- stage definitions
- packet lifecycle doc
- review outcome semantics
- shell doctrine
- anti-drift rules

Done when:

- there is one canonical description of the project
- approve/reject/rework semantics are explicit
- the team can explain the pipeline in one sentence and one diagram

### Phase 1: Cleanroom Pipeline Skeleton

Goal: build the deterministic backbone that stores, parses, and stages work.

Deliverables:

- raw document ingestion
- parsed document model with stable node ids
- local preflight marker pass
- reversible trim map generation
- bounded cloud packet builder
- validation report object
- review packet object
- approved packet object
- wiki node promotion object

Done when:

- a raw source can enter the system and reach a review-ready packet
- nothing mutates raw source
- packet lineage is preserved end to end

### Phase 2: Persistence and Replay

Goal: make every stage durable and replayable.

Deliverables:

- SQLite persistence for all major stages
- audit event stream
- replay bundle loader
- queue table for review work
- packet history inspection

Done when:

- any packet can be reconstructed from storage
- audit trail shows stage movement over time
- replay can explain how a wiki node came to exist

### Phase 3: Real Cloud Worker Integration

Goal: replace mock cloud behavior with real Codex-backed bounded task
execution.

Deliverables:

- packet serializer
- Codex cloud task submission adapter
- task polling and status handling
- output normalization into internal result schema
- failure quarantine path

Done when:

- escalated packets can be processed through Codex cloud
- cloud results return in strict structured form
- failures do not poison the review queue

### Phase 4: Review Queue and Cleanroom Actions

Goal: make human review real, gated, and explicit.

Deliverables:

- review queue claim flow
- approve action
- reject action
- rework action
- gate predicate engine
- review packet contract enforcement
- note policies
- blocker handling

Done when:

- reviewer can only stamp when required truth is visible
- reject and rework have distinct behavior
- review action state is machine-enforced rather than implied

### Phase 5: Shell UI Skeleton

Goal: build the terminal-grade shell around the real workflow.

Deliverables:

- left control rail
- center work surface
- right inspector rail
- bottom blotter strip
- review action bar
- splitter and resize behavior
- fake data fixtures matching real packet structures

Done when:

- the shell layout is stable
- panel roles are locked
- critical state is always visible
- the shell works with mock and real packet data

### Phase 6: Shell Review Integration

Goal: wire the review system into the UI without losing density or truth.

Deliverables:

- queue list view
- packet detail view
- validation issue view
- blocker view
- diff view
- evidence selection and inspection
- review actions with gate feedback

Done when:

- reviewer can claim, inspect, decide, and stamp from the shell
- approve path blocks correctly on stale, dirty, missing, or unreviewed state
- UI state matches backend gate logic exactly

### Phase 7: Metadata and Subject Linking

Goal: apply post-approval metadata and graph structure.

Deliverables:

- metadata engine
- subject tagging
- relationship mapping
- linked-node creation
- wiki indexing rules

Done when:

- approved nodes receive consistent metadata
- subject links are generated only after approval
- clean wiki navigation starts to become useful

### Phase 8: Visitable History and Learning Trails

Goal: turn packet and user traversal history into a revisit-friendly map.

Deliverables:

- trail capture
- session summaries
- thread replay
- branch history
- resume points
- cross-topic revisit tools

Done when:

- the system can explain what was explored
- users can return to a thread of learning or review work later
- history feels like a visitable map instead of dead logs

## Immediate Build Order

1. Freeze doctrine and roadmap.
2. Finish cleanroom state semantics.
3. Lock storage-backed packet lifecycle.
4. Swap mock cloud worker for real bounded Codex worker.
5. Build shell skeleton using fake packet fixtures.
6. Wire review queue and review actions into shell.
7. Add metadata and wiki promotion wiring.
8. Add trail and revisit systems.

## Milestones

### Milestone 1: One Packet Enters, One Clean Node Exits

A single source goes through the full pipeline and lands in the wiki after
review.

Required proof:

- raw source stored
- parsed nodes stored
- markers stored
- packet stored
- cloud result stored
- validation stored
- review decision stored
- wiki node stored
- replay bundle works

### Milestone 2: Real Review Shell

A reviewer can claim a packet and perform approval work entirely from the
shell.

Required proof:

- queue visible
- diff visible
- evidence visible
- blockers visible
- gate state visible
- review action outcome persisted

### Milestone 3: Revisitability

A user can return later and understand how a node or learning trail was formed.

Required proof:

- trail summary
- packet lineage
- source lineage
- review lineage
- easy resume point

## Anti-Drift Rules

1. Never discuss implementation without naming the current phase.
2. Never build new surfaces without mapping them to an existing workflow stage.
3. Never let UI work outrun packet truth.
4. Never let a model-generated artifact skip validation and review.
5. Never add new metadata rules before approval semantics are stable.
6. When confusion appears, return to: project identity, current phase, current
   milestone, blocker list.

## Ownership Model

### Human

Owns doctrine, approval, promotion policy, and final truth judgment.

### Dex

Owns implementation packets, delta specs, build sequencing, and code execution
within locked contracts.

### Local Model

Owns narrow preflight tasks, marker suggestions, and low-cost structured
assistance.

### Cloud Model / Codex

Owns bounded hard packets only, under explicit contracts.

## Review Outcome Semantics

### Approve

Packet is accepted, stamped, and eligible for promotion.

### Reject

Packet is closed as not acceptable in current form. It does not silently
re-enter the queue.

### Rework

Packet is not approved, but returns to a rework path with blockers or notes
attached. It may re-enter review later as a new reviewed attempt or updated
packet version.

## Minimum Operating Dashboard

At all times, the shell should make it cheap to answer:

- what stage is this item in
- what packet is active
- what is blocking approval
- what evidence is currently selected
- what changed in the diff
- what queue item is next
- whether the data is stale or dirty
- whether this item is promotion-eligible

## First Vertical Slice

One source document enters the cleanroom and exits as one approved wiki node
through the shell.

Must include:

- storage
- parsing
- marking
- packet building
- cloud processing
- validation
- review queue
- approve path
- promotion
- replay
- shell with fake data first, then real data

Must not include yet:

- large corpus ingestion
- broad ontology building
- advanced collaboration
- polished analytics dashboards
- fully general search

## Practical Operating Sentence

If the team gets lost, return to this sentence:

We are building a review-gated cleanroom that turns messy source material into
traceable knowledge nodes through a dense operator shell.
