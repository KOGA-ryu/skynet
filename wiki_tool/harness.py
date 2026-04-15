from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import ast
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable

from wiki_tool.catalog import DEFAULT_DB, fts_query
from wiki_tool.ids import digest
from wiki_tool.llm import (
    DEFAULT_OPENAI_MODEL,
    DeterministicSynthesisAdapter,
    OpenAIStructuredSynthesisAdapter,
    StructuredSynthesisAdapter,
    StructuredSynthesisError,
    synthesis_output_schema,
)


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
    for taxonomy in registry.specs.get("failure_taxonomy", {}).values():
        for version in taxonomy:
            severities = set(version.get("enums", {}).get("severity", []))
            actions = set(version.get("enums", {}).get("action", []))
            seen_codes: set[str] = set()
            for item in version.get("failures", []):
                code = str(item.get("code", ""))
                if not code:
                    errors.append(f"{version.get('id', '<missing>')} has failure without code")
                if code in seen_codes:
                    errors.append(f"{version.get('id', '<missing>')} has duplicate failure code {code}")
                seen_codes.add(code)
                severity = str(item.get("severity", ""))
                if severities and severity not in severities:
                    errors.append(f"{code} uses unknown severity {severity}")
                respond = item.get("respond", [])
                if not isinstance(respond, list) or not respond:
                    errors.append(f"{code} must define at least one response action")
                    continue
                for response in respond:
                    action = str(response.get("action", "")) if isinstance(response, dict) else ""
                    if actions and action not in actions:
                        errors.append(f"{code} uses unknown response action {action}")
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
    synthesis: str = "deterministic",
    llm_model: str | None = None,
    synthesis_adapter: StructuredSynthesisAdapter | None = None,
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
        inputs={"llm_model": llm_model, "synthesis": synthesis, "user_query": user_query},
        started_at=started,
    )
    failures: list[dict[str, Any]] = []
    failure_actions: list[dict[str, Any]] = []
    outputs: dict[str, Any] = {}
    chunks: list[dict[str, Any]] = []
    retrieval_fallback_used = False
    retrieval_fallback_hit_count = 0
    retrieval_primary_hit_count = 0
    synthesis_metadata: dict[str, Any] = {"model": llm_model, "provider": synthesis}
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
        retrieval_primary_hit_count = len(chunks)
        retrieval_failure_actions: list[dict[str, Any]] = []
        if not chunks:
            retrieval_failure_actions = record_failure_actions(
                failure_actions,
                registry,
                step_id="s2_retrieve",
                failure_code="RETRIEVAL_EMPTY",
                status="applied",
                reason="Primary retrieval returned no chunks; expanding retrieval with fallback queries.",
                only_actions={"expand_retrieval"},
            )
        trace.record_step(
            "s2_retrieve",
            "tool",
            tool_name="retriever.search",
            status="ok" if chunks else "failed",
            input_data={"queries": plan["search_queries"], "k": retrieval_k},
            output={
                "failure_actions": retrieval_failure_actions,
                "hit_count": len(chunks),
                "profile": "catalog.fts_spans.primary",
            },
            retrieval_candidates=chunks,
        )
        if not chunks:
            retrieval_fallback_used = True
            fallback_queries = build_fallback_search_queries(user_query)
            fallback_chunks = retrieve_catalog_chunks(
                catalog_db,
                fallback_queries,
                k=retrieval_k,
                method="catalog_fts_span_fallback",
            )
            retrieval_fallback_hit_count = len(fallback_chunks)
            trace.record_step(
                "s2b_retrieve_fallback",
                "tool",
                tool_name="retriever.search",
                status="ok" if fallback_chunks else "failed",
                input_data={"queries": fallback_queries, "k": retrieval_k},
                output={
                    "fallback_for": "s2_retrieve",
                    "hit_count": len(fallback_chunks),
                    "profile": "catalog.fts_spans.expanded",
                },
                retrieval_candidates=fallback_chunks,
            )
            chunks = fallback_chunks
            if not chunks:
                failures.append(
                    failure(
                        "s2b_retrieve_fallback",
                        "RETRIEVAL_EMPTY",
                        "high",
                        {
                            "fallback_queries": fallback_queries,
                            "primary_queries": plan["search_queries"],
                            "query": user_query,
                        },
                    )
                )

        adapter = synthesis_adapter or synthesis_adapter_for(synthesis=synthesis, model=llm_model)
        synthesis_attempts: list[dict[str, Any]] = []
        synthesis_failure_actions: list[dict[str, Any]] = []
        schema_valid = False
        max_synthesis_attempts = 2
        attempt = 1
        while attempt <= max_synthesis_attempts:
            synthesis_started = time.perf_counter()
            try:
                synthesis_result = adapter.synthesize(
                    user_query=user_query,
                    chunks=chunks,
                    min_citations=min_citations(contract),
                    output_schema=synthesis_output_schema(),
                )
                outputs = synthesis_result.output
                synthesis_metadata = {
                    **synthesis_result.metadata,
                    "duration_seconds": round(time.perf_counter() - synthesis_started, 3),
                    "mode": synthesis,
                }
                schema_valid = output_schema_valid(outputs)
                synthesis_attempts.append(
                    {
                        "attempt": attempt,
                        "failure_code": None if schema_valid else "OUTPUT_SCHEMA_INVALID",
                        "schema_valid": schema_valid,
                        "status": "ok" if schema_valid else "failed",
                    }
                )
                if schema_valid:
                    break
                if (
                    attempt < max_synthesis_attempts
                    and "retry" in failure_response_actions(registry, "OUTPUT_SCHEMA_INVALID")
                ):
                    synthesis_failure_actions.extend(
                        record_failure_actions(
                            failure_actions,
                            registry,
                            step_id="s3_synthesize",
                            failure_code="OUTPUT_SCHEMA_INVALID",
                            status="applied",
                            reason="Synthesis output failed schema validation; retrying once.",
                            attempt=attempt,
                            only_actions={"retry"},
                        )
                    )
                    attempt += 1
                    continue
                failures.append(failure("s3_synthesize", "OUTPUT_SCHEMA_INVALID", "critical", outputs))
                if "abort" in failure_response_actions(registry, "OUTPUT_SCHEMA_INVALID"):
                    synthesis_failure_actions.extend(
                        record_failure_actions(
                            failure_actions,
                            registry,
                            step_id="s3_synthesize",
                            failure_code="OUTPUT_SCHEMA_INVALID",
                            status="applied",
                            reason="Synthesis output remained schema-invalid after retry budget was exhausted.",
                            attempt=attempt,
                            only_actions={"abort"},
                        )
                    )
                break
            except StructuredSynthesisError as exc:
                outputs = {}
                schema_valid = False
                synthesis_metadata = {
                    "duration_seconds": round(time.perf_counter() - synthesis_started, 3),
                    "error": str(exc),
                    "model": llm_model,
                    "mode": synthesis,
                    "provider": synthesis,
                    "token_usage": None,
                }
                synthesis_attempts.append(
                    {
                        "attempt": attempt,
                        "failure_code": exc.failure_code,
                        "schema_valid": False,
                        "status": "failed",
                    }
                )
                response_actions = failure_response_actions(registry, exc.failure_code)
                should_retry = (
                    exc.failure_code != "LLM_PROVIDER_CONFIG_MISSING"
                    and "retry" in response_actions
                    and attempt < max_synthesis_attempts
                )
                if should_retry:
                    synthesis_failure_actions.extend(
                        record_failure_actions(
                            failure_actions,
                            registry,
                            step_id="s3_synthesize",
                            failure_code=exc.failure_code,
                            status="applied",
                            reason="Structured synthesis failed; retrying once under failure taxonomy policy.",
                            attempt=attempt,
                            only_actions={"retry"},
                        )
                    )
                    attempt += 1
                    continue
                failures.append(failure("s3_synthesize", exc.failure_code, "high", synthesis_metadata))
                if "abort" in response_actions:
                    synthesis_failure_actions.extend(
                        record_failure_actions(
                            failure_actions,
                            registry,
                            step_id="s3_synthesize",
                            failure_code=exc.failure_code,
                            status="applied",
                            reason="Synthesis cannot continue under current failure taxonomy policy.",
                            attempt=attempt,
                            only_actions={"abort"},
                        )
                    )
                break
        trace.record_step(
            "s3_synthesize",
            "deterministic" if synthesis == "deterministic" else "llm",
            status="ok" if schema_valid else "failed",
            tool_name=None if synthesis == "deterministic" else "llm.structured_synthesis",
            input_data={
                "chunk_count": len(chunks),
                "min_citations": min_citations(contract),
                "synthesis": synthesis,
            },
            output={
                **outputs,
                "failure_actions": synthesis_failure_actions,
                "schema_valid": schema_valid,
                "synthesis_attempts": synthesis_attempts,
                "synthesis_metadata": synthesis_metadata,
            },
        )

        groundedness_failure_actions: list[dict[str, Any]] = []
        if schema_valid:
            grounded = groundedness_check(outputs.get("citations", []), chunks, min_citations=min_citations(contract))
            if not grounded["pass"]:
                failures.append(failure("s4_verify_groundedness", "GROUNDEDNESS_FAIL", "high", grounded))
                groundedness_failure_actions = record_failure_actions(
                    failure_actions,
                    registry,
                    step_id="s4_verify_groundedness",
                    failure_code="GROUNDEDNESS_FAIL",
                    status="deferred",
                    reason="Groundedness repair requires a post-synthesis retrieval repair loop.",
                    only_actions={"expand_retrieval", "ask_clarifying"},
                )
        else:
            grounded = {
                "errors": ["synthesis did not produce schema-valid output"],
                "pass": False,
                "skipped": True,
            }
        grounded_status = "skipped" if not schema_valid else "ok" if grounded["pass"] else "failed"
        trace.record_step(
            "s4_verify_groundedness",
            "tool",
            tool_name="verifier.groundedness_check",
            status=grounded_status,
            input_data={"citation_count": len(outputs.get("citations", []))},
            output={**grounded, "failure_actions": groundedness_failure_actions},
        )

        status = "pass" if schema_valid and grounded["pass"] and not failures else "fail"
        trace.record_step(
            "s5_persist",
            "tool",
            tool_name="store.persist_run",
            status="ok",
            output={"failure_actions": failure_actions, "status": status},
        )
    except Exception as exc:
        status = "error"
        failures.append(failure("runtime", "TOOL_CALL_ERROR", "high", {"error": str(exc)}))
        record_failure_actions(
            failure_actions,
            registry,
            step_id="runtime",
            failure_code="TOOL_CALL_ERROR",
            status="deferred",
            reason="Unexpected tool/runtime errors are recorded and re-raised fail-closed.",
            only_actions={"retry", "abort"},
        )
        raise
    finally:
        ended = utc_now()
        metrics = {
            "failure_action_count": len(failure_actions),
            "failure_count": len(failures),
            "retrieval_fallback_hit_count": retrieval_fallback_hit_count,
            "retrieval_fallback_used": retrieval_fallback_used,
            "retrieval_hit_count": len(chunks),
            "retrieval_primary_hit_count": retrieval_primary_hit_count,
            "synthesis": synthesis,
            "synthesis_model": synthesis_metadata.get("model"),
            "synthesis_provider": synthesis_metadata.get("provider"),
            "token_usage": synthesis_metadata.get("token_usage"),
            "wall_clock_seconds": elapsed_seconds(started, ended),
        }
        trace.finish_run(
            ended_at=ended,
            status=status,
            outputs={**outputs, "failure_actions": failure_actions},
            metrics=metrics,
            failures=failures,
        )

    return {
        "answer_markdown": outputs.get("answer_markdown", ""),
        "citations": outputs.get("citations", []),
        "failure_actions": failure_actions,
        "failures": failures,
        "harness_db": str(harness_db),
        "run_id": run_id,
        "status": status,
        "synthesis": synthesis_metadata,
    }


