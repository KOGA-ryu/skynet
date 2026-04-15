# Research-grade discipline and enforcement layer for a personal wiki

## Executive summary

A “discipline/enforcement layer” for a personal wiki is best treated as a language-intelligence system for knowledge: a symbol layer that gives stable identities to the important things you care about (definitions, metrics, dataset schemas, experiments, configs, claims), an LSP-style navigation/query API (go-to-definition, find-references, hover, workspace search), a read-guard that enforces symbol-first retrieval and bounded reading, and delegation hooks that let sub-agents operate on resolved identifiers plus explicit budgets rather than roaming the vault. The design goal is not merely better retrieval; it is **behavioral control**: constrain information access patterns so answers become reproducible, auditable, and cheaper.

The most robust protocol foundation is **JSON-RPC 2.0**—a lightweight, transport-agnostic RPC spec used by the **Language Server Protocol** to exchange request/response/notification messages. citeturn0search1turn0search0 A “knowledge server” can adopt the same mechanics without pretending your wiki is code. To make the interface discoverable and testable, define the API contract in **OpenRPC**, which standardizes interface descriptions for JSON-RPC 2.0 APIs. citeturn0search6turn0search10

For provenance, use **W3C PROV-DM** concepts (Entity–Activity–Agent) as the backbone so every symbol, span, and generated answer traces to immutable sources and a deterministic pipeline run. citeturn0search3turn0search11 For parsing heterogeneous sources, combine general extraction with structure-aware tooling: **Apache Tika** for broad file-type coverage and metadata/text extraction, **GROBID** for PDF-to-structured TEI/XML (especially scientific/technical PDFs), and optionally **Unstructured** for partitioning/chunking strategies tuned to PDF/HTML variability. citeturn2search2turn2search3turn3search0

Two baseline stacks cover most personal-wiki realities:

A local-first MVP stack: SQLite + FTS5 for lexical navigation (fast and low-ops) plus optional FAISS for local vector search; keep a stable JSON-RPC interface so you can upgrade storage later. citeturn1search3turn9search17turn9search1

A research-grade “single source of truth” stack: PostgreSQL full-text (tsvector/tsquery) + JSONB metadata + pgvector for embeddings, exposed via JSON-RPC/OpenRPC. citeturn1search0turn1search1turn1search2 This reduces synchronization failure modes because symbols, spans, embeddings, and provenance can live in one transactional system.

Success should be measured explicitly: symbol-resolution quality (MRR), evidence retrieval ranking (nDCG, Recall@k), token usage reduction from symbol-first discipline using API usage counters, latency percentiles, developer ergonomics (time-to-add a symbol kind; time-to-debug a bad answer), and provenance completeness (fraction of outputs with verifiable source span chains). MRR is standard in QA-style ranking evaluation (e.g., TREC QA tracks). citeturn5search0turn5search1 nDCG is a canonical graded-relevance ranking metric in IR evaluation. citeturn4search4turn4search0 Token accounting can be measured directly from OpenAI API response usage fields when models are used, and complemented with end-to-end tracing/metrics/logs via OpenTelemetry. citeturn8search5turn2search5

## Assumptions and required inputs

The inputs you did not specify are the highest risk to schedule and correctness, so this plan makes assumptions explicitly and structures the work so assumptions can be replaced without rewrites.

### Assumptions where inputs are unspecified

| Area | Assumption used for this plan | What changes if false |
|---|---|---|
| Canonical wiki format | A Markdown vault (Obsidian-compatible) with internal links and headings as meaningful structure | Parsers and span IDs change if the “source of truth” is a database (Notion) or a different markup; the symbol layer still applies but ingestion must be adapted |
| Source corpus | Mix of Markdown notes + PDFs + some HTML pages (docs/blogs) | If you have mostly code/repos or mostly scanned PDFs, you need different extractors and OCR strategy (higher effort and lower determinism) |
| Ingestion posture | Local tooling runs on a developer machine and reads the vault (possibly over SMB) to produce derived catalogs | If ingestion must run on the NAS or in the cloud, you must formalize deployment, auth, and scheduling earlier |
| Privacy constraints | Personal / private, but may include sensitive notes (keys, personal identifiers) | You must add stronger redaction, access control, and audit logging early; OWASP/NIST-guided controls become baseline, not “later” citeturn6search0turn6search6 |
| Budget/provider | No fixed cloud provider; assume local-first is acceptable at least for MVP | You should pick managed Postgres/vector services sooner if uptime and multi-device access are priorities |

