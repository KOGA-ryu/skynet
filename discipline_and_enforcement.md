# Discipline and Enforcement Layer for a Personal Wiki

## Executive summary

This report proposes a research-grade, reproducible implementation plan for adding a “discipline layer” to a personal wiki so that agentic workflows behave more like a code intelligence system: resolve symbols first, read only what is necessary, and preserve provenance end-to-end. The core design pattern is to combine (a) a symbol layer that normalizes important knowledge artifacts (definitions, metrics, datasets, configs, claims, experiments) into stable identifiers, (b) an LSP-style query API over those symbols (Go-to-definition, Find-references, Hover, Workspace symbol search), (c) an enforcement/read-guard that blocks or budgets raw document reads unless symbol-first routes are exhausted, and (d) agent delegation hooks that pass only resolved handles plus tight read budgets to sub-agents.

The plan deliberately borrows protocol ideas from the Language Server Protocol, which is JSON-RPC-based and designed to support features like go-to-definition and find-references via a standardized client/server interface. citeturn0search0turn0search4turn0search1 A practical and well-supported technical backbone is JSON-RPC 2.0 for transport plus OpenRPC for machine-readable API contracts and auto-generated documentation/tests. citeturn14search19turn14search0

For storage and indexing, the recommended default is a single relational source of truth with first-class full-text and vector similarity, so that symbol metadata, provenance, and embeddings remain transactionally consistent. A common choice is PostgreSQL full-text (tsvector/tsquery) plus a vector extension such as pgvector, with JSONB for flexible metadata. citeturn2search1turn2search5turn13search8turn2search0 A local-only MVP can instead use SQLite FTS5 for full-text plus a local vector index (e.g., FAISS) if you want minimal ops, but the roadmap below is written to keep an easy migration path. citeturn2search2turn13search2turn3search0

Evaluation and reproducibility are treated as first-class deliverables, not afterthoughts. The plan includes: a query set and labeled ground truth for symbol resolution; retrieval metrics such as MRR and nDCG; RAG evaluation loops (e.g., RAGAS) for answer faithfulness and context quality; and provenance modeled explicitly using W3C PROV concepts (Entity/Activity/Agent) so outputs can be traced to sources and pipeline versions. citeturn9search8turn9search0turn0search7turn0search2turn0search6 Observability is specified using OpenTelemetry-style traces/metrics/logs to measure latency, token usage, failure modes, and guardrail overrides in a comparable way across iterations. citeturn1search7turn1search39

## Goals and success metrics

The discipline layer is successful when it systematically improves: retrieval accuracy, token efficiency, latency predictability, developer ergonomics, and provenance integrity.

**Retrieval accuracy (symbol resolution + evidence selection).** The system should reliably return the correct symbol(s) for a query, and then retrieve the minimal supporting evidence spans from source documents. Accuracy should be measured at two levels:

Symbol resolution quality:
- Top-k symbol hit rate for canonical lookups (e.g., “Sharpe ratio definition” resolves to the correct symbol ID at k=1 or k=3).
- Mean Reciprocal Rank (MRR) for symbol resolution (important when you care about “first correct match”). MRR is widely used in IR evaluation contexts to reflect how early the first relevant item appears. citeturn9search5turn9search8

Evidence retrieval quality:
- nDCG@k for evidence span ranking when relevance is graded (e.g., “exact formula + assumptions” is more relevant than “high-level mention”). The nDCG family was introduced to evaluate ranked retrieval with graded relevance. citeturn9search8turn9search0

**Token usage (discipline effectiveness).** The system should reduce tokens consumed by:
- Replacing “read many chunks then answer” with symbol-first resolution and selective span reads.
- Blocking broad/raw reads when adequate symbol metadata exists.
Token usage should be recorded per request and per stage (resolve → fetch → reason → draft), ideally as structured telemetry for easy time-series comparison (before/after). OpenTelemetry provides a standard model for correlating signals (traces, metrics, logs). citeturn1search7turn1search3

**Latency (predictability over raw speed).** The goal is to bound worst-case time by avoiding uncontrolled document scanning. You want p95 and p99 latency per query class (symbol lookup queries, synthesis queries, exploratory queries). A design that centralizes indexing and allows targeted retrieval is directly aligned with full-text/semantic search facilities (tsvector/tsquery; vector similarity). citeturn2search1turn2search5turn2search0

