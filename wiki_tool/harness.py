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
    LocalStructuredSynthesisAdapter,
    OpenAIStructuredSynthesisAdapter,
    StructuredSynthesisAdapter,
    StructuredSynthesisError,
    quote_from_text,
    synthesis_output_schema,
)


DEFAULT_SPEC_DIR = Path("harness_specs")
DEFAULT_HARNESS_DB = Path("state/harness.sqlite")
FENCE_RE = re.compile(r"^```(yaml|yml)\s*$", re.MULTILINE)
REQUIRED_SPEC_KEYS = {"kind", "id", "version", "description"}
SUPPORTED_SPEC_KINDS = {"failure_taxonomy", "reasoning_chain", "task_contract"}
SUPPORTED_SCHEMA_TYPES = {"array", "boolean", "integer", "list", "number", "object", "string"}
REQUIRED_FAILURE_CODES = {
    "CLAIM_PLAN_INVALID",
    "GROUNDEDNESS_FAIL",
    "LLM_PROVIDER_CONFIG_MISSING",
    "LLM_SYNTHESIS_ERROR",
    "OUTPUT_SCHEMA_INVALID",
    "RETRIEVAL_EMPTY",
    "TOOL_CALL_ERROR",
}
TASK_CONTRACT_REQUIRED_SECTIONS = {
    "budgets",
    "chain",
    "completion_condition",
    "inputs",
    "outputs",
    "persistence",
    "retrieval_profile",
    "tools_allowed",
    "verification_profile",
}
REASONING_CHAIN_STEP_TYPES = {"deterministic", "deterministic_or_llm", "tool"}


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
        kind = str(spec.get("kind", ""))
        if kind not in SUPPORTED_SPEC_KINDS:
            errors.append(f"{spec.get('id', '<missing>')} uses unsupported kind {kind}")
        if not isinstance(spec.get("version"), int) or int(spec.get("version", 0)) < 1:
            errors.append(f"{spec.get('id', '<missing>')} version must be a positive integer")
    for contract in registry.specs.get("task_contract", {}).values():
        for version in contract:
            validate_task_contract_spec(version, registry, errors)
    for chain in registry.specs.get("reasoning_chain", {}).values():
        for version in chain:
            validate_reasoning_chain_spec(version, errors)
    for taxonomy in registry.specs.get("failure_taxonomy", {}).values():
        for version in taxonomy:
            validate_failure_taxonomy_spec(version, errors)
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


def validate_task_contract_spec(
    contract: dict[str, Any],
    registry: SpecRegistry,
    errors: list[str],
) -> None:
    spec_id = str(contract.get("id", "<missing>"))
    missing_sections = sorted(TASK_CONTRACT_REQUIRED_SECTIONS - set(contract))
    if missing_sections:
        errors.append(f"{spec_id} missing contract sections: {', '.join(missing_sections)}")

    validate_field_map(contract.get("inputs"), f"{spec_id}.inputs", errors)
    validate_field_map(contract.get("outputs"), f"{spec_id}.outputs", errors)
    validate_positive_integer_map(
        contract.get("budgets"),
        f"{spec_id}.budgets",
        {"max_child_tasks", "max_model_calls", "max_retrieval_k", "max_wall_clock_seconds"},
        errors,
    )
    validate_string_list(contract.get("tools_allowed"), f"{spec_id}.tools_allowed", errors)

    retrieval_profile = contract.get("retrieval_profile")
    if not isinstance(retrieval_profile, dict):
        errors.append(f"{spec_id}.retrieval_profile must be an object")
    else:
        if not isinstance(retrieval_profile.get("id"), str) or not retrieval_profile.get("id"):
            errors.append(f"{spec_id}.retrieval_profile.id must be a non-empty string")
        threshold = retrieval_profile.get("min_score_threshold")
        if threshold is not None and not isinstance(threshold, (int, float)):
            errors.append(f"{spec_id}.retrieval_profile.min_score_threshold must be a number")

    chain = contract.get("chain")
    if not isinstance(chain, dict) or not isinstance(chain.get("id"), str) or not chain.get("id"):
        errors.append(f"{spec_id}.chain.id must be a non-empty string")
    elif chain["id"] not in registry.specs.get("reasoning_chain", {}):
        errors.append(f"{spec_id} references missing chain {chain['id']}")

    verification = contract.get("verification_profile")
    if not isinstance(verification, dict):
        errors.append(f"{spec_id}.verification_profile must be an object")
    else:
        for key in {"require_groundedness", "require_schema_valid"}:
            if key in verification and not isinstance(verification[key], bool):
                errors.append(f"{spec_id}.verification_profile.{key} must be boolean")
        require_min = verification.get("require_min_citations")
        if not isinstance(require_min, int) or require_min < 1:
            errors.append(f"{spec_id}.verification_profile.require_min_citations must be a positive integer")

    persistence = contract.get("persistence")
    if not isinstance(persistence, dict):
        errors.append(f"{spec_id}.persistence must be an object")
    else:
        for key in {"store_retrieval_candidates", "store_tool_io"}:
            if key in persistence and not isinstance(persistence[key], bool):
                errors.append(f"{spec_id}.persistence.{key} must be boolean")
        retention_days = persistence.get("retention_days_full_trace")
        if retention_days is not None and (not isinstance(retention_days, int) or retention_days < 1):
            errors.append(f"{spec_id}.persistence.retention_days_full_trace must be a positive integer")

    completion = contract.get("completion_condition")
    if not isinstance(completion, dict) or not isinstance(completion.get("all_of"), list) or not completion.get("all_of"):
        errors.append(f"{spec_id}.completion_condition.all_of must be a non-empty list")
    elif any(not isinstance(item, str) or not item for item in completion["all_of"]):
        errors.append(f"{spec_id}.completion_condition.all_of entries must be non-empty strings")