### Required inputs to proceed in a reproducible way

You can treat “required inputs” as a checklist of things that must exist (or be generated) before discipline can be enforced.

| Input | Minimal requirement | Why it matters for enforcement |
|---|---|---|
| Immutable source capture | Each Markdown/PDF/HTML source is stored or referenced with a stable ID and content hash | The read-guard can only cite and audit what it can identify unambiguously; provenance uses these IDs as PROV Entities citeturn0search3turn11search0 |
| Structural text model | Every document becomes a set of spans (headings/paragraphs/tables/code blocks) with stable span IDs | Enforcement operates on spans, not whole files; “bounded reads” mean bounded spans |
| Symbol seed set | A starter glossary of your most important concepts/metrics/configs (even if manually curated) | Symbol-first only works if some symbols exist; the system should measure “missing symbol” rates over time |
| Source extraction toolchain | Deterministic PDF/HTML extraction configuration | Different PDF extractors yield different text; pinning the toolchain is required for reproducibility citeturn2search2turn2search3turn11search0 |
| Evaluation seed queries | A small query set with expected symbol(s) and expected evidence spans | Without a gold set, enforcement can “feel” better while getting worse; IR evaluation is the guardrail citeturn4search4turn5search0 |

### Parsing and ingestion tool choices for PDFs and HTML

A research-grade approach is to support multiple extractors behind one interface and record which extractor produced which spans, rather than betting everything on one parser.

**entity["organization","Apache Tika","content analysis toolkit"]** provides broad-format metadata + text extraction through a single toolkit interface and is commonly used for indexing pipelines. citeturn2search2turn2search14

**entity["organization","GROBID","pdf to tei/xml extraction"]** is purpose-built for extracting structured TEI/XML from PDFs with a focus on technical/scientific publications, making it suitable when you care about citations, sections, and paper-like structure. citeturn2search3turn2search7turn2search23

**entity["organization","Unstructured","document partitioning library"]** offers partitioning functions and strategy options for PDFs/HTML that can adapt to document variability and supports automatic filetype routing via libmagic (when available). citeturn3search0turn3search4turn3search12

For HTML structure extraction, a deterministic DOM parser (e.g., BeautifulSoup) is typically sufficient; the plan treats this as an implementation detail while emphasizing that the produced spans and provenance must be stable.

## Architecture and interfaces

### High-level architecture

The core idea is to turn your wiki + sources into a “workspace” that can answer navigation queries without dumping whole documents into an agent context. This mirrors the design motivation behind language servers: standardized navigation queries over a workspace via JSON-RPC. citeturn0search0turn0search1

```mermaid
flowchart TB
  subgraph WorkspaceSources
    A[Markdown vault notes]
    B[PDF corpus]
    C[HTML snapshots]
  end

  subgraph CanonicalIngestion
    D[Immutable blob store\n(raw bytes + hash + metadata)]
    E[Extraction layer\nTika/GROBID/HTML parser]
    F[Span builder\nsections, paragraphs, tables, code blocks]
  end

  subgraph KnowledgeModel
    G[Symbol registry\nIDs, kinds, aliases, versions]
    H[Evidence index\nspan text + offsets]
    I[Relationship graph\nreferences, depends_on, derived_from, cites]
    J[Provenance ledger\nPROV Entities/Activities/Agents]
  end

  subgraph Indexes
    K[Lexical search\nFTS (Postgres or SQLite)]
    L[Vector search\npgvector/FAISS]
    M[Metadata indexes\nJSONB fields, path, tags]
  end

  subgraph QueryServer
    N[JSON-RPC server\nLSP-style methods]
    O[Read-guard / policy engine]
    P[Delegation hooks\nbudgets + allowed methods]
  end

  subgraph Clients
    Q[CLI]
    R[Lineage Explorer UI]
    S[Agent orchestrator]
  end

  A-->D
  B-->D
  C-->D
  D-->E-->F
  F-->H
  F-->K
  F-->L
  F-->J
  G-->I
  G-->J
  I-->M
  H-->M
  K-->N
  L-->N
  M-->N
  N<-->O
  N<-->P
  Q<-->N
  R<-->N
  S<-->N
```

Key architectural constraints:

The blob store is immutable and content-addressed (by hash), so you can always reproduce “what was seen” when a symbol or answer was created; this aligns with reproducible computational practice recommendations to avoid manual, untracked transformations and to preserve raw data artifacts. citeturn11search0

The span layer is the unit of bounded reading. A read-guard can cap the number of spans and characters, while still allowing precise evidence retrieval.

The symbol registry is not “an embedding index.” It is a typed semantic inventory with stable identifiers, aliases, and relationships, designed to support “go-to-definition” style queries analogous to LSP navigation. citeturn0search0turn0search8

### Data model primitives

A useful mental model is: Documents contain Spans; Symbols point to evidence spans; Relationships connect symbols; Provenance explains how each artifact was produced using PROV’s Entity/Activity/Agent relations. citeturn0search3turn0search14

A minimal normalized schema (conceptual):

Document
- doc_id (stable)
- source_uri or path
- content_hash
- extractor (tika/grobid/html)
- extracted_at, extractor_version
- metadata (JSON)

Span
- span_id (stable)
- doc_id (FK)
- kind (heading/paragraph/table/code)
- start_offset/end_offset (or structural locator)
- normalized_text
- embedding (optional)

Symbol
- symbol_id (stable, namespaced)
- kind (definition/metric/dataset/config/claim/experiment/code_symbol)
- name (human)
- aliases (array)
- signature (optional, e.g., formula)
- description/body (markdown)
- evidence_span_ids (array)
- status/version

Relationship
- from_symbol_id
- rel_type (references/depends_on/cites/derived_from/implements)
- to_symbol_id
- evidence_span_id (optional, for justification)

Provenance (PROV-inspired tables)
- entity_id, activity_id, agent_id
- wasGeneratedBy, used, wasAssociatedWith, etc. citeturn0search3

### Storage and indexing choices

For lexical search, the official primitives are mature in both PostgreSQL and SQLite:

PostgreSQL’s full-text search uses `tsvector` to represent documents and `tsquery` for queries and includes ranking and control functions for relevance. citeturn1search0turn1search4turn1search8

SQLite FTS5 is a virtual table module that provides full-text search functionality and is a strong choice for a low-ops local catalog. citeturn1search3turn1search15

For flexible symbol metadata and provenance attachments, PostgreSQL’s JSONB supports nested values and subscripting operations, which is practical for evolving symbol kinds without schema thrash. citeturn1search1turn1search5

For embeddings, pgvector is designed to store vectors “with the rest of your data” and support exact and approximate nearest-neighbor search inside Postgres. citeturn1search2turn1search18 Alternatively, FAISS is a widely used library for similarity search and clustering of dense vectors with published design principles and strong local/offline utility. citeturn9search17turn9search9

### LSP-style query interface over JSON-RPC

The LSP defines JSON-RPC request/response/notification messages between a tool (client) and a server (workspace intelligence). citeturn0search0turn9search8 You can reuse the pattern without inheriting every editor-specific concept.

JSON-RPC 2.0 defines the core message structures and is transport-agnostic, which fits local sockets, HTTP, or stdio. citeturn0search1 To make method contracts discoverable and testable, OpenRPC provides a standard interface description for JSON-RPC APIs and is explicitly JSON Schema-powered. citeturn0search6turn0search9

A pragmatic method mapping (conceptual):

| “Code intelligence” concept | Knowledge-server equivalent | Why it matters |
|---|---|---|
| Go-to-definition | `symbol/findDefinition` | “What is X?” resolves to a canonical symbol rather than scanning text |
| Find references | `symbol/findReferences` | “Where is X used?” supports impact analysis and lineage |
| Hover | `symbol/hover` | Quick summary + provenance without reading full docs |
| Workspace symbols | `symbol/search` | Fuzzy lookup across the vault |
| Document symbols | `span/listHeadings` or `span/listSymbols` | Structured navigation inside one note/document |

If you want offline/portable “index dumps,” LSIF (Language Server Index Format) is a directly relevant inspiration: it is a standard format for persisting workspace intelligence so LSP-style requests can be answered without running the server continuously. citeturn9search3turn9search7

## Enforcement and delegation protocols

### Enforcement rules and read-guard philosophy

The enforcement layer is a policy engine sitting between “an intent” and “content access.” Its job is to keep retrieval narrow, auditable, and cheap, not to maximize recall by brute force.

Core rule set (expressed as enforceable constraints, not hopes):

