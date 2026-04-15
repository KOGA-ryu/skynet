from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from wiki_tool.catalog import (
    DEFAULT_DB,
    audit_summary,
    find_references,
    get_headings,
    query_catalog,
)


DEFAULT_API_TRACE = Path("state/api_traces.jsonl")
JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SEARCH_DEFAULT_LIMIT = 10
SEARCH_MAX_LIMIT = 25
LIST_DEFAULT_LIMIT = 100
LIST_MAX_LIMIT = 250

METHODS: dict[str, dict[str, Any]] = {
    "api.methods": {
        "description": "List supported JSON-RPC methods and bounded parameter defaults.",
        "params": {},
    },
    "symbol.search": {
        "description": "Search wiki symbols and return bounded symbol handles.",
        "params": {"query": "string", "limit": "integer optional"},
        "default_limit": SEARCH_DEFAULT_LIMIT,
        "max_limit": SEARCH_MAX_LIMIT,
    },
    "span.searchText": {
        "description": "Search heading spans and return bounded span handles with snippets.",
        "params": {"query": "string", "limit": "integer optional"},
        "default_limit": SEARCH_DEFAULT_LIMIT,
        "max_limit": SEARCH_MAX_LIMIT,
    },
    "span.listHeadings": {
        "description": "List heading handles for a wiki path.",
        "params": {"path": "string", "limit": "integer optional"},
        "default_limit": LIST_DEFAULT_LIMIT,
        "max_limit": LIST_MAX_LIMIT,
    },
    "link.findReferences": {
        "description": "Find bounded backlinks/references to a target path, alias, or document id.",
        "params": {"target": "string", "limit": "integer optional"},
        "default_limit": LIST_DEFAULT_LIMIT,
        "max_limit": LIST_MAX_LIMIT,
    },
    "audit.summary": {
        "description": "Return the current catalog audit summary.",
        "params": {},
    },
}