def validate_reasoning_chain_spec(chain: dict[str, Any], errors: list[str]) -> None:
    spec_id = str(chain.get("id", "<missing>"))
    steps = chain.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(f"{spec_id}.steps must be a non-empty list")
        return
    seen_step_ids: set[str] = set()
    for index, step in enumerate(steps):
        path = f"{spec_id}.steps[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{path} must be an object")
            continue
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id:
            errors.append(f"{path}.id must be a non-empty string")
        elif step_id in seen_step_ids:
            errors.append(f"{spec_id} has duplicate step id {step_id}")
        else:
            seen_step_ids.add(step_id)
        step_type = step.get("type")
        if step_type not in REASONING_CHAIN_STEP_TYPES:
            errors.append(f"{path}.type must be one of {', '.join(sorted(REASONING_CHAIN_STEP_TYPES))}")
        if step_type == "tool" and (not isinstance(step.get("tool_name"), str) or not step.get("tool_name")):
            errors.append(f"{path}.tool_name is required for tool steps")
        output_schema = step.get("output_schema")
        if output_schema is not None:
            validate_step_output_schema(output_schema, f"{path}.output_schema", errors)


def validate_failure_taxonomy_spec(taxonomy: dict[str, Any], errors: list[str]) -> None:
    spec_id = str(taxonomy.get("id", "<missing>"))
    enums = taxonomy.get("enums", {})
    if not isinstance(enums, dict):
        errors.append(f"{spec_id}.enums must be an object")
        enums = {}
    severities = enum_values(enums.get("severity"), f"{spec_id}.enums.severity", errors)
    actions = enum_values(enums.get("action"), f"{spec_id}.enums.action", errors)
    failures = taxonomy.get("failures")
    if not isinstance(failures, list) or not failures:
        errors.append(f"{spec_id}.failures must be a non-empty list")
        return

    seen_codes: set[str] = set()
    for item in failures:
        if not isinstance(item, dict):
            errors.append(f"{spec_id} has non-object failure rule")
            continue
        code = str(item.get("code", ""))
        if not code:
            errors.append(f"{spec_id} has failure without code")
        if code in seen_codes:
            errors.append(f"{spec_id} has duplicate failure code {code}")
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

    missing_runtime_codes = sorted(REQUIRED_FAILURE_CODES - seen_codes)
    if missing_runtime_codes:
        errors.append(f"{spec_id} missing runtime failure codes: {', '.join(missing_runtime_codes)}")


