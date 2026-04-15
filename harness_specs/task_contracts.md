# Task Contracts

Executable blocks are fenced YAML with `kind: task_contract`.

```yaml
kind: task_contract
id: wiki.answer_with_citations
version: 1
description: >
  Answer a user question using retrieved wiki context and citations.
inputs:
  user_query:
    type: string
    required: true
outputs:
  answer_markdown:
    type: string
    required: true
  citations:
    type: array
    required: true
budgets:
  max_model_calls: 0
  max_child_tasks: 0
  max_retrieval_k: 8
  max_wall_clock_seconds: 45
tools_allowed:
  - retriever.search
  - verifier.groundedness_check
  - store.persist_run
retrieval_profile:
  id: catalog.fts_spans
  min_score_threshold: 0.0
chain:
  id: chain.rag_answer
verification_profile:
  require_schema_valid: true
  require_min_citations: 2
  require_groundedness: true
persistence:
  store_tool_io: true
  store_retrieval_candidates: true
  retention_days_full_trace: 30
completion_condition:
  all_of:
    - output.schema_valid == true
    - output.citations.count >= 2
    - verifier.groundedness.pass == true
```
