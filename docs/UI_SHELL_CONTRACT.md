# UI Shell Contract

## Purpose

The shell is a dense operator surface for review-gated knowledge work. It is
not a decorative wrapper around hidden state.

## Panel Doctrine

### Left Control Rail

Queue and navigation control.

### Center Work Surface

Active packet and primary review work.

### Right Inspector Rail

Evidence, validation, blockers, and focused inspection.

### Bottom Blotter Strip

Events, state changes, warnings, and operator status.

## Visibility Rules

- critical state must remain visible
- blockers must not be modal-only
- evidence selection must be inspectable
- queue state must remain cheap to scan
- stale and dirty state must be visible before approve

## Shell Questions It Must Answer Cheaply

- what stage is this item in
- what packet is active
- what blocks approval
- what evidence is selected
- what changed
- what queue item is next
- whether the item is stale or dirty
- whether the item is promotion-eligible

## Current Repo Status

The repo now contains a live Rust shell runtime under `src/shell/` and a Qt/QML
host under `qt/`. The current review cut keeps Rust as the source of truth for
claim/review availability, uses explicit packet claim with session reviewer
identity from `SKYNET_REVIEWER`, and preserves a session-scoped action receipt
even after the shell advances to the next packet. A live storage-backed shell
proof has now exercised the real claim -> approve path and confirmed that the
shell advances to the next pending packet while keeping the last action receipt
visible.
