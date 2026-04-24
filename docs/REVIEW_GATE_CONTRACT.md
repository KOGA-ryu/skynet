# Review Gate Contract

## Purpose

Review is the machine-enforced gate between validated output and promoted
knowledge.

## Review Outcomes

### Approve

- packet is accepted
- approval stamp is written
- packet becomes promotion-eligible

### Reject

- packet is closed as not acceptable in current form
- packet does not silently re-enter the queue

### Rework

- packet is not approved
- blockers or notes are attached
- follow-up work may create a later reviewable attempt

## Minimum Visible Truth Before Approve

Approve should only be possible when the reviewer can inspect:

- packet identity and stage
- validation state
- active blockers
- selected evidence
- relevant diff/change surface
- stale/dirty state
- promotion eligibility

## Backend Gate Expectations

- validation failure blocks queue admission
- queue state transitions are persisted, not implied
- claiming a packet is allowed only for `pending` packets with reviewer
  identity present
- claiming a packet assigns the reviewer and records acknowledgement of the
  currently visible diff/evidence review surface for that reviewer
- approval writes an `ApprovedPacket`
- reject and rework persist distinct outcomes
- promotion requires an approved packet

## Canonical Review Action Policy

### View-Time Policy

- `claim` is enabled only when the packet is `pending` and reviewer identity is
  present
- `approve` is enabled only when the packet is `in_review`, claimed by the
  session reviewer, and persisted gate state has `approve_enabled = true`
- `reject` and `rework` are enabled only when the packet is `in_review`,
  claimed by the session reviewer, and persisted gate state has
  `required_fields_loaded = true`
- stale, dirty, and blocker state can block `approve`, but do not block
  `reject` or `rework` in the current policy

### Submit-Time Policy

- `approve` note remains optional
- `reject` and `rework` require notes meeting the terminal minimum character
  policy
- mutation-time failures use stable reason kinds:
  - `reviewer_identity_missing`
  - `packet_missing`
  - `packet_not_pending`
  - `packet_not_in_review`
  - `claimed_by_other_reviewer`
  - `approve_gate_blocked`
  - `review_fields_not_loaded`
  - `terminal_note_too_short`

## Current Non-Goals

- no extra shell layout or RPC changes are required for the current gate engine

## Derived Stale / Dirty Truth

- `stale` means the current reviewable version no longer matches the persisted
  gate baseline version
- `dirty` means the packet became stale after it was claimed for review
- derived review version is fingerprinted from persisted packet, cloud result,
  validation, and lineage successor state
- reviewer acknowledgements, queue state, gate booleans, and timestamps do not
  participate in version hashing
- stale can appear on pending or in-review packets
- dirty remains false unless version drift happened after `claimed_at`
- stale and dirty still block `approve`, but do not block `reject` or `rework`

## Current Status

The backend and shell now share one explicit review gate engine, and stale /
dirty truth is derived from persisted reviewable state instead of manual flag
edits. Approve is the strict action, while reject and rework remain available
on claimed packets once required fields are loaded and note policy passes.
