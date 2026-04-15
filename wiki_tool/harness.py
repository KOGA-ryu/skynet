from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import ast
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from wiki_tool.catalog import DEFAULT_DB, fts_query
from wiki_tool.ids import digest


DEFAULT_SPEC_DIR = Path("harness_specs")
DEFAULT_HARNESS_DB = Path("state/harness.sqlite")
FENCE_RE = re.compile(r"^```(yaml|yml)\s*$", re.MULTILINE)
REQUIRED_SPEC_KEYS = {"kind", "id", "version", "description"}


@dataclass(frozen=True)
class SpecRegistry:
    specs: dict[str, dict[str, list[dict[str, Any]]]]

    def latest(self, kind: str, spec_id: str) -> dict[str, Any]:
        versions = self.specs.get(kind, {}).get(spec_id, [])
        if not versions:
            raise KeyError(f"No {kind} spec found for {spec_id}")
        return sorted(versions, key=lambda item: int(item["version"]))[-1]

    def all_specs(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for by_id in self.specs.values():
            for versions in by_id.values():
                items.extend(versions)
        return items


def validate_harness_specs(spec_dir: Path = DEFAULT_SPEC_DIR) -> dict[str, Any]:
    errors: list[str] = []
    registry = load_specs(spec_dir, errors=errors)
    for spec in registry.all_specs():
        missing = sorted(REQUIRED_SPEC_KEYS - set(spec))
        if missing:
            errors.append(f"{spec.get('id', '<missing>')} missing keys: {', '.join(missing)}")
    for contract in registry.specs.get("task_contract", {}).values():
        for version in contract:
            chain_id = version.get("chain", {}).get("id")
            if chain_id and chain_id not in registry.specs.get("reasoning_chain", {}):
                errors.append(f"{version['id']} references missing chain {chain_id}")
    return {
        "errors": errors,
        "spec_count": len(registry.all_specs()),
        "specs": [
            {
                "description": item.get("description", ""),
                "id": item.get("id"),
                "kind": item.get("kind"),
                "version": item.get("version"),
            }
            for item in sorted(
                registry.all_specs(),
                key=lambda item: (str(item.get("kind")), str(item.get("id")), int(item.get("version", 0))),
            )
        ],
        "valid": not errors,
    }


def load_specs(spec_dir: Path = DEFAULT_SPEC_DIR, *, errors: list[str] | None = None) -> SpecRegistry:
    errors = errors if errors is not None else []
    specs: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for path in sorted(spec_dir.glob("*.md")):
        for index, block in enumerate(extract_yaml_blocks(path.read_text()), start=1):
            try:
                spec = parse_yaml_subset(block)
            except ValueError as exc:
                errors.append(f"{path}:{index}: {exc}")
                continue
            if not isinstance(spec, dict):
                errors.append(f"{path}:{index}: YAML block must produce an object")
                continue
            spec["_source_path"] = str(path)
            spec["_source_block"] = index
            kind = str(spec.get("kind", ""))
            spec_id = str(spec.get("id", ""))
            specs.setdefault(kind, {}).setdefault(spec_id, []).append(spec)
    return SpecRegistry(specs=specs)


def extract_yaml_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    lines = text.splitlines()
    in_block = False
    current: list[str] = []
    for line in lines:
        if not in_block and line.strip() in {"```yaml", "```yml"}:
            in_block = True
            current = []
            continue
        if in_block and line.strip() == "```":
            blocks.append("\n".join(current))
            in_block = False
            continue
        if in_block:
            current.append(line)
    return blocks


def parse_yaml_subset(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    lines = text.splitlines()
    value, index = parse_yaml_block(lines, 0, 0)
    index = skip_blank(lines, index)
    if index < len(lines):
        raise ValueError(f"unexpected content at line {index + 1}")
    return value


def parse_yaml_block(lines: list[str], index: int, indent: int) -> tuple[Any, int]:
    index = skip_blank(lines, index)
    if index >= len(lines):
        return {}, index
    stripped = lines[index].lstrip(" ")
    if count_indent(lines[index]) < indent:
        return {}, index
    if stripped.startswith("- "):
        return parse_sequence(lines, index, indent)
    return parse_mapping(lines, index, indent)


def parse_mapping(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        index = skip_blank(lines, index)
        if index >= len(lines):
            break
        line = lines[index]
        current_indent = count_indent(line)
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"unexpected indentation at line {index + 1}")
        stripped = line.strip()
        if stripped.startswith("- "):
            break
        key, rest = split_key_value(stripped, index)
        if rest in {"|", ">"}:
            value, index = parse_block_scalar(lines, index + 1, indent, folded=rest == ">")
        elif rest:
            value = parse_scalar(rest)
            index += 1
        else:
            value, index = parse_yaml_block(lines, index + 1, indent + 2)
        result[key] = value
    return result, index


def parse_sequence(lines: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        index = skip_blank(lines, index)
        if index >= len(lines):
            break
        line = lines[index]
        current_indent = count_indent(line)
        if current_indent < indent:
            break
        if current_indent != indent or not line.lstrip(" ").startswith("- "):
            break
        item_text = line.lstrip(" ")[2:].strip()
        if not item_text:
            item, index = parse_yaml_block(lines, index + 1, indent + 2)
            result.append(item)
            continue
        if looks_like_mapping_item(item_text):
            key, rest = split_key_value(item_text, index)
            item: dict[str, Any] = {key: parse_scalar(rest) if rest else {}}
            index += 1
            next_index = skip_blank(lines, index)
            if next_index < len(lines) and count_indent(lines[next_index]) > indent:
                extra, index = parse_mapping(lines, index, indent + 2)
                item.update(extra)
            result.append(item)
            continue
        result.append(parse_scalar(item_text))
        index += 1
    return result, index


def parse_block_scalar(
    lines: list[str],
    index: int,
    parent_indent: int,
    *,
    folded: bool,
) -> tuple[str, int]:
    collected: list[str] = []
    content_indent: int | None = None
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            collected.append("")
            index += 1
            continue
        current_indent = count_indent(line)
        if current_indent <= parent_indent:
            break
        if content_indent is None:
            content_indent = current_indent
        collected.append(line[min(current_indent, content_indent) :])
        index += 1
    return (" ".join(part.strip() for part in collected if part.strip()) if folded else "\n".join(collected)), index


def split_key_value(text: str, index: int) -> tuple[str, str]:
    if ":" not in text:
        raise ValueError(f"expected key/value at line {index + 1}")
    key, rest = text.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"empty key at line {index + 1}")
    return key, rest.strip()


def looks_like_mapping_item(text: str) -> bool:
    if ":" not in text:
        return False
    return bool(re.match(r"^[A-Za-z0-9_.-]+:\s*", text))


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return ast.literal_eval(value)
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [] if not inner else [parse_scalar(part) for part in split_inline(inner)]
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        result: dict[str, Any] = {}
        if inner:
            for part in split_inline(inner):
                key, rest = split_key_value(part, 0)
                result[key] = parse_scalar(rest)
        return result
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def split_inline(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    for char in value:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def skip_blank(lines: list[str], index: int) -> int:
    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        index += 1
    return index


def count_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def run_answer_with_citations(
    user_query: str,
    *,
    catalog_db: Path = DEFAULT_DB,
    harness_db: Path = DEFAULT_HARNESS_DB,
    spec_dir: Path = DEFAULT_SPEC_DIR,
) -> dict[str, Any]:
    validation = validate_harness_specs(spec_dir)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    registry = load_specs(spec_dir)
    contract = registry.latest("task_contract", "wiki.answer_with_citations")
    chain = registry.latest("reasoning_chain", str(contract["chain"]["id"]))
    run_id = f"run:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}:{digest(user_query)}"
    started = utc_now()
    trace = RunTrace(run_id=run_id, harness_db=harness_db)
    trace.start_run(
        task_id=str(contract["id"]),
        task_version=int(contract["version"]),
        chain_id=str(chain["id"]),
        chain_version=int(chain["version"]),
        inputs={"user_query": user_query},
        started_at=started,
    )
    failures: list[dict[str, Any]] = []
    outputs: dict[str, Any] = {}
    status = "running"

    try:
        plan = {
            "must_answer": True,
            "query_intent": user_query.strip(),
            "search_queries": build_search_queries(user_query),
            "uncertainty_notes": [],
        }
        trace.record_step("s1_plan", "deterministic", status="ok", output=plan)

        retrieval_k = int(contract.get("budgets", {}).get("max_retrieval_k", 8))
        chunks = retrieve_catalog_chunks(catalog_db, plan["search_queries"], k=retrieval_k)
        trace.record_step(
            "s2_retrieve",
            "tool",
            tool_name="retriever.search",
            status="ok" if chunks else "failed",
            input_data={"queries": plan["search_queries"], "k": retrieval_k},
            output={"hit_count": len(chunks)},
            retrieval_candidates=chunks,
        )
        if not chunks:
            failures.append(failure("s2_retrieve", "RETRIEVAL_EMPTY", "high", {"query": user_query}))

        outputs = synthesize_answer(user_query=user_query, chunks=chunks, min_citations=min_citations(contract))
        schema_valid = output_schema_valid(outputs)
        if not schema_valid:
            failures.append(failure("s3_synthesize", "OUTPUT_SCHEMA_INVALID", "critical", outputs))
        trace.record_step(
            "s3_synthesize",
            "deterministic",
            status="ok" if schema_valid else "failed",
            output={**outputs, "schema_valid": schema_valid},
        )

        grounded = groundedness_check(outputs.get("citations", []), chunks, min_citations=min_citations(contract))
        if not grounded["pass"]:
            failures.append(failure("s4_verify_groundedness", "GROUNDEDNESS_FAIL", "high", grounded))
        trace.record_step(
            "s4_verify_groundedness",
            "tool",
            tool_name="verifier.groundedness_check",
            status="ok" if grounded["pass"] else "failed",
            input_data={"citation_count": len(outputs.get("citations", []))},
            output=grounded,
        )

        status = "pass" if schema_valid and grounded["pass"] and not failures else "fail"
        trace.record_step(
            "s5_persist",
            "tool",
            tool_name="store.persist_run",
            status="ok",
            output={"status": status},
        )
    except Exception as exc:
        status = "error"
        failures.append(failure("runtime", "TOOL_CALL_ERROR", "high", {"error": str(exc)}))
        raise
    finally:
        ended = utc_now()
        metrics = {
            "failure_count": len(failures),
            "retrieval_hit_count": len(outputs.get("citations", [])),
            "wall_clock_seconds": elapsed_seconds(started, ended),
        }
        trace.finish_run(
            ended_at=ended,
            status=status,
            outputs=outputs,
            metrics=metrics,
            failures=failures,
        )

    return {
        "answer_markdown": outputs.get("answer_markdown", ""),
        "citations": outputs.get("citations", []),
        "failures": failures,
        "harness_db": str(harness_db),
        "run_id": run_id,
        "status": status,
    }


def list_harness_runs(harness_db: Path = DEFAULT_HARNESS_DB, *, limit: int = 10) -> dict[str, Any]:
    init_harness_db(harness_db)
    with sqlite3.connect(harness_db) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT run_id, task_id, task_version, chain_id, chain_version,
                   started_at_utc, ended_at_utc, status, metrics_json
            FROM harness_runs
            ORDER BY started_at_utc DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {
        "harness_db": str(harness_db),
        "runs": [
            {
                **{key: row[key] for key in row.keys() if key != "metrics_json"},
                "metrics": json.loads(row["metrics_json"] or "{}"),
            }
            for row in rows
        ],
    }


def get_harness_run(run_id: str, harness_db: Path = DEFAULT_HARNESS_DB) -> dict[str, Any]:
    init_harness_db(harness_db)
    with sqlite3.connect(harness_db) as con:
        con.row_factory = sqlite3.Row
        run = con.execute(
            "SELECT * FROM harness_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise KeyError(f"No harness run found for {run_id}")
        steps = con.execute(
            """
            SELECT step_id, step_type, started_at_utc, ended_at_utc, status,
                   tool_name, input_json, output_json
            FROM harness_steps
            WHERE run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
        candidates = con.execute(
            """
            SELECT step_id, chunk_id, artifact_id, rank, score, method,
                   path, start_line, end_line, heading
            FROM harness_retrieval_candidates
            WHERE run_id = ?
            ORDER BY step_id, rank
            """,
            (run_id,),
        ).fetchall()
        failures = con.execute(
            """
            SELECT step_id, failure_code, severity, details_json
            FROM harness_failures
            WHERE run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    return {
        "failures": [
            {
                "details": json.loads(row["details_json"] or "{}"),
                "failure_code": row["failure_code"],
                "severity": row["severity"],
                "step_id": row["step_id"],
            }
            for row in failures
        ],
        "harness_db": str(harness_db),
        "retrieval_candidates": [dict(row) for row in candidates],
        "run": {
            **{
                key: run[key]
                for key in run.keys()
                if key not in {"inputs_json", "outputs_json", "metrics_json"}
            },
            "inputs": json.loads(run["inputs_json"] or "{}"),
            "metrics": json.loads(run["metrics_json"] or "{}"),
            "outputs": json.loads(run["outputs_json"] or "{}"),
        },
        "steps": [
            {
                **{
                    key: row[key]
                    for key in row.keys()
                    if key not in {"input_json", "output_json"}
                },
                "input": json.loads(row["input_json"] or "{}"),
                "output": json.loads(row["output_json"] or "{}"),
            }
            for row in steps
        ],
    }


class RunTrace:
    def __init__(self, *, run_id: str, harness_db: Path) -> None:
        self.run_id = run_id
        self.harness_db = harness_db
        init_harness_db(harness_db)

    def start_run(
        self,
        *,
        task_id: str,
        task_version: int,
        chain_id: str,
        chain_version: int,
        inputs: dict[str, Any],
        started_at: str,
    ) -> None:
        with sqlite3.connect(self.harness_db) as con:
            con.execute(
                """
                INSERT INTO harness_runs(
                    run_id, task_id, task_version, chain_id, chain_version,
                    started_at_utc, ended_at_utc, status, inputs_json, outputs_json, metrics_json
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, '{}', '{}')
                """,
                (
                    self.run_id,
                    task_id,
                    task_version,
                    chain_id,
                    chain_version,
                    started_at,
                    "running",
                    json.dumps(inputs, sort_keys=True),
                ),
            )

    def record_step(
        self,
        step_id: str,
        step_type: str,
        *,
        status: str,
        tool_name: str | None = None,
        input_data: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        retrieval_candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        started = utc_now()
        ended = utc_now()
        with sqlite3.connect(self.harness_db) as con:
            con.execute(
                """
                INSERT INTO harness_steps(
                    run_id, step_id, step_type, started_at_utc, ended_at_utc,
                    status, tool_name, input_json, output_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    step_id,
                    step_type,
                    started,
                    ended,
                    status,
                    tool_name,
                    json.dumps(input_data or {}, sort_keys=True),
                    json.dumps(output or {}, sort_keys=True),
                ),
            )
            for rank, candidate in enumerate(retrieval_candidates or [], start=1):
                candidate_key = f"{self.run_id}|{step_id}|{rank}|{candidate['chunk_id']}"
                con.execute(
                    """
                    INSERT INTO harness_retrieval_candidates(
                        run_id, step_id, candidate_id, chunk_id, artifact_id,
                        rank, score, method, path, start_line, end_line, heading
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.run_id,
                        step_id,
                        f"cand:{digest(candidate_key)}",
                        candidate["chunk_id"],
                        candidate["artifact_id"],
                        rank,
                        float(candidate["score"]),
                        candidate["method"],
                        candidate["path"],
                        candidate.get("start_line"),
                        candidate.get("end_line"),
                        candidate.get("heading"),
                    ),
                )

    def finish_run(
        self,
        *,
        ended_at: str,
        status: str,
        outputs: dict[str, Any],
        metrics: dict[str, Any],
        failures: list[dict[str, Any]],
    ) -> None:
        with sqlite3.connect(self.harness_db) as con:
            for item in failures:
                con.execute(
                    """
                    INSERT INTO harness_failures(run_id, step_id, failure_code, severity, details_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self.run_id,
                        item["step_id"],
                        item["failure_code"],
                        item["severity"],
                        json.dumps(item.get("details", {}), sort_keys=True),
                    ),
                )
            con.execute(
                """
                UPDATE harness_runs
                SET ended_at_utc = ?, status = ?, outputs_json = ?, metrics_json = ?
                WHERE run_id = ?
                """,
                (
                    ended_at,
                    status,
                    json.dumps(outputs, sort_keys=True),
                    json.dumps(metrics, sort_keys=True),
                    self.run_id,
                ),
            )


def init_harness_db(path: Path = DEFAULT_HARNESS_DB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS harness_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                task_version INTEGER NOT NULL,
                chain_id TEXT NOT NULL,
                chain_version INTEGER NOT NULL,
                started_at_utc TEXT NOT NULL,
                ended_at_utc TEXT,
                status TEXT NOT NULL,
                inputs_json TEXT NOT NULL,
                outputs_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS harness_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                step_type TEXT NOT NULL,
                started_at_utc TEXT NOT NULL,
                ended_at_utc TEXT NOT NULL,
                status TEXT NOT NULL,
                tool_name TEXT,
                input_json TEXT NOT NULL,
                output_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS harness_retrieval_candidates (
                candidate_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                score REAL NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                heading TEXT
            );
            CREATE TABLE IF NOT EXISTS harness_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                failure_code TEXT NOT NULL,
                severity TEXT NOT NULL,
                details_json TEXT NOT NULL
            );
            """
        )


def retrieve_catalog_chunks(catalog_db: Path, queries: list[str], *, k: int) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(catalog_db) as con:
        con.row_factory = sqlite3.Row
        for query in queries:
            match = fts_query(query)
            if not match:
                continue
            rows = con.execute(
                """
                SELECT sp.span_id, sp.path, sp.heading, sp.start_line, sp.end_line, sp.text,
                       bm25(spans_fts) AS rank
                FROM spans_fts
                JOIN spans sp ON sp.span_id = spans_fts.span_id
                WHERE spans_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, k),
            ).fetchall()
            for row in rows:
                score = max(0.0, -float(row["rank"]))
                existing = merged.get(row["span_id"])
                if existing and existing["score"] >= score:
                    continue
                merged[row["span_id"]] = {
                    "artifact_id": row["path"],
                    "chunk_id": row["span_id"],
                    "end_line": row["end_line"],
                    "heading": row["heading"],
                    "method": "catalog_fts_span",
                    "path": row["path"],
                    "score": score,
                    "start_line": row["start_line"],
                    "text": row["text"],
                }
    return sorted(merged.values(), key=lambda item: (-item["score"], item["path"], item["start_line"]))[:k]


def synthesize_answer(*, user_query: str, chunks: list[dict[str, Any]], min_citations: int) -> dict[str, Any]:
    citations = []
    for chunk in chunks[: max(min_citations, min(4, len(chunks)))]:
        citations.append(
            {
                "artifact_id": chunk["artifact_id"],
                "chunk_id": chunk["chunk_id"],
                "quote": quote_from_text(chunk["text"]),
                "relevance_note": f"Matched query terms in {chunk['path']}#{chunk['heading']}",
            }
        )
    if not citations:
        answer = f"No grounded wiki evidence was found for `{user_query}`."
    else:
        lines = [
            f"Found {len(chunks)} candidate wiki spans for `{user_query}`.",
            "",
            "## Strongest Evidence",
            "",
        ]
        for index, citation in enumerate(citations, start=1):
            lines.append(f"{index}. `{citation['artifact_id']}`: {citation['quote']}")
        answer = "\n".join(lines)
    return {"answer_markdown": answer, "citations": citations}


def groundedness_check(
    citations: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    *,
    min_citations: int,
) -> dict[str, Any]:
    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    errors = []
    if len(citations) < min_citations:
        errors.append(f"expected at least {min_citations} citations")
    for citation in citations:
        chunk = chunk_by_id.get(citation.get("chunk_id"))
        if chunk is None:
            errors.append(f"unknown chunk_id {citation.get('chunk_id')}")
            continue
        if normalize_quote(str(citation.get("quote", ""))) not in normalize_quote(chunk["text"]):
            errors.append(f"quote not found in chunk {citation.get('chunk_id')}")
    return {"errors": errors, "pass": not errors}


def build_search_queries(user_query: str) -> list[str]:
    query = " ".join(user_query.split())
    return [query] if query else []


def min_citations(contract: dict[str, Any]) -> int:
    return int(contract.get("verification_profile", {}).get("require_min_citations", 1))


def output_schema_valid(outputs: dict[str, Any]) -> bool:
    return isinstance(outputs.get("answer_markdown"), str) and isinstance(outputs.get("citations"), list)


def quote_from_text(text: str, *, max_words: int = 20) -> str:
    words = " ".join(text.split()).split()
    return " ".join(words[:max_words])


def normalize_quote(value: str) -> str:
    return " ".join(value.lower().split())


def failure(step_id: str, code: str, severity: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "details": details,
        "failure_code": code,
        "severity": severity,
        "step_id": step_id,
    }


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def elapsed_seconds(started: str, ended: str) -> float:
    start = datetime.fromisoformat(started)
    end = datetime.fromisoformat(ended)
    return (end - start).total_seconds()
