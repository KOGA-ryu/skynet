# Cleanroom Doctrine

## Project Identity

`skynet` is a review-gated cleanroom that turns messy source material into
traceable knowledge nodes through a dense operator shell.

It has two tightly coupled parts:

1. A staged processing pipeline that owns correctness, lineage, validation, and
   promotion.
2. A shell UI that exposes packet truth, review state, evidence, blockers, and
   actions without hiding critical state.

It is not a generic notes app and not a general chatbot wrapper.

## Stage Model

The canonical pipeline is:

`raw -> parsed -> marked -> cloud/local work -> validation -> review -> stamp -> promotion`

The clean wiki receives only stamped work.

## Laws

- Raw source is immutable.
- Promotion is review-gated.
- Every promoted node must preserve source, packet, and review lineage.
- Approve is stricter than reject or rework.
- Critical state must stay visible in the shell.
- Future sessions must be able to answer: what was this, where did it come
  from, what changed, and why was it accepted.

## Hard vs Soft Mechanics

Hard mechanics are deterministic system duties:

- IDs and hashes
- packet lineage
- state transitions
- validation rules
- review gates
- audit trails
- promotion eligibility

Soft mechanics are model-assisted:

- preflight marking
- reversible trimming suggestions
- bounded summary output
- post-approval metadata suggestions

The hard side keeps the system honest. The soft side makes it useful.

## Anti-Drift Rules

1. Always name the current phase before implementation discussion.
2. Map every new surface to an existing workflow stage.
3. Do not let shell work outrun packet truth.
4. Do not let model output bypass validation and review.
5. Do not expand metadata policy before approval semantics are stable.
6. When confused, return to project identity, current phase, current milestone,
   and blocker list.