def synthesis_adapter_for(*, synthesis: str, model: str | None) -> StructuredSynthesisAdapter:
    if synthesis == "deterministic":
        return DeterministicSynthesisAdapter()
    if synthesis == "openai":
        return OpenAIStructuredSynthesisAdapter(model=model or DEFAULT_OPENAI_MODEL)
    raise ValueError(f"unsupported synthesis mode: {synthesis}")


def failure_response_actions(registry: SpecRegistry, failure_code: str) -> list[str]:
    _, rule = failure_taxonomy_rule(registry, failure_code)
    if rule is None:
        return ["abort"]
    actions: list[str] = []
    for response in rule.get("respond", []):
        if isinstance(response, dict) and isinstance(response.get("action"), str):
            actions.append(response["action"])
    return actions


def failure_actions_for(
    registry: SpecRegistry,
    *,
    step_id: str,
    failure_code: str,
    status: str,
    reason: str,
    attempt: int | None = None,
    only_actions: set[str] | None = None,
) -> list[dict[str, Any]]:
    taxonomy, rule = failure_taxonomy_rule(registry, failure_code)
    if rule is None:
        records = [
            {
                "action": "abort",
                "reason": f"No failure taxonomy rule exists for {failure_code}; aborting fail-closed.",
                "source_failure_code": failure_code,
                "source_step_id": step_id,
                "status": "applied",
                "taxonomy_id": None,
                "taxonomy_version": None,
                "order": 1,
            }
        ]
    else:
        records = []
        for index, response in enumerate(rule.get("respond", []), start=1):
            if not isinstance(response, dict) or not isinstance(response.get("action"), str):
                continue
            action = response["action"]
            if only_actions is not None and action not in only_actions:
                continue
            records.append(
                {
                    "action": action,
                    "reason": reason,
                    "source_failure_code": failure_code,
                    "source_step_id": step_id,
                    "status": status,
                    "taxonomy_id": taxonomy.get("id"),
                    "taxonomy_version": taxonomy.get("version"),
                    "order": index,
                }
            )
    if attempt is not None:
        for record in records:
            record["attempt"] = attempt
    return records


