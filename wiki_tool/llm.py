from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import error, request


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


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


def quote_from_text(text: str, *, max_words: int = 20) -> str:
    words = " ".join(text.split()).split()
    return " ".join(words[:max_words])
