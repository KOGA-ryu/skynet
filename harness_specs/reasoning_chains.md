# Reasoning Chains

Executable blocks are fenced YAML with `kind: reasoning_chain`.

```yaml
kind: reasoning_chain
id: chain.rag_answer
version: 1
description: >
  Plan, retrieve catalog spans, synthesize a deterministic evidence answer, verify groundedness, then persist.
steps:
  - id: s1_plan
    type: deterministic
    output_schema:
      type: object
      required_keys:
        - query_intent
        - search_queries
        - must_answer
        - uncertainty_notes
  - id: s2_retrieve
    type: tool
    tool_name: retriever.search
    outputs:
      retrieved_chunks: list
  - id: s3_synthesize
    type: deterministic
    output_schema:
      type: object
      required_keys:
        - answer_markdown
        - citations
  - id: s4_verify_groundedness
    type: tool
    tool_name: verifier.groundedness_check
  - id: s5_persist
    type: tool
    tool_name: store.persist_run
```