class JsonRpcException(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def handle_jsonrpc_text(
    text: str,
    *,
    db_path: Path = DEFAULT_DB,
    trace_path: Path | None = DEFAULT_API_TRACE,
) -> dict[str, Any] | None:
    try:
        request = json.loads(text)
    except json.JSONDecodeError:
        response = error_response(None, PARSE_ERROR, "Parse error")
        trace_error(trace_path, method=None, request_id=None, code=PARSE_ERROR, message="Parse error")
        return response
    return handle_jsonrpc(request, db_path=db_path, trace_path=trace_path)


def handle_jsonrpc(
    request: Any,
    *,
    db_path: Path = DEFAULT_DB,
    trace_path: Path | None = DEFAULT_API_TRACE,
) -> dict[str, Any] | None:
    request_id = request.get("id") if isinstance(request, dict) else None
    method = request.get("method") if isinstance(request, dict) else None
    try:
        method, params, request_id, notification = validate_request(request)
        result = dispatch_method(method, params, db_path=db_path)
    except JsonRpcException as exc:
        response = error_response(request_id, exc.code, exc.message)
        trace_error(trace_path, method=method, request_id=request_id, code=exc.code, message=exc.message)
        return response
    except Exception as exc:  # pragma: no cover - defensive API boundary
        response = error_response(request_id, INTERNAL_ERROR, "Internal error")
        trace_error(
            trace_path,
            method=method,
            request_id=request_id,
            code=INTERNAL_ERROR,
            message=str(exc),
        )
        return response

    trace_success(trace_path, method=method, request_id=request_id, params=params, result=result)
    if notification:
        return None
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def validate_request(request: Any) -> tuple[str, dict[str, Any], Any, bool]:
    if not isinstance(request, dict):
        raise JsonRpcException(INVALID_REQUEST, "Invalid Request")
    if request.get("jsonrpc") != JSONRPC_VERSION:
        raise JsonRpcException(INVALID_REQUEST, "Invalid Request")
    method = request.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcException(INVALID_REQUEST, "Invalid Request")
    params = request.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JsonRpcException(INVALID_PARAMS, "Invalid params")
    return method, params, request.get("id"), "id" not in request


def dispatch_method(method: str, params: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    if method == "api.methods":
        return api_methods()
    if method == "symbol.search":
        return search_symbols(params, db_path=db_path)
    if method == "span.searchText":
        return search_spans(params, db_path=db_path)
    if method == "span.listHeadings":
        return list_headings(params, db_path=db_path)
    if method == "link.findReferences":
        return find_link_references(params, db_path=db_path)
    if method == "audit.summary":
        return {
            "method": method,
            "summary": audit_summary(db_path),
            "policy": policy("audit-summary", returned="summary"),
        }
    raise JsonRpcException(METHOD_NOT_FOUND, "Method not found")


def api_methods() -> dict[str, Any]:
    return {
        "method": "api.methods",
        "methods": [
            {"name": name, **spec}
            for name, spec in sorted(METHODS.items(), key=lambda item: item[0])
        ],
        "policy": policy("method-discovery", returned="contract"),
    }


def search_symbols(params: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    query = required_string(params, "query")
    limit = bounded_limit(params, default=SEARCH_DEFAULT_LIMIT, maximum=SEARCH_MAX_LIMIT)
    rows = query_catalog(db_path, "symbol.search", query, limit + 1)
    results, truncated = slice_results(rows, limit)
    return {
        "method": "symbol.search",
        "query": query,
        "limit": limit,
        "truncated": truncated,
        "results": results,
        "policy": policy("symbol-search", returned="symbol-handles"),
    }


def search_spans(params: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    query = required_string(params, "query")
    limit = bounded_limit(params, default=SEARCH_DEFAULT_LIMIT, maximum=SEARCH_MAX_LIMIT)
    rows = query_catalog(db_path, "span.searchText", query, limit + 1)
    results, truncated = slice_results(rows, limit)
    return {
        "method": "span.searchText",
        "query": query,
        "limit": limit,
        "truncated": truncated,
        "results": results,
        "policy": policy("bounded-span-search", returned="span-handles"),
    }


def list_headings(params: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    path = required_string(params, "path")
    limit = bounded_limit(params, default=LIST_DEFAULT_LIMIT, maximum=LIST_MAX_LIMIT)
    rows = get_headings(db_path, path)
    results, truncated = slice_results(rows, limit)
    return {
        "method": "span.listHeadings",
        "path": path,
        "limit": limit,
        "truncated": truncated,
        "headings": results,
        "policy": policy("bounded-heading-list", returned="heading-handles"),
    }


def find_link_references(params: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    target = required_string(params, "target")
    limit = bounded_limit(params, default=LIST_DEFAULT_LIMIT, maximum=LIST_MAX_LIMIT)
    rows = find_references(db_path, target)
    results, truncated = slice_results(rows, limit)
    return {
        "method": "link.findReferences",
        "target": target,
        "limit": limit,
        "truncated": truncated,
        "references": results,
        "policy": policy("bounded-reference-list", returned="reference-handles"),
    }


def required_string(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JsonRpcException(INVALID_PARAMS, f"Invalid params: {key} must be a non-empty string")
    return value.strip()


def bounded_limit(params: dict[str, Any], *, default: int, maximum: int) -> int:
    raw_limit = params.get("limit", default)
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool):
        raise JsonRpcException(INVALID_PARAMS, "Invalid params: limit must be an integer")
    if raw_limit < 1:
        raise JsonRpcException(INVALID_PARAMS, "Invalid params: limit must be >= 1")
    return min(raw_limit, maximum)


def slice_results(rows: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], bool]:
    return rows[:limit], len(rows) > limit


def policy(decision: str, *, returned: str) -> dict[str, Any]:
    return {
        "bounded": True,
        "decision": decision,
        "returned": returned,
        "whole_document_reads": False,
    }


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def trace_success(
    trace_path: Path | None,
    *,
    method: str,
    request_id: Any,
    params: dict[str, Any],
    result: dict[str, Any],
) -> None:
    write_trace(
        trace_path,
        {
            "event": "api_request",
            "status": "ok",
            "method": method,
            "request_id": request_id,
            "params": trace_params(params),
            "policy": result.get("policy", {}),
            "result_count": result_count(result),
        },
    )


def trace_error(
    trace_path: Path | None,
    *,
    method: Any,
    request_id: Any,
    code: int,
    message: str,
) -> None:
    write_trace(
        trace_path,
        {
            "event": "api_request",
            "status": "error",
            "method": method,
            "request_id": request_id,
            "error": {"code": code, "message": message},
        },
    )


def write_trace(trace_path: Path | None, payload: dict[str, Any]) -> None:
    if trace_path is None:
        return
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a") as handle:
        handle.write(json.dumps({"timestamp_utc": utc_now(), **payload}, sort_keys=True) + "\n")


def trace_params(params: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("query", "path", "target", "limit"):
        if key in params:
            summary[key] = params[key]
    return summary


def result_count(result: dict[str, Any]) -> int | None:
    for key in ("results", "headings", "references", "methods"):
        value = result.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