**Developer ergonomics (day-to-day usability).** Measure friction, not vibes:
- Median time to add a new symbol type (schema + extractor + indexes + tests).
- Median time to diagnose a bad answer (traceability from answer → evidence spans → source doc).
- Rate of “read-guard overrides” (how often you had to bypass enforcement to get acceptable outputs).
The LSP ecosystem exists largely because a standardized “intelligence server” reduces duplicated effort across clients and keeps tooling consistent. citeturn0search0turn0search4turn0search24

**Provenance quality (auditability and reproducibility).** Every computed artifact should carry:
- Source identifiers (document hash + canonical URL/path), extraction tool versions, extraction parameters, timestamps.
- A structured linkage of generated symbols and derived answers back to sources, using a provenance model. W3C PROV defines provenance in terms of entities, activities, and agents, designed to support trust and traceability. citeturn0search2turn0search6
This aligns with “track how results were produced” guidance from reproducible-computing best practice literature. citeturn1search0turn1search1

## Required inputs and baseline prerequisites

Because your current wiki formats and ingestion pipeline are unspecified, the plan assumes a discovery-first baseline: inventory what you already have, then normalize into a small number of canonical representations, keeping raw sources immutable.

The following table defines the minimum required inputs and how to collect them without upfront reformatting.

| Input category | Minimum requirement | Examples of what counts | Suggested normalization approach |
|---|---|---|---|
| Wiki “primary notes” | Stable note IDs and last-modified timestamps | Markdown notes, org-mode, notion exports, plaintext | Treat note as a Document with a stable `doc_id`; store raw bytes + content hash; parse into spans (headings/paragraphs) |
| External sources | Raw PDFs/HTML/web pages stored or fetchable | Research papers, blog posts, API docs, git repos, posts | Store raw PDF/HTML, plus a canonical source reference; parse to text + structure using robust tools |
| Extraction pipeline state | Ability to re-run extraction deterministically | Scripts, notebooks, CLI commands | Pin versions and record parameters for each pipeline step; persist outputs per run |
| Identity of “symbol glossary” | Any existing structured vocab, tags, or glossary pages | Glossary notes, YAML frontmatter, tags | Use them to seed canonical symbol IDs and aliases, then expand automatically |
| Evaluation seed set | A small set of “gold” queries and expected symbol targets | “find definition sharpe ratio”, “what is VWAP”, “backtest config v2” | Create a reviewable dataset (JSONL) with query → expected symbol(s) and evidence spans |

For PDF and HTML parsing, pick tools based on source type and desired structure:

- PDFs: Apache Tika is commonly used to extract text and metadata across many formats, including PDF, through a single interface. citeturn7search4turn7search0 For scholarly PDFs where structure matters (title/authors/sections/references), GROBID is explicitly designed to transform PDFs into structured TEI/XML for technical/scientific documents. citeturn7search1turn7search5turn7search25 For layout-sensitive chunking into elements (Title, NarrativeText, etc.) with different strategies, Unstructured provides partitioning functions and strategy controls for PDFs and other document types. citeturn7search2turn7search18turn7search26
- HTML: Beautiful Soup is a standard Python library for parsing HTML/XML, useful when you need to preserve structure and extract specific elements. citeturn7search3turn7search15

## Architecture design

### System overview

The architecture is built around four contracts: (1) immutable sources, (2) normalized spans, (3) first-class symbols and relationships, (4) a discipline controller that governs reading and delegation.

```mermaid
flowchart TB
  subgraph Sources
    A[PDF/HTML/MD Raw Sources]
    B[Wiki Notes]
  end

  subgraph Ingestion
    C[Canonical Store: raw bytes + hash]
    D[Parsers: Tika / GROBID / Unstructured / HTML parser]
    E[Span Builder: sections, paragraphs, tables, code blocks]
  end

  subgraph SymbolLayer
    F[Symbol Extractors\n(definitions, equations, configs, datasets, claims)]
    G[Symbol Registry\n(stable IDs, aliases, types, versioning)]
    H[Relationship Graph\n(depends_on, cites, derived_from, implements)]
    I[Provenance Ledger\n(PROV-style entities/activities/agents)]
  end

  subgraph Indexing
    J[Full-text index]
    K[Vector index]
    L[Metadata index]
  end

  subgraph QueryServer
    M[JSON-RPC / LSP-style API\nsymbol/*, span/*, graph/*, provenance/*]
    N[Read-Guard + Policy Engine]
    O[Delegation Hooks\n(sub-agent protocol)]
  end

  subgraph Clients
    P[CLI / TUI]
    Q[Web UI: Lineage Explorer]
    R[Agent Orchestrator]
  end

  A --> C
  B --> C
  C --> D --> E --> F --> G
  F --> H
  E --> J
  E --> K
  G --> L
  H --> L
  F --> I
  E --> I
  M --> N
  R --> M
  P --> M
  Q --> M
  N --> M
  O --> M
```