Symbol-first: if the query is likely resolvable as a symbol (definition, metric, config, dataset field, named concept), the policy must attempt symbol resolution before any bulk reading.

Span-bounded evidence: if evidence is required, fetch only the minimal number of spans (and characters) needed; avoid full document reads unless explicitly authorized.

Budgeted fallback: allow broader retrieval only after structured options fail; record the exception as a first-class event (for later tuning).

Provenance required: any answer that asserts facts must include span IDs and document IDs so the claim is auditable through the provenance ledger defined with PROV concepts. citeturn0search3turn0search11

### Read-guard flow

```mermaid
flowchart TD
  A[Request:\nuser or agent] --> B[Intent classification\nsymbolic vs exploratory vs synthesis]
  B --> C[Policy: Symbol-first path]
  C --> D[symbol/search + alias resolution]
  D -->|Resolved| E[symbol/get\n(no raw docs)]
  E --> F{Need evidence?}
  F -->|No| G[Return canonical symbol\n+ provenance summary]
  F -->|Yes| H[span/get (bounded)\n+ provenance pointers]
  H --> I[Compose answer\ncite span_ids]
  D -->|Not resolved| J[Fallback retrieval\nFTS + vector]
  J --> K[Candidate spans\nranked]
  K --> L{Budget allows more?}
  L -->|No| M[Return insufficient-evidence\n+ suggested symbol creation]
  L -->|Yes| N[Selective expansions\nbounded span reads]
  N --> I
```

This flow is aligned with why LSP-style navigation is powerful: resolve and jump, don’t scan. citeturn0search8turn0search0

### Sub-agent delegation hooks

Delegation should pass **handles, not haystacks**.

A sub-agent protocol should include:

A strict read budget (max spans, max characters per span, max tool calls) and an allowlist of methods (typically symbol and span reads, not arbitrary filesystem reads).

A required structured output with `used_symbol_ids`, `used_span_ids`, and `policy_exceptions` so results can be audited and scored.

A deterministic “task capsule” payload (inputs + constraints) so it can be replayed in evaluation.

If you are using OpenAI models as orchestrators/sub-agents, tool calling provides a native mechanism to connect the model to your JSON-RPC methods (as tools) and JSON Schema can constrain function parameters, while Structured Outputs can constrain the model’s output format to a supplied schema. citeturn3search2turn8search1 This is a practical way to enforce “no wandering” at the interface boundary, not only in your internal code.

## Implementation roadmap and MVP

### Roadmap milestones

The roadmap below is designed so you can ship a discipline layer in increasing capability without changing the external API contract. The table is intentionally outcome-based so you can measure success and stop early if the value curve flattens.

| Milestone | Deliverables | Exit criteria using success metrics |
|---|---|---|
| Canonical capture and spans | Immutable source registry, extraction config, span builder, provenance for extraction runs | Re-ingestion yields identical doc_id/span_id sets for unchanged inputs; extraction tool versions recorded; span coverage is stable across runs citeturn11search0turn0search3 |
| Symbol registry core | Symbol schema, aliases, typed symbol kinds, evidence linking to spans | `symbol/findDefinition` resolves top concepts at k=1 with measurable MRR; missing-symbol rate tracked citeturn5search0turn5search6 |
| JSON-RPC service + OpenRPC contract | Server implementation, OpenRPC spec, contract test harness | OpenRPC spec fully describes methods and examples; client can discover and test API; JSON-RPC compliance verified citeturn0search1turn0search6 |
| Lexical navigation quality | FTS index, ranking, snippet/highlight support | Evidence retrieval nDCG and Recall@k reach acceptable baselines on gold queries; results stable over time citeturn4search4turn1search0 |
| Read-guard enforcement | Policy engine, budgets, exception logging, explainability (`policy/explainDecision`) | Token usage and span reads drop materially for symbolic queries; exceptions are rare and explainable in logs/traces citeturn8search5turn2search5 |
| Delegation protocol | Sub-agent capsule schema, allowed-method enforcement, replay logging | Sub-agent outputs are reproducible and auditable; delegation does not increase uncontrolled reading; policy exceptions attributable |
| Lineage Explorer UI | Graph-based browsing, symbol/evidence/provenance panes | You can click any claim/symbol and see evidence spans + provenance chain; debugging time per failure decreases citeturn6search3turn7search0 |
| Optional offline index dumps | LSIF-inspired export/import of symbol & span intelligence | A snapshot can answer symbol navigation without rerunning extractors; snapshot invalidation rules defined citeturn9search3turn9search7 |

