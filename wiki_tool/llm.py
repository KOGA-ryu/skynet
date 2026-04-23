from __future__ import annotations

from dataclasses import dataclass
import json
import os
import shlex
import subprocess
from typing import Any
from urllib import error, request


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_LOCAL_LLM_COMMAND = "qwen-local"
DEFAULT_LOCAL_TIMEOUT_SECONDS = 120.0


class StructuredSynthesisError(RuntimeError):
    def __init__(self, message: str, *, failure_code: str = "LLM_SYNTHESIS_ERROR") -> None:
        super().__init__(message)
        self.failure_code = failure_code


@dataclass(frozen=True)
class SynthesisResult:
    output: dict[str, Any]
    metadata: dict[str, Any]


class StructuredSynthesisAdapter:
    provider = "deterministic"

    def synthesize(
        self,
        *,
        user_query: str,
        chunks: list[dict[str, Any]],
        min_citations: int,
        output_schema: dict[str, Any],
        repair_errors: list[str] | None = None,
    ) -> SynthesisResult:
        raise NotImplementedError


class DeterministicSynthesisAdapter(StructuredSynthesisAdapter):
    provider = "deterministic"

    def synthesize(
        self,
        *,
        user_query: str,
        chunks: list[dict[str, Any]],
        min_citations: int,
        output_schema: dict[str, Any],
        repair_errors: list[str] | None = None,
    ) -> SynthesisResult:
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
        return SynthesisResult(
            output={"answer_markdown": answer, "citations": citations},
            metadata={"model": None, "provider": self.provider, "token_usage": None},
        )


class OpenAIStructuredSynthesisAdapter(StructuredSynthesisAdapter):
    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        endpoint: str = "https://api.openai.com/v1/responses",
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("WIKI_OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def synthesize(
        self,
        *,
        user_query: str,
        chunks: list[dict[str, Any]],
        min_citations: int,
        output_schema: dict[str, Any],
        repair_errors: list[str] | None = None,
    ) -> SynthesisResult:
        if not self.api_key:
            raise StructuredSynthesisError(
                "OPENAI_API_KEY is required for openai synthesis",
                failure_code="LLM_PROVIDER_CONFIG_MISSING",
            )
        payload = {
            "input": openai_prompt(user_query=user_query, chunks=chunks, min_citations=min_citations),
            "model": self.model,
            "text": {
                "format": {
                    "name": "wiki_answer_with_citations",
                    "schema": output_schema,
                    "strict": True,
                    "type": "json_schema",
                }
            },
        }
        req = request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise StructuredSynthesisError(
                f"OpenAI synthesis request failed with HTTP {exc.code}: {details}",
            ) from exc
        except OSError as exc:
            raise StructuredSynthesisError(f"OpenAI synthesis request failed: {exc}") from exc

        data = json.loads(raw)
        text = extract_response_text(data)
        try:
            output = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StructuredSynthesisError(f"OpenAI response did not contain valid JSON: {exc}") from exc
        return SynthesisResult(
            output=output,
            metadata={
                "model": self.model,
                "provider": self.provider,
                "response_id": data.get("id"),
                "token_usage": data.get("usage"),
            },
        )


class LocalStructuredSynthesisAdapter(StructuredSynthesisAdapter):
    provider = "local"

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        env_command = os.environ.get("WIKI_LOCAL_LLM_COMMAND", DEFAULT_LOCAL_LLM_COMMAND)
        self.command = command if command is not None else shlex.split(env_command)
        if timeout_seconds is None:
            timeout_seconds = env_float("WIKI_LOCAL_TIMEOUT_SECONDS", DEFAULT_LOCAL_TIMEOUT_SECONDS)
        self.timeout_seconds = timeout_seconds

    def synthesize(
        self,
        *,
        user_query: str,
        chunks: list[dict[str, Any]],
        min_citations: int,
        output_schema: dict[str, Any],
        repair_errors: list[str] | None = None,
    ) -> SynthesisResult:
        if not self.command:
            raise StructuredSynthesisError(
                "WIKI_LOCAL_LLM_COMMAND did not resolve to an executable command",
                failure_code="LLM_PROVIDER_CONFIG_MISSING",
            )

        prompt = local_prompt(
            user_query=user_query,
            chunks=chunks,
            min_citations=min_citations,
            repair_errors=repair_errors,
        )
        try:
            response = subprocess.run(
                [*self.command, prompt],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise StructuredSynthesisError(
                f"Local synthesis command not found: {self.command[0]}",
                failure_code="LLM_PROVIDER_CONFIG_MISSING",
            ) from exc
        except OSError as exc:
            raise StructuredSynthesisError(f"Local synthesis request failed: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise StructuredSynthesisError(
                f"Local synthesis timed out after {self.timeout_seconds:.0f}s",
            ) from exc

        if response.returncode != 0:
            stderr = (response.stderr or "").strip()
            stdout = (response.stdout or "").strip()
            details = stderr or stdout or f"exit status {response.returncode}"
            raise StructuredSynthesisError(f"Local synthesis command failed: {details}")

        raw_text = response.stdout or ""
        json_text = extract_json_object(raw_text)
        try:
            output = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise StructuredSynthesisError(f"Local synthesis response did not contain valid JSON: {exc}") from exc
        return SynthesisResult(
            output=output,
            metadata={
                "command": self.command,
                "model": "local",
                "provider": self.provider,
                "token_usage": None,
            },
        )


def synthesis_output_schema() -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": {
            "answer_markdown": {"type": "string"},
            "citations": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "chunk_id": {"type": "string"},
                        "quote": {"type": "string"},
                        "relevance_note": {"type": "string"},
                    },
                    "required": ["artifact_id", "chunk_id", "quote", "relevance_note"],
                    "type": "object",
                },
                "type": "array",
            },
        },
        "required": ["answer_markdown", "citations"],
        "type": "object",
    }