This “server” approach intentionally mirrors why language servers exist: compute intelligence once, serve it consistently to multiple clients over a standardized request/response protocol. The Language Server Protocol formalizes this over JSON-RPC, and its specification is designed around navigation primitives like go-to-definition and find-references. citeturn0search4turn0search1

### Symbol schema and examples

A “symbol” is any stable knowledge artifact you want to resolve by name and reuse across notes, answers, and agents (concept definitions, metrics, dataset specs, experiment configs, code modules, etc.). The schema must support:

- Stable identity: `symbol_id` that does not change when text changes.
- Human names + aliases: for flexible resolution.
- Typing: symbol kinds and domains.
- Evidence pointers: link to spans in canonical documents.
- Versioning: revisions and deprecations.
- Provenance: how the symbol was created/extracted and from what sources.

A minimal research-grade symbol schema (illustrative):

```json
{
  "symbol_id": "sym.finance.metric.sharpe_ratio",
  "kind": "definition",
  "name": "Sharpe ratio",
  "aliases": ["Sharpe", "Sharpe Ratio"],
  "domain": "finance",
  "signature": "S = (E[R_p] - R_f) / σ_p",
  "body_markdown": "Risk-adjusted return metric ... assumptions ...",
  "evidence": [
    {"doc_id": "doc:paper:sharpe1966", "span_id": "span:doc:paper:sharpe1966:p3:eq1"}
  ],
  "relationships": [
    {"type": "depends_on", "target": "sym.stats.metric.standard_deviation"},
    {"type": "related_to", "target": "sym.finance.metric.sortino_ratio"}
  ],
  "status": {"state": "active", "revision": "2026-04-15"},
  "provenance_id": "prov:activity:symbol_extraction:run_2026_04_15_001"
}
```

**Relationship graph vs “just links.”** Treat relationships as queryable first-class data, not just markdown links. This is the key to enabling “find references,” “what depends on this,” and lineage explanations.

If you want an explicit PROV-compatible mental model, W3C PROV-DM defines provenance around entities, activities, and agents, plus relations like “used” and “wasGeneratedBy.” citeturn0search2turn0search6 Your system can treat:
- Raw source docs and normalized spans as PROV entities,
- Parsing and symbol extraction as PROV activities,
- The extractor code + the model/tool version (and you) as agents.

### Storage and indexing choices

A practical baseline is one transactional database that stores symbols, relationships, spans, and provenance with consistent snapshots.

**Full-text search.** PostgreSQL provides full-text search primitives via `tsvector` (document representation) and `tsquery` (query representation), intended for searching natural-language documents efficiently. citeturn2search1turn2search13turn2search5 SQLite can also do full-text very effectively via FTS5 virtual tables. citeturn2search2

**Vector search.** If you want semantic search and hybrid retrieval, you can either:
- Use an integrated extension such as pgvector inside PostgreSQL to store and query embeddings alongside your relational data, benefiting from transactional consistency and joins. citeturn2search0turn2search16
- Use a dedicated vector database for larger scale or specialized ANN performance characteristics (trade-offs discussed in the technology table).

**Canonical metadata and flexible fields.** PostgreSQL JSONB supports nested structures and subscripting, which is convenient for evolving symbol metadata without constant migrations. citeturn13search8

### Query interface: LSP-style over JSON-RPC

The recommended protocol is JSON-RPC 2.0 for method calls and notifications (simple, transport-agnostic). citeturn14search19 LSP is itself JSON-RPC-based and adds conventions for capabilities negotiation and a method namespace style. citeturn0search4turn4search12

For a “knowledge server,” you can adopt an LSP-like structure:

- `initialize` / `shutdown` / `workspace/*` capabilities
- `symbol/*` navigation methods
- `span/*` targeted reading
- `graph/*` neighborhood and path queries
- `provenance/*` lineage queries
- `policy/*` read-guard explanations and overrides

To make the API reproducible and self-documenting, define it using OpenRPC, which is to JSON-RPC what OpenAPI is to HTTP APIs. citeturn14search0turn14search1 OpenRPC exists specifically to describe JSON-RPC 2.0 APIs for discovery, documentation, and tooling. citeturn14search0turn14search19

### Enforcement rules and read-guard flow

The discipline layer is not “nice-to-have.” It is a policy engine that limits unstructured reading and forces symbol-first navigation.

Core policies (expressed conceptually):
- Always attempt symbol resolution before raw reading when the query appears “symbolic” (definitions, configs, metrics, functions, dataset fields).
- Prefer structured evidence retrieval (span IDs) over whole-document reads.
- Enforce budgets: maximum number of spans, maximum characters per span, maximum documents.
- Require provenance: any answer that cites sources must return explicit span pointers.
- Support overrides, but log them as policy exceptions for later evaluation.

A concrete read-guard flow:

```mermaid
flowchart TD
  A[User/Agent Request] --> B[Classify intent\nsymbolic vs exploratory vs synthesis]
  B -->|symbolic| C[Try symbol resolution\nsymbol/find + alias expansion]
  C -->|found| D[Fetch symbol metadata\nno raw docs]
  D --> E[Need evidence?]
  E -->|yes| F[Fetch minimal spans\nspan/get by span_id]
  E -->|no| G[Answer from symbol layer\n(provenance attached)]
  F --> H[Compose answer\nwith span citations + provenance]
  C -->|not found| I[Fallback: structured search\n(full-text + vector)]
  I --> J[Propose candidate symbols or spans]
  J --> K[If still insufficient\nallow bounded raw read]
  K --> L[Record policy exception\n(reason + budget)]
  L --> H
```

This is intentionally analogous to the idea behind LSP navigation: you resolve symbols and jump directly to relevant locations rather than scanning files. citeturn0search0turn0search4

### Sub-agent delegation hooks

Delegation is most effective when the orchestrator sets constraints and provides unambiguous handles (symbol IDs, span IDs), rather than loosely describing what to read.

A delegation contract should include:
- A fixed “read budget” (max spans, max bytes, max documents, max tool calls).
- Allowed methods (sub-agent can call `symbol/*` and `span/*` but not `span/rawReadAll`).
- Required outputs: structured JSON with `used_symbol_ids`, `used_span_ids`, and a reproducible trace ID.
- Failure modes: sub-agent must return “insufficient evidence” rather than silently guessing.

If you are using an LLM platform that supports tool calling, structured outputs, and multi-step tool execution, you can directly map the discipline layer methods into tools and enforce schemas at the boundary. citeturn10search4turn10search3turn10search0

## Implementation roadmap and MVP

### Roadmap milestones

The roadmap is designed to get you a disciplined MVP quickly, then iterate toward research-grade evaluation and richer UX without rewrites.

| Phase | Outcome | Primary deliverables | Acceptance criteria |
|---|---|---|---|
| Foundation | Immutable sources + span layer | Canonical store (raw bytes + hashes), parsers for PDF/HTML/MD, span builder | Any source can be re-ingested deterministically; spans have stable IDs and offsets; provenance recorded for parse runs citeturn1search0turn0search6 |
| Symbol layer MVP | Stable symbol registry + minimal extractors | Symbol schema & storage, aliasing, manual + semi-auto symbol creation, relationship edges | `symbol/find` resolves common queries; `symbol/definition` returns consistent canonical output |
| Query server | JSON-RPC service + OpenRPC spec | JSON-RPC server, method set, OpenRPC contract, CI validation | Client can call `initialize`, `symbol/find`, `span/get`, `provenance/lineage`; contract validates via JSON Schema/OpenRPC citeturn14search0turn14search2turn14search19 |
| Read-guard | Enforcement + budgets | Intent classifier, policy rules, fallback logic, exception logging | “Symbolic” queries never trigger bulk reads; overrides are explicit and logged; token usage drops vs baseline |
| Evaluation harness | Reproducible measurement | Gold query set, metrics computation (MRR, nDCG), RAG evaluation loop (optional) | Regression tests catch retrieval quality drops; metrics reported per commit or per release citeturn9search8turn9search0turn0search7 |
| UX: Lineage Explorer | Visual debugging + exploration | Graph visualization UI backed by `graph/*` calls | User can click symbol → see dependencies and provenance; open evidence spans; export lineage snapshot |