### Minimal viable prototype definition

A research-grade MVP is the smallest system that can enforce discipline and measure the improvement.

MVP components:

A local catalog database (SQLite FTS5 or PostgreSQL full-text) containing documents and spans, with deterministic span IDs. citeturn1search3turn1search0

A symbol registry (tables or documents) supporting aliases and evidence pointers (span IDs) plus a small relationship graph.

A JSON-RPC server exposing symbol and span methods; JSON-RPC 2.0 defines the message shape. citeturn0search1

An OpenRPC spec file describing all methods and example requests/responses. citeturn0search6

A read-guard module enforcing symbol-first lookups and bounded span reads.

A thin CLI client (for repeatable tests) and optionally an agent tool wrapper.

### Sample JSON-RPC methods and payloads

Example: `symbol/findDefinition` (your `find_definition("sharpe_ratio")` use case)

```json
{"jsonrpc":"2.0","id":"req-001","method":"symbol/findDefinition","params":{"query":"sharpe_ratio","k":3}}
```

```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "result": {
    "matches": [
      {
        "symbol_id": "sym.finance.metric.sharpe_ratio",
        "name": "Sharpe ratio",
        "aliases": ["Sharpe", "Sharpe Ratio"],
        "kind": "metric_definition",
        "signature": "S = (E[R_p] - R_f) / σ_p",
        "evidence": [
          {"doc_id":"doc:paper:sharpe1966","span_id":"span:doc:paper:sharpe1966:sec2:eq1"}
        ],
        "provenance_id": "prov:activity:symbol_extraction:run_2026_04_15_001",
        "confidence": 0.93
      }
    ],
    "unresolved": []
  }
}
```

Example: `span/get` (bounded evidence fetch)

```json
{"jsonrpc":"2.0","id":"req-002","method":"span/get","params":{"span_id":"span:doc:paper:sharpe1966:sec2:eq1","max_chars":1200}}
```

```json
{
  "jsonrpc": "2.0",
  "id": "req-002",
  "result": {
    "span_id": "span:doc:paper:sharpe1966:sec2:eq1",
    "doc_id": "doc:paper:sharpe1966",
    "kind": "equation_block",
    "text": "…",
    "locator": {"page": 3, "section_path": ["2", "Risk-adjusted performance"], "offsets": [15342, 15801]},
    "source": {"content_hash": "sha256:…", "extractor": "grobid", "extractor_version": "…"},
    "provenance_id": "prov:activity:span_extraction:run_2026_04_15_000"
  }
}
```

These examples are standard JSON-RPC 2.0-shaped requests and responses. citeturn0search1

### Effort estimates

Estimates assume you already have a functioning vault and a modest corpus (thousands to tens of thousands of notes/spans), and you prioritize determinism and auditing over UI polish.

| Scope | Solo developer | Small team (2–3 engineers) |
|---|---|---|
| Local-first MVP (SQLite FTS5 + JSON-RPC + read-guard + minimal eval) | 4–7 weeks | 2–4 weeks |
| Research-grade baseline (Postgres + pgvector + OpenRPC + provenance + UI v1) | 8–12 weeks | 4–7 weeks |
| “Comfortably scalable” (hybrid retrieval, reranking, richer lineage UI, stronger security hardening) | 12–20 weeks | 7–12 weeks |

The reason team scaling helps is parallelism: one engineer on ingestion+spans, one on symbol+API+policy, one on eval+UI/observability.

## Evaluation, testing, and reproducibility

### Evaluation datasets and metrics

You should evaluate two layers separately: symbol resolution and evidence retrieval. Conflating them makes debugging impossible.

Symbol resolution: Use MRR and top-k hit rates. MRR is used in QA track evaluations (e.g., TREC QA) where systems return ranked candidate answers/snippets. citeturn5search0turn5search1

Evidence retrieval: Use nDCG@k for graded relevance and Recall@k for coverage. nDCG is defined in classic IR evaluation work on cumulative gain and discounted cumulative gain. citeturn4search4turn4search0