def validate_field_map(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or not value:
        errors.append(f"{path} must be a non-empty object")
        return
    for field_name, schema in value.items():
        field_path = f"{path}.{field_name}"
        if not isinstance(field_name, str) or not field_name:
            errors.append(f"{path} field names must be non-empty strings")
            continue
        if not isinstance(schema, dict):
            errors.append(f"{field_path} must be an object")
            continue
        field_type = schema.get("type")
        if field_type not in SUPPORTED_SCHEMA_TYPES:
            errors.append(f"{field_path}.type must be one of {', '.join(sorted(SUPPORTED_SCHEMA_TYPES))}")
        if "required" in schema and not isinstance(schema["required"], bool):
            errors.append(f"{field_path}.required must be boolean")


def validate_positive_integer_map(
    value: Any,
    path: str,
    required_keys: set[str],
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    missing = sorted(required_keys - set(value))
    if missing:
        errors.append(f"{path} missing keys: {', '.join(missing)}")
    for key in required_keys & set(value):
        if not isinstance(value[key], int) or value[key] < 0:
            errors.append(f"{path}.{key} must be a non-negative integer")


def validate_string_list(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{path} must be a non-empty list")
        return
    if any(not isinstance(item, str) or not item for item in value):
        errors.append(f"{path} entries must be non-empty strings")


def validate_step_output_schema(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    if value.get("type") != "object":
        errors.append(f"{path}.type must be object")
    required_keys = value.get("required_keys")
    if not isinstance(required_keys, list) or not required_keys:
        errors.append(f"{path}.required_keys must be a non-empty list")
    elif any(not isinstance(item, str) or not item for item in required_keys):
        errors.append(f"{path}.required_keys entries must be non-empty strings")


def enum_values(value: Any, path: str, errors: list[str]) -> set[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{path} must be a non-empty list")
        return set()
    results: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item:
            errors.append(f"{path} entries must be non-empty strings")
            continue
        if item in seen:
            errors.append(f"{path} has duplicate value {item}")
            continue
        seen.add(item)
        results.append(item)
    return set(results)


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
        schema_errors: list[str] = []
        schema_valid = False
        claim_plan: dict[str, Any] | None = None
        claim_plan_attempts: list[dict[str, Any]] = []
        repair_errors: list[str] | None = None
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
                    repair_errors=repair_errors,
                )
                raw_output = synthesis_result.output
                synthesis_metadata = {
                    **synthesis_result.metadata,
                    "duration_seconds": round(time.perf_counter() - synthesis_started, 3),
                    "mode": synthesis,
                }
                plan_errors: list[str] = []
                plan_valid = True
                valid_span_refs = True
                if synthesis == "local":
                    claim_plan = raw_output if isinstance(raw_output, dict) else {}
                    plan_errors = claim_plan_errors(
                        claim_plan,
                        chunks,
                        min_citations=min_citations(contract),
                    )
                    valid_span_refs = claim_plan_has_valid_span_refs(plan_errors)
                    plan_valid = not plan_errors
                    if plan_valid:
                        outputs = render_claim_plan_outputs(
                            claim_plan,
                            chunks,
                            user_query=user_query,
                        )
                    else:
                        outputs = {}
                    schema_errors = output_schema_errors(outputs, contract=contract, chain=chain) if plan_valid else []
                else:
                    outputs = raw_output
                    schema_errors = output_schema_errors(outputs, contract=contract, chain=chain)
                schema_valid = not schema_errors and plan_valid
                failure_code = None
                if not plan_valid:
                    failure_code = "CLAIM_PLAN_INVALID"
                    repair_errors = plan_errors
                elif not schema_valid:
                    failure_code = "OUTPUT_SCHEMA_INVALID"
                    repair_errors = schema_errors
                claim_plan_attempt = {
                    "attempt": attempt,
                    "claim_count": len(claim_plan.get("claims", [])) if isinstance(claim_plan, dict) else 0,
                    "plan_errors": plan_errors,
                    "plan_valid": plan_valid,
                    "refusal": claim_plan.get("refusal") if isinstance(claim_plan, dict) else None,
                    "valid_span_refs": valid_span_refs,
                }
                claim_plan_attempts.append(claim_plan_attempt)
                synthesis_attempts.append(
                    {
                        **claim_plan_attempt,
                        "attempt": attempt,
                        "failure_code": failure_code,
                        "provider": synthesis_metadata.get("provider"),
                        "schema_errors": schema_errors,
                        "schema_valid": schema_valid,
                        "status": "ok" if schema_valid else "failed",
                    }
                )
                if schema_valid:
                    break
                if (
                    attempt < max_synthesis_attempts
                    and failure_code is not None
                    and "retry" in failure_response_actions(registry, failure_code)
                ):
                    synthesis_failure_actions.extend(
                        record_failure_actions(
                            failure_actions,
                            registry,
                            step_id="s3_synthesize",
                            failure_code=failure_code,
                            status="applied",
                            reason="Synthesis output failed validation; retrying once with repair feedback.",
                            attempt=attempt,
                            only_actions={"retry"},
                        )
                    )
                    attempt += 1
                    continue
                severity = "high" if failure_code == "CLAIM_PLAN_INVALID" else "critical"
                details = {"outputs": outputs, "schema_errors": schema_errors}
                if failure_code == "CLAIM_PLAN_INVALID":
                    details = {"claim_plan": claim_plan or {}, "plan_errors": plan_errors}
                failures.append(failure("s3_synthesize", str(failure_code), severity, details))
                if failure_code is not None and "abort" in failure_response_actions(registry, failure_code):
                    synthesis_failure_actions.extend(
                        record_failure_actions(
                            failure_actions,
                            registry,
                            step_id="s3_synthesize",
                            failure_code=failure_code,
                            status="applied",
                            reason="Synthesis output remained invalid after retry budget was exhausted.",
                            attempt=attempt,
                            only_actions={"abort"},
                        )
                    )
                break
            except StructuredSynthesisError as exc:
                outputs = {}
                schema_errors = [str(exc)]
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
                        "plan_errors": [],
                        "plan_valid": False,
                        "provider": synthesis_metadata.get("provider"),
                        "schema_valid": False,
                        "status": "failed",
                        "valid_span_refs": False,
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
                "claim_plan": claim_plan or {},
                "claim_plan_attempts": claim_plan_attempts,
                "failure_actions": synthesis_failure_actions,
                "first_pass_plan_valid": synthesis_attempts[0]["plan_valid"] if synthesis_attempts else False,
                "first_pass_schema_valid": synthesis_attempts[0]["schema_valid"] if synthesis_attempts else False,
                "first_pass_valid_span_refs": synthesis_attempts[0]["valid_span_refs"] if synthesis_attempts else False,
                "repaired_success": len(synthesis_attempts) > 1 and schema_valid,
                "schema_errors": schema_errors,
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
        first_attempt = synthesis_attempts[0] if synthesis_attempts else {}
        metrics = {
            "failure_action_count": len(failure_actions),
            "failure_count": len(failures),
            "retrieval_fallback_hit_count": retrieval_fallback_hit_count,
            "retrieval_fallback_used": retrieval_fallback_used,
            "retrieval_hit_count": len(chunks),
            "retrieval_primary_hit_count": retrieval_primary_hit_count,
            "synthesis_attempt_count": len(synthesis_attempts),
            "synthesis_first_pass_failure_code": first_attempt.get("failure_code"),
            "synthesis_first_pass_plan_valid": first_attempt.get("plan_valid"),
            "synthesis_first_pass_schema_valid": first_attempt.get("schema_valid"),
            "synthesis_first_pass_valid_span_refs": first_attempt.get("valid_span_refs"),
            "synthesis": synthesis,
            "synthesis_model": synthesis_metadata.get("model"),
            "synthesis_provider": synthesis_metadata.get("provider"),
            "synthesis_repaired_success": len(synthesis_attempts) > 1 and schema_valid,
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
    if synthesis == "local":
        return LocalStructuredSynthesisAdapter()
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


def diff_harness_runs(
    base_run_id: str,
    head_run_id: str,
    harness_db: Path = DEFAULT_HARNESS_DB,
    *,
    limit: int = 25,
) -> dict[str, Any]:
    base = get_harness_run(base_run_id, harness_db)
    head = get_harness_run(head_run_id, harness_db)
    base_run = base["run"]
    head_run = head["run"]
    status_change = {
        "base": base_run["status"],
        "changed": base_run["status"] != head_run["status"],
        "head": head_run["status"],
    }
    task_change = compare_fields(
        base_run,
        head_run,
        ["task_id", "task_version", "chain_id", "chain_version"],
    )
    metrics = diff_mapping(base_run.get("metrics", {}), head_run.get("metrics", {}), limit=limit)
    steps = diff_steps(base["steps"], head["steps"], limit=limit)
    retrieval = diff_retrieval_candidates(
        base["retrieval_candidates"],
        head["retrieval_candidates"],
        limit=limit,
    )
    citations = diff_citations(
        base_run.get("outputs", {}).get("citations", []),
        head_run.get("outputs", {}).get("citations", []),
        limit=limit,
    )
    failures = diff_failures(base["failures"], head["failures"], limit=limit)
    outputs = diff_outputs(base, head)
    changed = any(
        [
            status_change["changed"],
            task_change["changed"],
            metrics["changed"],
            steps["changed"],
            retrieval["changed"],
            citations["changed"],
            failures["changed"],
            outputs["changed"],
        ]
    )
    return {
        "citations": citations,
        "failures": failures,
        "harness_db": str(harness_db),
        "metrics": metrics,
        "outputs": outputs,
        "retrieval": retrieval,
        "status_change": status_change,
        "steps": steps,
        "summary": {
            "base_run_id": base_run_id,
            "base_status": base_run["status"],
            "changed": changed,
            "head_run_id": head_run_id,
            "head_status": head_run["status"],
            "risk_flags": diff_risk_flags(
                status_change=status_change,
                retrieval=retrieval,
                failures=failures,
                outputs=outputs,
            ),
        },
        "task_change": task_change,
    }


def compare_fields(base: dict[str, Any], head: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    changed = []
    unchanged = []
    for field in fields:
        item = {"field": field, "base": base.get(field), "head": head.get(field)}
        if item["base"] == item["head"]:
            unchanged.append(field)
        else:
            changed.append(item)
    return {
        "changed": bool(changed),
        "changed_fields": changed,
        "unchanged_fields": unchanged,
    }


def diff_mapping(base: dict[str, Any], head: dict[str, Any], *, limit: int) -> dict[str, Any]:
    base_keys = set(base)
    head_keys = set(head)
    added = [{"key": key, "head": head[key]} for key in sorted(head_keys - base_keys)]
    removed = [{"base": base[key], "key": key} for key in sorted(base_keys - head_keys)]
    changed = []
    unchanged_count = 0
    for key in sorted(base_keys & head_keys):
        if base[key] == head[key]:
            unchanged_count += 1
            continue
        item = {"base": base[key], "head": head[key], "key": key}
        if numeric(base[key]) and numeric(head[key]):
            item["delta"] = head[key] - base[key]
        changed.append(item)
    return {
        "added": clip(added, limit),
        "added_count": len(added),
        "changed": bool(added or removed or changed),
        "changed_items": clip(changed, limit),
        "changed_count": len(changed),
        "removed": clip(removed, limit),
        "removed_count": len(removed),
        "truncated": truncated(added, removed, changed, limit=limit),
        "unchanged_count": unchanged_count,
    }


def diff_steps(base_steps: list[dict[str, Any]], head_steps: list[dict[str, Any]], *, limit: int) -> dict[str, Any]:
    base = keyed(base_steps, "step_id")
    head = keyed(head_steps, "step_id")
    base_keys = set(base)
    head_keys = set(head)
    added = [step_summary(head[key]) for key in sorted(head_keys - base_keys)]
    removed = [step_summary(base[key]) for key in sorted(base_keys - head_keys)]
    changed = []
    unchanged_count = 0
    for key in sorted(base_keys & head_keys):
        base_summary = step_summary(base[key])
        head_summary = step_summary(head[key])
        changed_fields = [
            field
            for field in [
                "status",
                "step_type",
                "tool_name",
                "input_hash",
                "output_hash",
                "schema_errors",
                "failure_action_statuses",
                "synthesis_attempt_statuses",
            ]
            if base_summary.get(field) != head_summary.get(field)
        ]
        if not changed_fields:
            unchanged_count += 1
            continue
        changed.append(
            {
                "base": base_summary,
                "changed_fields": changed_fields,
                "head": head_summary,
                "step_id": key,
            }
        )
    return {
        "added": clip(added, limit),
        "added_count": len(added),
        "changed": bool(added or removed or changed),
        "changed_count": len(changed),
        "changed_steps": clip(changed, limit),
        "removed": clip(removed, limit),
        "removed_count": len(removed),
        "truncated": truncated(added, removed, changed, limit=limit),
        "unchanged_count": unchanged_count,
    }


def step_summary(step: dict[str, Any]) -> dict[str, Any]:
    output = step.get("output", {})
    return {
        "failure_action_statuses": failure_action_statuses(output),
        "input_hash": stable_hash(step.get("input", {})),
        "output_hash": stable_hash(output),
        "schema_errors": output.get("schema_errors", []),
        "status": step.get("status"),
        "step_id": step.get("step_id"),
        "step_type": step.get("step_type"),
        "synthesis_attempt_statuses": synthesis_attempt_statuses(output),
        "tool_name": step.get("tool_name"),
    }


def failure_action_statuses(output: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "action": item.get("action"),
            "source_failure_code": item.get("source_failure_code"),
            "status": item.get("status"),
        }
        for item in output.get("failure_actions", [])
    ]


def synthesis_attempt_statuses(output: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "attempt": item.get("attempt"),
            "provider": item.get("provider"),
            "schema_errors": item.get("schema_errors", []),
            "status": item.get("status"),
        }
        for item in output.get("synthesis_attempts", [])
    ]


def diff_retrieval_candidates(
    base_candidates: list[dict[str, Any]],
    head_candidates: list[dict[str, Any]],
    *,
    limit: int,
) -> dict[str, Any]:
    base = keyed(base_candidates, "chunk_id")
    head = keyed(head_candidates, "chunk_id")
    base_keys = set(base)
    head_keys = set(head)
    added = [candidate_summary(head[key]) for key in sorted(head_keys - base_keys)]
    removed = [candidate_summary(base[key]) for key in sorted(base_keys - head_keys)]
    changed = []
    for key in sorted(base_keys & head_keys):
        base_summary = candidate_summary(base[key])
        head_summary = candidate_summary(head[key])
        changed_fields = [
            field
            for field in ["rank", "score", "method", "path", "step_id", "heading"]
            if base_summary.get(field) != head_summary.get(field)
        ]
        if changed_fields:
            item = {
                "base": base_summary,
                "changed_fields": changed_fields,
                "chunk_id": key,
                "head": head_summary,
            }
            if "score" in changed_fields and numeric(base_summary["score"]) and numeric(head_summary["score"]):
                item["score_delta"] = head_summary["score"] - base_summary["score"]
            changed.append(item)
    top_path = {
        "base": base_candidates[0]["path"] if base_candidates else None,
        "changed": (base_candidates[0]["path"] if base_candidates else None)
        != (head_candidates[0]["path"] if head_candidates else None),
        "head": head_candidates[0]["path"] if head_candidates else None,
    }
    return {
        "added": clip(added, limit),
        "added_count": len(added),
        "base_count": len(base_candidates),
        "changed": bool(added or removed or changed or top_path["changed"]),
        "changed_candidates": clip(changed, limit),
        "changed_count": len(changed),
        "head_count": len(head_candidates),
        "overlap_count": len(base_keys & head_keys),
        "removed": clip(removed, limit),
        "removed_count": len(removed),
        "top_path_change": top_path,
        "truncated": truncated(added, removed, changed, limit=limit),
    }


def candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": candidate.get("chunk_id"),
        "heading": candidate.get("heading"),
        "method": candidate.get("method"),
        "path": candidate.get("path"),
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
        "step_id": candidate.get("step_id"),
    }


def diff_citations(base_citations: list[Any], head_citations: list[Any], *, limit: int) -> dict[str, Any]:
    base = keyed_citations(base_citations)
    head = keyed_citations(head_citations)
    base_keys = set(base)
    head_keys = set(head)
    added = [citation_summary(head[key]) for key in sorted(head_keys - base_keys)]
    removed = [citation_summary(base[key]) for key in sorted(base_keys - head_keys)]
    return {
        "added": clip(added, limit),
        "added_count": len(added),
        "base_count": len(base_citations) if isinstance(base_citations, list) else 0,
        "changed": bool(added or removed),
        "head_count": len(head_citations) if isinstance(head_citations, list) else 0,
        "removed": clip(removed, limit),
        "removed_count": len(removed),
        "truncated": truncated(added, removed, limit=limit),
    }


def keyed_citations(citations: list[Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(citations, list):
        return {}
    result = {}
    for index, item in enumerate(citations):
        if not isinstance(item, dict):
            key = f"non_object:{index}:{stable_hash(item)}"
            result[key] = {"value": item}
            continue
        quote_hash = stable_hash(item.get("quote", ""))
        key = f"{item.get('artifact_id')}|{item.get('chunk_id')}|{quote_hash}"
        result[key] = item
    return result


def citation_summary(citation: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": citation.get("artifact_id"),
        "chunk_id": citation.get("chunk_id"),
        "quote_hash": stable_hash(citation.get("quote", "")),
        "relevance_note": citation.get("relevance_note"),
    }


def diff_failures(base_failures: list[dict[str, Any]], head_failures: list[dict[str, Any]], *, limit: int) -> dict[str, Any]:
    base = keyed_failures(base_failures)
    head = keyed_failures(head_failures)
    base_keys = set(base)
    head_keys = set(head)
    added = [failure_summary(head[key]) for key in sorted(head_keys - base_keys)]
    removed = [failure_summary(base[key]) for key in sorted(base_keys - head_keys)]
    return {
        "added": clip(added, limit),
        "added_count": len(added),
        "base_count": len(base_failures),
        "changed": bool(added or removed),
        "head_count": len(head_failures),
        "removed": clip(removed, limit),
        "removed_count": len(removed),
        "truncated": truncated(added, removed, limit=limit),
    }


def keyed_failures(failures: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in failures:
        key = "|".join(
            [
                str(item.get("step_id")),
                str(item.get("failure_code")),
                str(item.get("severity")),
                stable_hash(item.get("details", {})),
            ]
        )
        result[key] = item
    return result


def failure_summary(failure: dict[str, Any]) -> dict[str, Any]:
    return {
        "details_hash": stable_hash(failure.get("details", {})),
        "failure_code": failure.get("failure_code"),
        "severity": failure.get("severity"),
        "step_id": failure.get("step_id"),
    }


def diff_outputs(base: dict[str, Any], head: dict[str, Any]) -> dict[str, Any]:
    base_outputs = base["run"].get("outputs", {})
    head_outputs = head["run"].get("outputs", {})
    base_answer = base_outputs.get("answer_markdown", "")
    head_answer = head_outputs.get("answer_markdown", "")
    base_schema_errors = collect_schema_errors(base)
    head_schema_errors = collect_schema_errors(head)
    base_citation_count = citation_count(base_outputs.get("citations", []))
    head_citation_count = citation_count(head_outputs.get("citations", []))
    answer_changed = base_answer != head_answer
    citation_count_delta = head_citation_count - base_citation_count
    schema_errors_changed = base_schema_errors != head_schema_errors
    return {
        "answer_changed": answer_changed,
        "answer_hashes": {
            "base": stable_hash(base_answer),
            "head": stable_hash(head_answer),
        },
        "changed": answer_changed or citation_count_delta != 0 or schema_errors_changed,
        "citation_count": {
            "base": base_citation_count,
            "delta": citation_count_delta,
            "head": head_citation_count,
        },
        "schema_error_changes": {
            "base": base_schema_errors,
            "changed": schema_errors_changed,
            "head": head_schema_errors,
        },
    }


def collect_schema_errors(trace: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for step in trace.get("steps", []):
        output = step.get("output", {})
        if output.get("schema_errors"):
            items.append({"errors": output["schema_errors"], "step_id": step.get("step_id")})
        for attempt in output.get("synthesis_attempts", []):
            if attempt.get("schema_errors"):
                items.append(
                    {
                        "attempt": attempt.get("attempt"),
                        "errors": attempt["schema_errors"],
                        "step_id": step.get("step_id"),
                    }
                )
    return items


def diff_risk_flags(
    *,
    status_change: dict[str, Any],
    retrieval: dict[str, Any],
    failures: dict[str, Any],
    outputs: dict[str, Any],
) -> list[str]:
    flags = []
    if status_change["changed"]:
        flags.append("status_changed")
    if status_change["base"] == "pass" and status_change["head"] == "fail":
        flags.append("status_regressed")
    if failures["added_count"]:
        flags.append("new_failures")
    if outputs["schema_error_changes"]["changed"]:
        flags.append("schema_errors_changed")
    if retrieval["top_path_change"]["changed"]:
        flags.append("top_retrieval_path_changed")
    if outputs["answer_changed"]:
        flags.append("answer_changed")
    return flags


def keyed(items: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    return {str(item[field]): item for item in items}


def citation_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def stable_hash(value: Any) -> str:
    return digest(json.dumps(value, sort_keys=True, default=str))


def clip(items: list[Any], limit: int) -> list[Any]:
    return items[: max(0, limit)]


def truncated(*groups: list[Any], limit: int) -> bool:
    return any(len(group) > limit for group in groups)


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


def claim_plan_errors(
    claim_plan: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    min_citations: int,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(claim_plan, dict):
        return ["claim plan must be an object"]
    refusal = claim_plan.get("refusal")
    refusal_reason = claim_plan.get("refusal_reason")
    claims = claim_plan.get("claims")
    if not isinstance(refusal, bool):
        errors.append("refusal must be a boolean")
    if not isinstance(refusal_reason, str):
        errors.append("refusal_reason must be a string")
    if not isinstance(claims, list):
        return errors + ["claims must be an array"]

    chunk_ids = {chunk["chunk_id"] for chunk in chunks}
    claim_ids: set[str] = set()
    unique_span_ids: set[str] = set()
    for index, claim in enumerate(claims):
        prefix = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{prefix} must be an object")
            continue
        claim_id = claim.get("claim_id")
        text = claim.get("text")
        span_ids = claim.get("span_ids")
        if not isinstance(claim_id, str) or not claim_id.strip():
            errors.append(f"{prefix}.claim_id must be a non-empty string")
        elif claim_id in claim_ids:
            errors.append(f"duplicate claim_id {claim_id}")
        else:
            claim_ids.add(claim_id)
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{prefix}.text must be a non-empty string")
        elif "\n" in text or text.lstrip().startswith(("-", "*", "#")):
            errors.append(f"{prefix}.text must be a single atomic claim")
        if not isinstance(span_ids, list) or not span_ids:
            errors.append(f"{prefix}.span_ids must be a non-empty array")
            continue
        for span_index, span_id in enumerate(span_ids):
            if not isinstance(span_id, str) or not span_id.strip():
                errors.append(f"{prefix}.span_ids[{span_index}] must be a non-empty string")
                continue
            if span_id not in chunk_ids:
                errors.append(f"unknown span_id {span_id}")
                continue
            unique_span_ids.add(span_id)

    if refusal is True:
        if claims:
            errors.append("claims must be empty when refusal is true")
        if not isinstance(refusal_reason, str) or not refusal_reason.strip():
            errors.append("refusal_reason must be non-empty when refusal is true")
    elif refusal is False:
        if not claims:
            errors.append("claims must be non-empty when refusal is false")
        if isinstance(refusal_reason, str) and refusal_reason.strip():
            errors.append("refusal_reason must be empty when refusal is false")
        if len(unique_span_ids) < min_citations:
            errors.append(f"expected at least {min_citations} unique supporting spans")
    return errors


def claim_plan_has_valid_span_refs(errors: list[str]) -> bool:
    return not any("span_id" in error or "supporting spans" in error for error in errors)


def render_claim_plan_outputs(
    claim_plan: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    user_query: str,
) -> dict[str, Any]:
    if claim_plan.get("refusal") is True:
        reason = str(claim_plan.get("refusal_reason", "")).strip()
        answer = f"Insufficient grounded wiki evidence was found for `{user_query}`."
        if reason:
            answer = f"{answer}\n\n{reason}"
        return {"answer_markdown": answer, "citations": []}

    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    claims = [claim for claim in claim_plan.get("claims", []) if isinstance(claim, dict)]
    answer_lines = [str(claim["text"]).strip() for claim in claims if str(claim.get("text", "")).strip()]
    citations: list[dict[str, Any]] = []
    seen_span_ids: set[str] = set()
    for claim in claims:
        claim_text = str(claim.get("text", "")).strip()
        for span_id in claim.get("span_ids", []):
            if span_id in seen_span_ids:
                continue
            seen_span_ids.add(span_id)
            chunk = chunk_by_id[span_id]
            citations.append(
                {
                    "artifact_id": chunk["artifact_id"],
                    "chunk_id": chunk["chunk_id"],
                    "quote": quote_from_text(chunk["text"]),
                    "relevance_note": f"Supports claim: {claim_text}",
                }
            )
    answer = "\n".join(f"- {line}" for line in answer_lines) if len(answer_lines) > 1 else "".join(answer_lines)
    return {"answer_markdown": answer, "citations": citations}


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


def output_schema_valid(
    outputs: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
    chain: dict[str, Any] | None = None,
) -> bool:
    return not output_schema_errors(outputs, contract=contract, chain=chain)


def output_schema_errors(
    outputs: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
    chain: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(outputs, dict):
        return ["outputs must be an object"]

    if chain is not None:
        output_schema = synthesis_step_output_schema(chain)
        for key in output_schema.get("required_keys", []):
            if key not in outputs:
                errors.append(f"chain output missing required key {key}")

    if contract is not None:
        validate_runtime_field_map(outputs, contract.get("outputs"), "contract.outputs", errors)
    else:
        if not isinstance(outputs.get("answer_markdown"), str):
            errors.append("answer_markdown must be a string")
        if not isinstance(outputs.get("citations"), list):
            errors.append("citations must be an array")

    citations = outputs.get("citations")
    if not isinstance(citations, list):
        return errors
    required = {"artifact_id", "chunk_id", "quote", "relevance_note"}
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            errors.append(f"citations[{index}] must be an object")
            continue
        missing = sorted(required - set(citation))
        if missing:
            errors.append(f"citations[{index}] missing keys: {', '.join(missing)}")
            continue
        for key in sorted(required):
            if not isinstance(citation[key], str):
                errors.append(f"citations[{index}].{key} must be a string")
    return errors


def synthesis_step_output_schema(chain: dict[str, Any]) -> dict[str, Any]:
    steps = chain.get("steps", [])
    if not isinstance(steps, list):
        return {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("id") == "s3_synthesize" or step.get("tool_name") == "llm.structured_synthesis":
            output_schema = step.get("output_schema")
            return output_schema if isinstance(output_schema, dict) else {}
    return {}


def validate_runtime_field_map(
    outputs: dict[str, Any],
    field_map: Any,
    path: str,
    errors: list[str],
) -> None:
    if not isinstance(field_map, dict):
        errors.append(f"{path} must be an object")
        return
    for field, schema in field_map.items():
        if not isinstance(schema, dict):
            errors.append(f"{path}.{field} must be an object")
            continue
        required = bool(schema.get("required", False))
        if field not in outputs:
            if required:
                errors.append(f"{field} is required")
            continue
        expected_type = schema.get("type")
        if not runtime_type_matches(outputs[field], str(expected_type)):
            errors.append(f"{field} must be {expected_type}")


def runtime_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type in {"array", "list"}:
        return isinstance(value, list)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "string":
        return isinstance(value, str)
    return False


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