def failure_taxonomy_rule(
    registry: SpecRegistry,
    failure_code: str,
) -> tuple[dict[str, Any], dict[str, Any] | None] | tuple[None, None]:
    try:
        taxonomy = registry.latest("failure_taxonomy", "failures.core")
    except KeyError:
        return None, None
    for item in taxonomy.get("failures", []):
        if str(item.get("code")) == failure_code:
            return taxonomy, item
    return taxonomy, None


def record_failure_actions(
    action_log: list[dict[str, Any]],
    registry: SpecRegistry,
    *,
    step_id: str,
    failure_code: str,
    status: str,
    reason: str,
    attempt: int | None = None,
    only_actions: set[str] | None = None,
) -> list[dict[str, Any]]:
    records = failure_actions_for(
        registry,
        step_id=step_id,
        failure_code=failure_code,
        status=status,
        reason=reason,
        attempt=attempt,
        only_actions=only_actions,
    )
    action_log.extend(records)
    return records


def list_harness_runs(harness_db: Path = DEFAULT_HARNESS_DB, *, limit: int = 10) -> dict[str, Any]:
    init_harness_db(harness_db)
    with closing(sqlite3.connect(harness_db)) as con:
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
    with closing(sqlite3.connect(harness_db)) as con:
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
        with closing(sqlite3.connect(self.harness_db)) as con:
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
            con.commit()

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
        with closing(sqlite3.connect(self.harness_db)) as con:
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
            con.commit()

    def finish_run(
        self,
        *,
        ended_at: str,
        status: str,
        outputs: dict[str, Any],
        metrics: dict[str, Any],
        failures: list[dict[str, Any]],
    ) -> None:
        with closing(sqlite3.connect(self.harness_db)) as con:
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
            con.commit()