### Minimal viable prototype scope

A credible MVP is not “a demo.” It is the smallest system that can enforce discipline and measure the benefits.

MVP components:

- **Canonical store**: content-addressed file store (raw bytes + SHA-256 hash), plus a document registry table.
- **Span index**: per document, store spans with stable IDs, type (heading/paragraph/table/code), offsets, and normalized text.
- **Symbol registry**: symbols + aliases + relationships + evidence pointers.
- **Indexes**: full-text index over span text; embeddings over span text (optional but valuable).
- **JSON-RPC knowledge server**: implementing the methods below.
- **Read-guard**: policy engine integrated into the orchestrator or server.
- **Basic CLI**: for debugging and scripted testing.

Suggested MVP API surface (illustrative):

```json
{
  "methods": [
    "initialize",
    "symbol/find",
    "symbol/get",
    "symbol/findDefinition",
    "symbol/findReferences",
    "span/get",
    "span/searchText",
    "span/searchVector",
    "graph/neighborhood",
    "provenance/lineage",
    "policy/explainDecision"
  ]
}
```

Sample query: `find_definition("sharpe_ratio")`

JSON-RPC request:

```json
{"jsonrpc":"2.0","id":1,"method":"symbol/findDefinition","params":{"query":"sharpe_ratio","k":3}}
```

Response:

```json
{
  "jsonrpc":"2.0",
  "id":1,
  "result":{
    "matches":[
      {
        "symbol_id":"sym.finance.metric.sharpe_ratio",
        "name":"Sharpe ratio",
        "confidence":0.93,
        "definition":"S = (E[R_p] - R_f) / σ_p",
        "evidence":[{"doc_id":"doc:paper:sharpe1966","span_id":"span:doc:paper:sharpe1966:p3:eq1"}],
        "provenance_id":"prov:activity:symbol_extraction:run_2026_04_15_001"
      }
    ]
  }
}
```

This style is intentionally aligned with JSON-RPC request/response structure and can be documented via OpenRPC. citeturn14search19turn14search0

### Estimated effort

Effort depends heavily on whether you already have a parsing pipeline and whether you want integrated vector search from day one.

A realistic solo-developer estimate for a research-grade MVP (not UI-polished):
- Foundation + symbol registry + JSON-RPC server: 2–4 weeks.
- Read-guard + evaluation harness + provenance ledger: 2–4 additional weeks.
- Lineage Explorer UI (usable, not fancy): 2–3 additional weeks.

If you choose PostgreSQL + pgvector early, you spend more time up front on schema/index design but less time later on migration. pgvector’s goal is to enable vector similarity search inside Postgres, keeping vectors “with the rest of your data,” which aligns with the discipline layer’s need for consistent joins across symbols/spans/provenance. citeturn2search0turn2search16

## Testing, evaluation, and reproducibility

### Testing strategy

**Unit tests** should validate:
- Schema validation (symbol JSON schema, provenance schema).
- Deterministic ID generation for documents/spans/symbols.
- Policy engine decisions (given a query class, ensure correct gating and budgets).

**Integration tests** should validate:
- End-to-end ingestion for each source type (PDF, HTML, markdown) using your chosen parsers; tools like Apache Tika and GROBID have clear boundaries and predictable outputs you can snapshot-test. citeturn7search4turn7search1turn7search5
- Index rebuild correctness (full-text index and vector index match expected results).

### Evaluation datasets and metrics

You need two evaluation datasets:

1) **Symbol resolution dataset** (query → expected symbol_id(s)).
This can be small initially (50–200) but must be curated.

2) **Evidence dataset** (query → expected evidence spans).
Even if you only label “best” evidence span(s), you can compute rank-based metrics and compare changes.

Recommended metrics:
- MRR for symbol resolution. citeturn9search5turn9search8
- nDCG@k for evidence ranking with graded relevance. citeturn9search0turn9search8
- Recall@k (especially if you prefer “don’t miss the right thing” over “rank perfectly”).

