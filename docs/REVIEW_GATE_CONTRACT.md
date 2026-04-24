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
- claiming a packet assigns the reviewer and records acknowledgement of the
  currently visible diff/evidence review surface for that reviewer
- approval writes an `ApprovedPacket`
- reject and rework persist distinct outcomes
- promotion requires an approved packet

## Current Status

The backend and live shell proof now agree on the core operator path:
claim -> inspect visible review surfaces -> approve. Remaining work is to lock
the full explicit gate predicate engine and mirror any stricter future policy
1:1 in the shell.
