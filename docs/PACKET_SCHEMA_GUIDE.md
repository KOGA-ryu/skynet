# Packet Schema Guide

## Purpose

Packets are the bounded handoff objects between deterministic cleanroom stages
and model-assisted work. A packet must be small enough for bounded cloud work
and rich enough to preserve reversible lineage.

## Core Objects

### `RawDocument`

Immutable acquired source:

- `document_id`
- source metadata
- raw text
- SHA-256 hash

### `ParsedDocument`

Deterministic parse result:

- original `RawDocument`
- stable `ParsedNode`s with `NodeId`
- node byte spans
- parent-child relationships where needed

### `Marker`

Local preflight overlay only. Markers do not edit source text.

Current marker families:

- preserve verbatim
- collapse boilerplate
- needs context
- likely noise
- core content
- duplicate of
- escalate to cloud
- unsafe to edit

### `CloudTaskPacket`

Bounded work order for cloud execution:

- `packet_id`
- `document_id`
- `work_units`
- `style_contract`
- `completion_contract`

### `WorkUnit`

The smallest cloud-facing reviewable unit:

- target node id
- visible node ids
- context node ids
- reversible `trim_map`
- rendered trimmed view
- instructions

### `CloudSummaryResult`

Strict normalized result:

- `packet_id`
- `model_name`
- one fragment per work unit

### `ValidationReport`

Validation decides review eligibility, not model confidence.

### `ApprovedPacket`

Review-stamped packet. Promotion eligibility starts here.

### `WikiNode`

Promoted clean artifact with:

- approved packet linkage
- source document linkage
- source node ids
- metadata

## Contract Rules

- Raw text is never mutated.
- Packets must preserve reversible trim information.
- Cloud output must resolve back to node IDs and evidence spans.
- Validation failure blocks queue admission.
- Promotion requires approval.