For embedding-model sanity checks and selection, you can optionally use established benchmarks (BEIR, MTEB) to understand general retrieval behavior and trade-offs between lexical baselines and dense retrieval. citeturn8search0turn8search1

For RAG-style answer evaluation (faithfulness, context relevance, etc.), RAGAS provides a reference-free evaluation framework oriented around retrieval augmented generation systems. citeturn0search7turn0search3

### Reproducibility controls

A research-grade system must allow you to re-run ingestion and indexing with the same results (or at least explainable differences) and to reproduce evaluations.

Minimum controls:
- Version control all pipeline code and schemas.
- Pin dependencies and record exact tool/model versions for each run, aligning with reproducible-computing guidance to track how each result was produced and avoid manual/untracked transformations. citeturn1search0turn1search1
- Use environment locking (Poetry lockfile or Conda environment export) and store those files with experiment runs. Poetry explicitly uses a lock file to prevent automatic drift in dependency versions. citeturn12search2turn12search6 Conda supports exporting and sharing environments, which is commonly used for reproducible environments. citeturn12search3
- For stronger reproducibility, containerize builds (Docker) and/or define deterministic builds (Nix). Docker documents approaches to reproducible builds (e.g., controlling timestamps via standardized variables). citeturn12search0turn12search12 Nix provides mechanisms like build checking to spot non-deterministic outputs. citeturn12search5

### Provenance implementation detail

Implement provenance as a ledger that is queryable and joinable to symbols/spans. Use W3C PROV concepts to avoid inventing a bespoke model that becomes a dead end. citeturn0search2turn0search6

A minimal internal mapping:
- `prov:entity`: raw_source_blob, parsed_document, span, symbol, answer_artifact
- `prov:activity`: parse_run, span_extraction_run, symbol_extraction_run, index_build_run, answer_generation_run
- `prov:agent`: user, extractor_version, model_version, server_version

Then expose `provenance/lineage` and `provenance/trace` in the query API so any UI or agent can ask “why do we think this is true?” and get a structured chain, not a story.

## Deployment, scaling, security, and UX

### Deployment and scaling considerations

A discipline layer tends to shift cost from “token spend + unpredictable reading” toward “index build + fast targeted queries.” Design for this intentionally:

- **Local-first deployment**: viable with SQLite FTS5 and WAL mode for concurrency improvements, plus local embeddings and a local vector index. SQLite documents WAL mode and how to enable it. citeturn13search2turn2search2
- **Single-node server deployment**: PostgreSQL is a strong default when you want unified storage, full-text search, and optional pgvector. PostgreSQL full-text facilities are built around `tsvector` and `tsquery`. citeturn2search1turn2search5 pgvector provides vector similarity search inside Postgres and is supported broadly across common Postgres distributions. citeturn2search0turn2search31
- **Scaling up**: when corpus size and concurrency rise, dedicated vector stores may offer operational and performance advantages, but you pay complexity tax. (See technology comparison table.)

Instrument the system using OpenTelemetry-style traces/metrics/logs so you can separate “index slow,” “policy too strict,” and “retrieval weak” failure modes. citeturn1search7turn1search39

### Security, privacy, and access control

Even a personal wiki can contain sensitive data (credentials in notes, private identifiers, research drafts). Security requirements are less about compliance theater and more about preventing “oops.”

Baseline controls:
- Authenticate and authorize access to the JSON-RPC server and any UI; OWASP’s API Security Top 10 highlights broken authorization and related API risks as core failure modes. citeturn6search8turn6search0
- Maintain audit logs and protect them; NIST SP 800-53 includes control families for audit and accountability, emphasizing that audit records should capture what happened, when, where, source, outcome, and identity. citeturn6search2turn6search6
- Handle PII carefully if it exists in notes or imported sources; NIST SP 800-122 provides practical guidance for identifying and protecting PII and tailoring safeguards. citeturn6search5turn6search1
- Ensure the system stores raw sources immutably and versioned; provenance is not only for reproducibility, it also reduces the risk of silent data tampering (you can re-hash and verify).

### UX and visualization: Lineage Explorer

The Lineage Explorer is not an aesthetic toy; it is your debugging console for knowledge. Its job is to show, for any symbol or answer:
- what it is,
- where it came from,
- what it depends on,
- who/what created it (pipeline + model version),
- what evidence spans support it.

