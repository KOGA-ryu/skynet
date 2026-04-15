# Failure Taxonomy

Executable blocks are fenced YAML with `kind: failure_taxonomy`.

```yaml
kind: failure_taxonomy
id: failures.core
version: 1
description: Base failure taxonomy for wiki harness.
enums:
  severity:
    - low
    - medium
    - high
    - critical
  action:
    - retry
    - expand_retrieval
    - ask_clarifying
    - abort
failures:
  - code: RETRIEVAL_EMPTY
    severity: high
    description: Retriever returned zero chunks.
    respond:
      - action: expand_retrieval
      - action: retry
  - code: OUTPUT_SCHEMA_INVALID
    severity: critical
    description: Output failed required-field validation.
    respond:
      - action: retry
      - action: abort
  - code: LLM_PROVIDER_CONFIG_MISSING
    severity: high
    description: Required LLM provider configuration is missing.
    respond:
      - action: abort
  - code: LLM_SYNTHESIS_ERROR
    severity: high
    description: Structured synthesis provider failed.
    respond:
      - action: retry
      - action: abort
  - code: GROUNDEDNESS_FAIL
    severity: high
    description: Answer citations are not supported by retrieved context.
    respond:
      - action: expand_retrieval
      - action: ask_clarifying
  - code: TOOL_CALL_ERROR
    severity: high
    description: Tool execution failed.
    respond:
      - action: retry
      - action: abort
```