To sanity-check retrieval approaches against broader practice, external benchmarks like BEIR provide a heterogeneous benchmark for zero-shot retrieval evaluation (useful as reference, not as your primary success measure). citeturn4search2turn4search6 If you build RAG-style answer generation on top, frameworks like RAGAS propose automated evaluation dimensions for RAG pipelines (retrieval + generation faithfulness) and can complement your labeled gold set. citeturn4search3turn4search7

### Token usage and latency measurement

If you use OpenAI models, token usage can be measured directly using API response `usage` structures (e.g., input/output token counts in the Responses API), enabling per-request and per-stage accounting (resolve vs retrieve vs generate). citeturn8search5turn8search7

For systemwide observability—latency percentiles, policy exceptions, cache hit rates—instrument the server and clients with OpenTelemetry traces/metrics/logs so a single request can be followed through ingestion, retrieval, policy decisions, and model calls. citeturn2search5turn2search1

### Testing matrix

| Test layer | What to test | Example checks | Success condition |
|---|---|---|---|
| Unit | Deterministic IDs and schema validation | Same file produces same doc_id/span_id; JSON schema validation for symbol records; OpenRPC examples validate | Zero nondeterminism for unchanged inputs; schema rejects malformed symbols citeturn0search6turn11search0 |
| Integration | End-to-end ingestion per source type | PDF extraction equivalence under pinned tool versions (Tika/GROBID); HTML span building stability; Markdown link parsing | Re-ingestion reproducible; provenance includes extractor name/version and timestamps citeturn2search2turn2search3turn0search3 |
| Retrieval evaluation | Symbol resolution + evidence ranking | MRR on “definition/config” queries; nDCG and Recall@k on evidence queries | Metrics trend upward or stay stable; regressions fail CI citeturn5search0turn4search4 |
| Policy enforcement | Read-guard decisions and budgets | Symbolic queries do not trigger bulk reads; exceptions logged with reason; `policy/explainDecision` returns traceable rationale | Policy violations become test failures; exception rate measurable and bounded citeturn2search5turn0search1 |
| Delegation | Sub-agent capsule adherence | Sub-agent cannot call disallowed methods; outputs include used_symbol_ids/span_ids | Delegation outputs replay deterministically; no silent policy bypass citeturn3search2turn8search1 |
| Security | Authz/authn and data exposure | API rejects unauthorized calls; sensitive fields not leaked by default | No broken object/property authorization patterns in core endpoints citeturn6search0turn6search14 |

### Reproducibility checklist

A discipline layer that cannot be reproduced is just an elaborate journaling system.

The checklist below is aligned with reproducible computational research guidance emphasizing recorded workflows, preserved intermediates, and minimized manual steps. citeturn11search0

| Category | Minimum practice | Concrete artifact |
|---|---|---|
| Dependency pinning | Lock dependencies; record runtime versions | `poetry.lock` or `environment.yml` plus tool versions (extractors, embedding model) citeturn11search1turn11search2 |
| Containerization | Build deterministically when needed | Docker build config; reproducible build settings (e.g., SOURCE_DATE_EPOCH where applicable) citeturn11search3 |
| Dataset/versioning | Version gold query sets and corpora snapshots | `eval/queries_v1.jsonl`; corpus manifest with hashes |
| Provenance logging | Record who/what/when produced symbols and answers | PROV-inspired ledger tables; link every symbol to extraction activities and source entities citeturn0search3 |
| Contracts | Freeze API behavior via specs | OpenRPC spec with examples and automated contract tests citeturn0search6turn0search9 |
| Observability | Keep comparable logs/traces across runs | OpenTelemetry spans with request IDs; dashboards for latency/token usage citeturn2search5turn2search9 |

## Deployment, security, and UX

### Deployment and scaling considerations

A local-first SQLite catalog is extremely attractive for early discipline work because it minimizes operational overhead while giving you strong lexical navigation via FTS5. citeturn1search3 If you expect concurrent writes from multiple clients or long-running services, SQLite WAL mode is a common approach to improving concurrency characteristics, though it has constraints and trade-offs (notably with very large transactions). citeturn2search0turn2search16

A PostgreSQL-based deployment is the cleanest “single source” approach when you want full-text search, flexible metadata (JSONB), and embedding vectors (pgvector) in one place, reducing synchronization complexity. citeturn1search0turn1search1turn1search2