A practical UI model is similar to common graph-view paradigms in note tools (nodes represent notes; edges represent links). For example, Obsidian’s Graph view visualizes relationships between notes as nodes and internal links as edges. citeturn11search3

A minimal Lineage Explorer mockup (screen-level wireframe):

```text
+-----------------------------------------------------------+
| Query:  [ sharpe_ratio ]   [Resolve]  [Explain Read-Guard] |
+-------------------+-----------------------+---------------+
| Symbol Card        | Evidence Viewer       | Lineage Graph |
|-------------------|-----------------------|---------------|
| ID: sym.finance... | doc:... span:...      |  (interactive)|
| Definition: ...    | -------------------   |  Sharpe ----->|
| Aliases: ...       | highlighted excerpt   |  StdDev ------|
| Status: active     | source metadata       |  RiskFreeRate |
| Provenance: run... |                       |               |
+-------------------+-----------------------+---------------+
| Exceptions / Policy Decisions (with trace id)             |
+-----------------------------------------------------------+
```

For implementation, web-friendly graph visualization libraries include Cytoscape.js (purpose-built for interactive graph visualization and analysis) and D3 force simulations. citeturn11search0turn11search1turn11search5 Cytoscape.js is explicitly positioned as an embeddable graph visualization component for web apps. citeturn11search0turn11search16

image_group{"layout":"carousel","aspect_ratio":"16:9","query":["Obsidian graph view screenshot","Cytoscape.js interactive graph example","Neo4j Bloom graph visualization screenshot","knowledge graph lineage explorer UI"],"num_per_query":1}

### Technology options comparison and recommendations

The table below compares technology choices for each major layer. “Recommended” does not mean “best in all situations”; it means “lowest regret for a disciplined, research-grade personal system.”