def local_claim_plan_schema() -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": {
            "claims": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "claim_id": {"type": "string"},
                        "span_ids": {
                            "items": {"type": "string"},
                            "type": "array",
                        },
                        "text": {"type": "string"},
                    },
                    "required": ["claim_id", "text", "span_ids"],
                    "type": "object",
                },
                "type": "array",
            },
            "refusal": {"type": "boolean"},
            "refusal_reason": {"type": "string"},
        },
        "required": ["refusal", "refusal_reason", "claims"],
        "type": "object",
    }


def openai_prompt(*, user_query: str, chunks: list[dict[str, Any]], min_citations: int) -> str:
    evidence = [
        {
            "artifact_id": chunk["artifact_id"],
            "chunk_id": chunk["chunk_id"],
            "heading": chunk.get("heading"),
            "path": chunk["path"],
            "start_line": chunk.get("start_line"),
            "end_line": chunk.get("end_line"),
            "text": chunk["text"],
        }
        for chunk in chunks
    ]
    return "\n".join(
        [
            "Answer the wiki query using only the provided evidence chunks.",
            "Return JSON that matches the provided schema.",
            f"Minimum citations required: {min_citations}.",
            "Each citation quote must be an exact substring of the cited chunk text.",
            "",
            f"User query: {user_query}",
            "",
            "Evidence chunks JSON:",
            json.dumps(evidence, indent=2, sort_keys=True),
        ]
    )


def local_prompt(
    *,
    user_query: str,
    chunks: list[dict[str, Any]],
    min_citations: int,
    repair_errors: list[str] | None = None,
) -> str:
    evidence_budget = max(min_citations, min(4, len(chunks)))
    evidence = [
        {
            "artifact_id": chunk["artifact_id"],
            "span_id": chunk["chunk_id"],
            "heading": chunk.get("heading"),
            "path": chunk["path"],
            "start_line": chunk.get("start_line"),
            "end_line": chunk.get("end_line"),
            "text": chunk["text"],
        }
        for chunk in chunks[:evidence_budget]
    ]
    example = {
        "claims": [
            {
                "claim_id": "c1",
                "span_ids": [evidence[0]["span_id"]] if evidence else ["span:example"],
                "text": "Atomic supported claim text.",
            }
        ],
        "refusal": False,
        "refusal_reason": "",
    }
    compact_evidence = json.dumps(evidence, separators=(",", ":"), sort_keys=True)
    compact_example = json.dumps(example, separators=(",", ":"), sort_keys=True)
    lines = [
        "You are a local wiki claim planner.",
        "Use only the evidence spans below.",
        "Return exactly one JSON object and nothing else.",
        f"Minimum unique supporting spans required: {min_citations}.",
        "Do not write markdown, quotes, or final citations.",
        "Return only atomic claims tied to span_ids from the evidence.",
        "If support is insufficient, set refusal=true, claims=[] and explain briefly in refusal_reason.",
        "Do not wrap the JSON in markdown fences.",
        "Required top-level keys: refusal, refusal_reason, claims.",
        "Each claim object must have: claim_id, text, span_ids.",
        "",
        f"User query: {user_query}",
        "",
        "Example JSON shape:",
        compact_example,
    ]
    if repair_errors:
        lines.extend(
            [
                "",
                "The previous response failed validation. Fix these issues exactly:",
                json.dumps(repair_errors, separators=(",", ":"), sort_keys=True),
            ]
        )
    lines.extend(
        [
            "",
            "Evidence spans JSON (compact):",
            compact_evidence,
        ]
    )
    return "\n".join(lines)


def extract_response_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    texts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                texts.append(content["text"])
    if texts:
        return "\n".join(texts)
    raise StructuredSynthesisError("OpenAI response did not contain output text")


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def extract_json_object(text: str) -> str:
    stripped = strip_code_fences(text.strip())
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            json.loads(stripped)
            return stripped
        except json.JSONDecodeError:
            pass
    if "{" not in stripped:
        raise StructuredSynthesisError("Local synthesis response did not contain a JSON object")
    last_valid: str | None = None
    last_preferred: str | None = None
    for start in [index for index, char in enumerate(stripped) if char == "{"]:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = stripped[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        last_valid = candidate
                        if {"answer_markdown", "citations"} <= set(parsed):
                            last_preferred = candidate
                        if {"claims", "refusal", "refusal_reason"} <= set(parsed):
                            last_preferred = candidate
                    break
    if last_preferred is not None:
        return last_preferred
    if last_valid is not None:
        return last_valid
    raise StructuredSynthesisError("Local synthesis response contained an unterminated JSON object")


def strip_code_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def quote_from_text(text: str, *, max_words: int = 20) -> str:
    words = " ".join(text.split()).split()
    return " ".join(words[:max_words])