def init_harness_db(path: Path = DEFAULT_HARNESS_DB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as con:
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
        con.commit()


def retrieve_catalog_chunks(
    catalog_db: Path,
    queries: list[str],
    *,
    k: int,
    method: str = "catalog_fts_span",
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    with closing(sqlite3.connect(catalog_db)) as con:
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
                    "method": method,
                    "path": row["path"],
                    "score": score,
                    "start_line": row["start_line"],
                    "text": row["text"],
                }
    return sorted(merged.values(), key=lambda item: (-item["score"], item["path"], item["start_line"]))[:k]


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


def build_fallback_search_queries(user_query: str) -> list[str]:
    tokens = fallback_search_tokens(user_query)
    if not tokens:
        return []
    queries = [" OR ".join(tokens)] if len(tokens) > 1 else []
    queries.extend(tokens)
    return dedupe_preserving_order(queries)


def fallback_search_tokens(user_query: str) -> list[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "for",
        "how",
        "is",
        "me",
        "no",
        "of",
        "or",
        "the",
        "to",
        "what",
        "when",
        "where",
        "why",
        "with",
    }
    tokens = [token for token in re.findall(r"[A-Za-z0-9_./-]+", user_query) if len(token) >= 3]
    return dedupe_preserving_order(token for token in tokens if token.lower() not in stopwords)


def dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def min_citations(contract: dict[str, Any]) -> int:
    return int(contract.get("verification_profile", {}).get("require_min_citations", 1))


def output_schema_valid(outputs: dict[str, Any]) -> bool:
    citations = outputs.get("citations")
    if not isinstance(outputs.get("answer_markdown"), str) or not isinstance(citations, list):
        return False
    required = {"artifact_id", "chunk_id", "quote", "relevance_note"}
    for citation in citations:
        if not isinstance(citation, dict):
            return False
        if not required <= set(citation):
            return False
        if any(not isinstance(citation[key], str) for key in required):
            return False
    return True


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