| Layer | Option | Pros | Cons | Recommendation |
|---|---|---|---|---|
| Relational store + metadata | PostgreSQL (full-text + JSONB) | Built-in full-text (`tsvector`/`tsquery`) and robust indexing; JSONB supports flexible metadata; strong operational maturity. citeturn2search1turn2search5turn13search8 | Higher ops than embedded DB; needs schema/index tuning. | Recommended default when you want one source of truth and clean joins across symbols/spans/provenance. |
| Embedded store | SQLite (FTS5 + WAL) | Very low ops; FTS5 provides full-text search via virtual tables; WAL mode supports concurrency improvements. citeturn2search2turn13search2 | Harder to scale concurrent writes; vector search not native (needs extensions or separate index); migrations to multi-user server later may be non-trivial. | Recommended for “local-first MVP” if you want fastest path to a working prototype. |
| Analytical store (optional) | DuckDB | Excellent for analytical queries and reading Parquet efficiently; embedded and easy to use. citeturn2search15turn13search3 | Not a primary transactional source of truth for an always-on server; you still need best-practice provenance and locking around writes. | Useful as an auxiliary analytics/evaluation engine (metrics over logs, corpora snapshots). |
| Vector search integrated | pgvector (PostgreSQL extension) | Store embeddings inside Postgres; benefit from ACID + joins; supports ANN indexing. citeturn2search0turn2search16 | Not always as feature-rich as dedicated vector DBs; performance tuning still required. | Recommended if using PostgreSQL; reduces system complexity and improves reproducibility. |
| Vector DB (dedicated) | entity["company","Qdrant","vector db company"] | Designed for vector similarity + payload filtering; supports multiple deployment modes; explicit guidance around filtering and HNSW indexing. citeturn3search11turn3search15 | Additional service to operate; data consistency with relational store must be engineered. | Strong option if you outgrow integrated vector search and want a focused retrieval system. |
| Vector DB (dedicated) | entity["company","Weaviate","vector db company"] | Vector database designed to store objects + vectors; supports semantic search patterns and hybrid retrieval. citeturn3search2turn3search8 | Another system to run; object model may overlap with your own symbol schema (duplication risk). | Consider if you want an “object + vector” store and accept the overlap with symbol registry. |
| Vector DB (dedicated) | entity["company","Zilliz","milvus sponsor company"] / Milvus | Scalable vector DB ecosystem; focuses on ANN performance and deployment. citeturn3search1turn3search18 | Operational overhead; you still need a relational/provenance store. | Good at scale; less compelling for a personal wiki unless corpus is very large. |
| Local vector index | FAISS | Efficient similarity search and clustering; classic baseline for local ANN; backed by published research on billion-scale similarity search. citeturn3search0turn3search21 | Not a database; you must manage persistence, metadata joins, and updates. | Strong MVP choice when paired with SQLite or file-based metadata, but expect to build “DB glue.” |
| Query protocol | JSON-RPC 2.0 | Simple RPC protocol; transport-agnostic; compatible with LSP-style patterns. citeturn14search19turn0search4 | Not as common as REST/HTTP in typical tooling; needs method discovery/docs. | Recommended for “LSP-like” feel and direct parity with language server patterns. |
| Query contract | OpenRPC | Standard interface description for JSON-RPC APIs; enables discovery, docs, and tooling without reading source. citeturn14search0 | Smaller ecosystem than OpenAPI; some tooling less mature. | Recommended if you use JSON-RPC; it prevents your API from becoming tribal lore. |
| LSP implementation libs | `pygls` (Python) | Generic LSP implementation designed to build language servers quickly in Python. citeturn4search1 | LSP includes lots of editor-specific conventions you may not need; you may still implement a custom subset. | Recommended if you want to reuse LSP transport + patterns directly. |
| LSP implementation libs | `vscode-languageserver-node` JSON-RPC | Implements the JSON-RPC messaging layer used in VS Code language servers; usable standalone. citeturn4search0turn4search4 | Node/TypeScript stack; may not match your backend language choice. | Recommended if the server is in Node, or if you need compatibility with VS Code ecosystems. |
| Embeddings | entity["company","OpenAI","ai research company"] embeddings API | Official embeddings guidance; models such as `text-embedding-3-small` and `text-embedding-3-large` are designed for search and relatedness tasks. citeturn5search0turn5search24turn5search8 | External dependency and cost; deterministic reproducibility requires careful version logging. | Strong default for quality; log model name + version/date in provenance. |
| Embeddings | entity["company","Cohere","ai company"] Embed models | Embed models with documented dimensions and multilingual variants; explicit embedding model catalog. citeturn5search1turn5search5 | External dependency and cost; model lineup evolves. | Strong alternative; choose if it aligns better with your stack or deployment constraints. |
| Embeddings | entity["company","Voyage AI","embedding model company"] embeddings | Documentation for instruction-tuned embedding models; explicit guidance on query/document input types. citeturn5search2turn5search6 | External dependency and cost; model range evolves rapidly. | Consider when retrieval quality is the main priority and you can tolerate vendor dependency. |
| Embeddings | Sentence-Transformers | Open ecosystem for embedding and reranking models; supports computing dense embeddings and rerankers (cross-encoders). citeturn5search3turn5search23 | You own hosting and performance; model selection and finetuning are your responsibility. | Recommended for fully local/offline workflows or when you want to finetune to your domain. |

### Key risks and mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Symbol schema “design freeze” too early | You will discover new symbol types as you use it; rigid schema creates friction | Use stable core fields + flexible metadata (e.g., JSONB); version schemas and add type-specific validation citeturn13search8 |
| Over-enforcement reduces usefulness | If read-guard is too strict, answers become brittle | Implement explicit override mechanisms with logging; tune intent classification; treat overrides as evaluation signals |
| Provenance becomes “optional” | Without forcing provenance, you lose auditability and reproducibility | Make provenance required for symbol creation and answer artifacts; model it explicitly using PROV concepts citeturn0search2turn0search6 |
| PDF parsing quality variance | PDFs differ wildly; naive extraction breaks evidence spans and symbols | Use fit-for-purpose tools: Tika for general extraction, GROBID for scientific structure, Unstructured for layout-aware partitioning citeturn7search4turn7search1turn7search2 |
| Evaluation dataset drift | If your “gold” queries don’t evolve, you optimize for yesterday’s use | Add new queries continuously; run regressions in CI; track MRR/nDCG trends over time citeturn9search8turn9search0 |
| Multi-store complexity | Separate relational + vector + graph stores can desync | Prefer integrated designs early (Postgres + full-text + pgvector); only split stores when scaling forces it citeturn2search0turn2search1 |
| Tool/model updates break reproducibility | Embedding models and parsers evolve; outputs change | Log versions and parameters; pin environments (lockfiles/containers); rerun evaluations when upgrading citeturn1search0turn12search2turn12search0 |