If you later externalize vector search, vector databases like Qdrant and Weaviate emphasize payload filtering and hybrid search patterns, but they introduce multi-store consistency engineering. Qdrant’s docs explicitly emphasize payload indexes for filter efficiency, and Weaviate’s docs describe hybrid search as fusing vector and keyword search. citeturn7search9turn7search2

### Security, privacy, and auditability

Because your knowledge server is an API, treat it like an API even if it’s “just you.” OWASP’s API Security Top 10 highlights authorization failures and excessive data exposure patterns as common API risks; your default policy should be least privilege and “don’t return what you don’t need.” citeturn6search0turn6search14

For auditability, NIST SP 800-53 provides comprehensive security and privacy control families, including audit and accountability controls relevant to logging policy exceptions and access. citeturn6search1turn6search9 For personally identifiable information, NIST SP 800-122 provides practical guidance on identifying PII and tailoring protections; this is relevant if your vault includes personal identifiers or sensitive notes. citeturn6search6turn6search2

A minimal security stance for the MVP:

Authenticate clients calling the JSON-RPC server.

Authorize methods: “span/rawDocumentRead” should be privileged; symbol and bounded span reads can be broadly allowed.

Log access and policy exceptions with trace IDs (and treat logs as sensitive data).

### UX and visualization: Lineage Explorer

The Lineage Explorer is the system’s debugging console: click a symbol, see its definition, see evidence spans, see provenance activities, walk relationships (depends_on, cited_by), and answer “why does the system believe this?”

Obsidian’s Graph view demonstrates the value of graph visualization for vault relationships: nodes represent notes; edges represent internal links. citeturn6search3 For an in-app lineage graph, Cytoscape.js is a practical web building block for interactive graph visualization and manipulation, designed to be embedded into applications and support user interaction (zoom, pan, selection). citeturn7search0turn7search8

image_group{"layout":"carousel","aspect_ratio":"16:9","query":["Obsidian graph view core plugin screenshot","Cytoscape.js interactive graph example UI","knowledge graph lineage explorer UI mockup","provenance graph visualization example"],"num_per_query":1}

A minimal Lineage Explorer layout that supports disciplined workflows:

A left pane for symbol search and symbol cards (definition, aliases, version, status).

A center pane for evidence spans (bounded text with source metadata and “open in vault” actions).

A right pane for a graph neighborhood view (symbol dependencies, citations, derivations) with filters by relationship and time.

A bottom pane for policy explanations: why a request was blocked, budgets used, and how to resolve (create symbol, add alias, reindex).

## Technology options and recommendations

### Comparative options table

The goal of this table is not exhaustiveness; it is to show trade-offs that affect reproducibility, enforcement, and operational drag.

| Layer | Option | Pros | Cons | Recommendation |
|---|---|---|---|---|
| Relational + lexical | **entity["organization","PostgreSQL","relational database"]** | Strong full-text search primitives (`tsvector`, `tsquery`) and ranking controls; JSONB for flexible metadata. citeturn1search0turn1search1turn1search4 | Higher ops than embedded DB; schema/index tuning required | Best “research-grade baseline” when you want one transactional source of truth |
| Embedded + lexical | **entity["organization","SQLite","embedded database"]** (FTS5 + WAL) | Very low ops; FTS5 provides efficient full-text search. citeturn1search3 WAL mode can improve concurrency in many workloads. citeturn2search16 | Harder to scale multi-user writes; vector search is separate unless you add extensions | Best local-first MVP; keep the API stable so you can migrate |
| Vector in Postgres | **entity["organization","pgvector","postgres vector extension"]** | Stores embeddings with relational data; supports similarity search and supports ANN options. citeturn1search2turn1search18 | Not all specialized vector-db features; performance tuning still matters | Recommended with Postgres for simplicity and consistency |
| Local vector index | **entity["organization","FAISS","vector similarity library"]** | Efficient similarity search and clustering; strong published foundations and widely used. citeturn9search17turn9search9 | Not a database; you manage persistence, metadata joins, and updates | Good with SQLite for local-first semantics; expect glue code |
| Vector DB | **entity["company","Qdrant","vector database"]** | Payload filtering with indexes is explicitly supported; good for scalable filtered vector search. citeturn7search9turn7search5 | Another service; multi-store consistency complexity | Consider when corpus/concurrency outgrows integrated storage |
| Vector DB | **entity["company","Weaviate","vector database"]** | Hybrid search fuses vector and keyword search; supports configurable fusion. citeturn7search2turn7search10 | Additional ops; object model overlap with symbol registry can cause duplication | Consider if hybrid retrieval is central and you accept the overlap |
| LSP-style server framework | **entity["organization","pygls","python lsp framework"]** | Generic implementation of LSP in Python; accelerates LSP-style server patterns. citeturn3search3turn3search11 | LSP adds editor conventions you may not need | Recommended if you want true LSP compatibility; otherwise implement JSON-RPC directly |
| JSON-RPC transport libs | **entity["company","Microsoft","technology company"]** vscode-languageserver-node JSON-RPC | Implements the base messaging protocol used in VS Code language servers; can be used standalone for JSON-RPC channels. citeturn9search0turn9search4 | Node stack if your backend is Python; adds cross-language complexity | Great if your server is Node/TS; otherwise use a Python JSON-RPC library |
| Embeddings (API) | **entity["company","OpenAI","ai company"]** embeddings | Clear official docs; `text-embedding-3-large` default dimension is 3072 and designed for search/relatedness tasks. citeturn3search5turn3search1 | External dependency; must log model/version for reproducibility | Recommended baseline for quality; record usage and versions in provenance |
| Embeddings (API) | **entity["company","Cohere","ai company"]** Embed | Official model catalog with dimensions and multilingual options. citeturn10search0turn10search4 | External dependency; evolving model lineup | Strong alternative when you want explicit model variants and multilingual support |
| Embeddings (API) | **entity["company","Voyage AI","ai company"]** embeddings | Official docs list context length and embedding dimension options per model. citeturn10search1turn10search17 | External dependency; ecosystem shifts quickly | Consider when retrieval quality/latency trade-offs matter and you can accept vendor dependency |
| Embeddings (local) | **entity["organization","Sentence Transformers","embedding toolkit"]** | Documented toolkit for embedding and reranker models; supports local compute and training. citeturn9search2turn9search18 | You manage inference performance and model selection | Recommended for offline/private deployments and domain finetuning |

### Recommended baseline stacks

Local-first baseline (fastest to usefulness):
- SQLite + FTS5 for catalog and span search. citeturn1search3
- Optional FAISS for vector fallback retrieval. citeturn9search17turn9search1
- JSON-RPC 2.0 server with OpenRPC spec for contract discipline. citeturn0search1turn0search6
- Provenance ledger modeled after PROV-DM (even if minimal initially). citeturn0search3
- Parsing with Tika; add GROBID for scientific PDFs as needed. citeturn2search2turn2search3

Research-grade baseline (best long-term coherence):
- PostgreSQL full-text + JSONB + pgvector. citeturn1search0turn1search1turn1search2
- Same JSON-RPC/OpenRPC interface as local-first. citeturn0search6turn0search1
- OpenAI embeddings for retrieval; instrument usage via API usage fields and OpenTelemetry. citeturn3search5turn8search5turn2search5
- Optional LSIF-inspired snapshot export for offline navigation and reproducible index archives. citeturn9search3turn9search7

### Risk and mitigation table

| Risk | How it shows up | Mitigation strategy |
|---|---|---|
| PDF extraction variability breaks span stability | Evidence spans shift between runs; citations become unreliable | Record extractor choice/version in provenance; use GROBID for structured technical PDFs; keep raw blobs immutable and re-extract deterministically citeturn2search3turn0search3turn11search0 |
| Over-strict read-guard reduces usability | “Insufficient evidence” too often; users bypass guard | Add explicit exception pathways with logged reasons; tune intent classification; treat exception rate as a metric with targets |
| Symbol registry becomes messy | Duplicate symbols; alias chaos; poor resolution metrics | Establish naming conventions and namespaces; measure MRR and fix top failure modes; require evidence spans for symbol definitions citeturn5search0turn4search4 |
| Multi-store consistency issues | Vector DB and relational DB disagree; provenance links break | Prefer integrated storage early (Postgres + pgvector); only split stores when scale demands it citeturn1search2turn1search18 |
| Sensitive data exposure via API | Span/get leaks secrets; accidental over-return | Follow OWASP API security guidance (authz, least privilege, avoid excessive data exposure); apply NIST PII handling guidance where relevant citeturn6search0turn6search6 |
| Reproducibility regressions | Same inputs produce different outputs after upgrades | Pin deps, log versions, containerize where helpful, and treat the evaluation suite as a required gate citeturn11search0turn11search3 |