#!/usr/bin/env python3
"""Capture Codex custom-provider wire requests with a minimal local provider.

It prefers FastAPI when project dependencies are installed, and falls back to
the Python standard library so probing can still run in a bare environment. It
emulates enough of the OpenAI Responses API for Codex CLI/IDE probing and
writes sanitized JSONL records.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "proxy-authorization",
}

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "fingerprint",
    "installation",
    "session",
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "key",
)

MODEL_ID = "deepseek-ai/DeepSeek-V3.2"
CAPTURE_RESPONSE_ID = "resp_capture_static"


class CaptureStore:
    def __init__(self, output: Path, force_tool_call: bool = False) -> None:
        self.output = output
        self.force_tool_call = force_tool_call
        self.responses: dict[str, dict[str, Any]] = {}
        self.output.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        with self.output.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _redact_string(value: str, keep_prefix: int = 160) -> str:
    if len(value) <= keep_prefix:
        return value
    return f"{value[:keep_prefix]}...[{len(value)} chars]"


def sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth > 10:
        return "<max_depth>"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                sanitized[str(key)] = "<redacted>"
            else:
                sanitized[str(key)] = sanitize_value(item, depth + 1)
        return sanitized
    if isinstance(value, list):
        return [sanitize_value(item, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def shape_of(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<max_depth>"
    if isinstance(value, dict):
        return {str(key): shape_of(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        return [shape_of(value[0], depth + 1), f"... len={len(value)}"]
    if isinstance(value, str):
        return "str"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return type(value).__name__


def sanitized_headers(headers: Any) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in SENSITIVE_HEADER_NAMES or any(part in lowered for part in SENSITIVE_KEY_PARTS):
            safe[key] = "<redacted>"
        elif lowered in {
            "accept",
            "accept-encoding",
            "content-type",
            "host",
            "user-agent",
            "openai-organization",
            "openai-project",
            "x-stainless-arch",
            "x-stainless-lang",
            "x-stainless-os",
            "x-stainless-package-version",
            "x-stainless-runtime",
            "x-stainless-runtime-version",
            "x-stainless-retry-count",
            "x-stainless-timeout",
        }:
            safe[key] = _redact_string(value, keep_prefix=200)
    return safe


def response_object(
    response_id: str,
    body: dict[str, Any] | None,
    output: list[dict[str, Any]],
    status: str = "completed",
) -> dict[str, Any]:
    model = body.get("model", MODEL_ID) if body else MODEL_ID
    output_text = "".join(
        part.get("text", "")
        for item in output
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output,
        "output_text": output_text,
        "parallel_tool_calls": body.get("parallel_tool_calls", True) if body else True,
        "previous_response_id": body.get("previous_response_id") if body else None,
        "reasoning": {"summary": []},
        "store": body.get("store", True) if body else True,
        "temperature": body.get("temperature") if body else None,
        "text": body.get("text", {"format": {"type": "text"}}) if body else {"format": {"type": "text"}},
        "tool_choice": body.get("tool_choice", "auto") if body else "auto",
        "tools": body.get("tools", []) if body else [],
        "truncation": body.get("truncation", "disabled") if body else "disabled",
        "usage": {
            "input_tokens": 1,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": max(1, len(output_text) // 4),
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 1 + max(1, len(output_text) // 4),
        },
    }


def text_output_item(text: str) -> dict[str, Any]:
    return {
        "id": f"msg_{uuid.uuid4().hex[:16]}",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": text,
                "annotations": [],
            }
        ],
    }


def get_tool_name(tool: dict[str, Any]) -> str | None:
    if "name" in tool:
        return str(tool["name"])
    function = tool.get("function")
    if isinstance(function, dict) and "name" in function:
        return str(function["name"])
    tool_type = tool.get("type")
    if isinstance(tool_type, str):
        return tool_type
    return None


def build_tool_arguments(tool: dict[str, Any]) -> dict[str, Any]:
    name = get_tool_name(tool) or ""
    lowered = name.lower()
    schema = tool.get("parameters") or tool.get("function", {}).get("parameters") or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}

    if "exec" in lowered or "shell" in lowered or "command" in lowered:
        if "cmd" in properties:
            return {"cmd": "pwd"}
        if "command" in properties:
            return {"command": "pwd"}
        if "args" in properties:
            return {"args": ["pwd"]}
    if "apply_patch" in lowered:
        patch = "*** Begin Patch\n*** End Patch\n"
        if "patch" in properties:
            return {"patch": patch}
        if "cmd" in properties:
            return {"cmd": "apply_patch <<'PATCH'\n" + patch + "PATCH"}
    if "read" in lowered or "file" in lowered:
        if "path" in properties:
            return {"path": "README.md"}

    required = schema.get("required", []) if isinstance(schema, dict) else []
    args: dict[str, Any] = {}
    for key in required:
        prop = properties.get(key, {})
        prop_type = prop.get("type")
        if prop_type == "array":
            args[key] = []
        elif prop_type == "object":
            args[key] = {}
        elif prop_type == "number":
            args[key] = 0
        elif prop_type == "integer":
            args[key] = 0
        elif prop_type == "boolean":
            args[key] = False
        else:
            args[key] = "capture_probe"
    return args or {"cmd": "pwd"}


def should_return_tool_call(body: dict[str, Any], store: CaptureStore) -> bool:
    if not body.get("tools"):
        return False
    if has_function_call_output(body.get("input")):
        return False
    body_text = json.dumps(sanitize_value(body), ensure_ascii=False)
    return store.force_tool_call or "CAPTURE_TRIGGER_TOOL_CALL" in body_text


def has_function_call_output(input_value: Any) -> bool:
    if isinstance(input_value, dict):
        if input_value.get("type") == "function_call_output":
            return True
        return any(has_function_call_output(value) for value in input_value.values())
    if isinstance(input_value, list):
        return any(has_function_call_output(item) for item in input_value)
    return False


def first_tool_call_item(body: dict[str, Any]) -> dict[str, Any] | None:
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        return None
    tool = next((item for item in tools if isinstance(item, dict)), None)
    if not tool:
        return None
    name = get_tool_name(tool)
    if not name:
        return None
    args = build_tool_arguments(tool)
    return {
        "id": f"fc_{uuid.uuid4().hex[:16]}",
        "type": "function_call",
        "status": "completed",
        "call_id": f"call_{uuid.uuid4().hex[:16]}",
        "name": name,
        "arguments": json.dumps(args, ensure_ascii=False),
    }


def output_for_request(body: dict[str, Any], store: CaptureStore) -> list[dict[str, Any]]:
    if should_return_tool_call(body, store):
        item = first_tool_call_item(body)
        if item:
            return [item]
    if has_function_call_output(body.get("input")):
        return [text_output_item("Capture provider received tool output and completed the turn.")]
    return [text_output_item("Capture provider response: request recorded successfully.")]


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def iter_sse_events(response: dict[str, Any]):
    created = {**response, "output": []}
    yield sse_event("response.created", {"type": "response.created", "response": created})
    yield sse_event("response.in_progress", {"type": "response.in_progress", "response": created})

    for output_index, item in enumerate(response["output"]):
        if item["type"] == "function_call":
            added = {**item, "arguments": ""}
            yield sse_event(
                "response.output_item.added",
                {"type": "response.output_item.added", "output_index": output_index, "item": added},
            )
            arguments = item.get("arguments", "")
            if arguments:
                yield sse_event(
                    "response.function_call_arguments.delta",
                    {
                        "type": "response.function_call_arguments.delta",
                        "output_index": output_index,
                        "item_id": item["id"],
                        "delta": arguments,
                    },
                )
            yield sse_event(
                "response.function_call_arguments.done",
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": output_index,
                    "item_id": item["id"],
                    "arguments": arguments,
                },
            )
            yield sse_event(
                "response.output_item.done",
                {"type": "response.output_item.done", "output_index": output_index, "item": item},
            )
            continue

        text_part = item["content"][0]
        content_index = 0
        added = {**item, "content": []}
        yield sse_event(
            "response.output_item.added",
            {"type": "response.output_item.added", "output_index": output_index, "item": added},
        )
        yield sse_event(
            "response.content_part.added",
            {
                "type": "response.content_part.added",
                "output_index": output_index,
                "content_index": content_index,
                "item_id": item["id"],
                "part": {**text_part, "text": ""},
            },
        )
        text = text_part.get("text", "")
        if text:
            yield sse_event(
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "output_index": output_index,
                    "content_index": content_index,
                    "item_id": item["id"],
                    "delta": text,
                },
            )
        yield sse_event(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "output_index": output_index,
                "content_index": content_index,
                "item_id": item["id"],
                "text": text,
            },
        )
        yield sse_event(
            "response.content_part.done",
            {
                "type": "response.content_part.done",
                "output_index": output_index,
                "content_index": content_index,
                "item_id": item["id"],
                "part": text_part,
            },
        )
        yield sse_event(
            "response.output_item.done",
            {"type": "response.output_item.done", "output_index": output_index, "item": item},
        )

    yield sse_event("response.completed", {"type": "response.completed", "response": response})


def record_external_request(
    store: CaptureStore,
    method: str,
    path: str,
    query: str,
    headers: Any,
    body: Any,
) -> None:
    record = {
        "captured_at": int(time.time()),
        "method": method,
        "path": path,
        "query": query,
        "headers": sanitized_headers(headers),
        "json_body": sanitize_value(body) if body is not None else None,
        "json_body_shape": shape_of(body) if body is not None else None,
    }
    store.append(record)


class CaptureHandler(BaseHTTPRequestHandler):
    server_version = "CodexCaptureProvider/1.0"

    @property
    def store(self) -> CaptureStore:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[capture] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        self.record_request(None)
        if parsed.path in {"/v1/models", "/models"}:
            self.send_json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL_ID,
                            "object": "model",
                            "created": 1700000000,
                            "owned_by": "capture",
                        }
                    ],
                }
            )
            return
        response_id = response_id_from_path(parsed.path)
        if response_id:
            response = self.store.responses.get(response_id)
            if response is None:
                self.send_json(error_payload("response_not_found", f"Response {response_id} not found"), 404)
            else:
                self.send_json(response)
            return
        if parsed.path.endswith("/input_items"):
            self.send_json({"object": "list", "data": [], "has_more": False})
            return
        self.send_json(error_payload("not_found", f"Unhandled path {parsed.path}"), 404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        self.record_request(None)
        response_id = response_id_from_path(parsed.path)
        if response_id:
            deleted = self.store.responses.pop(response_id, None) is not None
            self.send_json({"id": response_id, "object": "response.deleted", "deleted": deleted})
            return
        self.send_json(error_payload("not_found", f"Unhandled path {parsed.path}"), 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        raw = self.rfile.read(int(self.headers.get("content-length", "0") or 0))
        body = parse_json(raw)
        self.record_request(body)

        if parsed.path not in {"/v1/responses", "/responses"}:
            self.send_json(error_payload("not_found", f"Unhandled path {parsed.path}"), 404)
            return

        if not isinstance(body, dict):
            self.send_json(error_payload("invalid_json", "Expected a JSON object body."), 400)
            return

        response_id = f"resp_{uuid.uuid4().hex[:24]}"
        output = output_for_request(body, self.store)
        response = response_object(response_id, body, output)
        self.store.responses[response_id] = response

        if body.get("stream"):
            self.send_stream(response)
        else:
            self.send_json(response)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_stream(self, response: dict[str, Any]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        for chunk in iter_sse_events(response):
            self.wfile.write(chunk)

    def record_request(self, body: Any) -> None:
        parsed = urlparse(self.path)
        record_external_request(self.store, self.command, parsed.path, parsed.query, self.headers, body)


def parse_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_unparsed_body": f"{len(raw)} bytes"}


def response_id_from_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[-2] == "responses" and not parts[-1].endswith("input_items"):
        return parts[-1]
    return None


def error_payload(code: str, message: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": "invalid_request_error", "code": code}}


def run_stdlib_server(args: argparse.Namespace, store: CaptureStore) -> None:
    server = ThreadingHTTPServer((args.host, args.port), CaptureHandler)
    server.store = store  # type: ignore[attr-defined]
    print(f"Capture provider listening on http://{args.host}:{args.port} (stdlib backend)")
    print(f"Sanitized JSONL will be written to {args.output}")
    server.serve_forever()


def run_fastapi_server(args: argparse.Namespace, store: CaptureStore) -> None:
    try:
        import uvicorn
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, StreamingResponse
    except ImportError as exc:
        if args.backend == "fastapi":
            raise SystemExit(f"FastAPI backend requested but dependency is missing: {exc}") from exc
        run_stdlib_server(args, store)
        return

    app = FastAPI(title="Codex Capture Provider")

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "DELETE"])
    async def capture(full_path: str, request: Request):
        path = "/" + full_path
        body: Any = None
        if request.method == "POST":
            raw = await request.body()
            body = parse_json(raw)
        record_external_request(store, request.method, path, request.url.query, request.headers, body)

        if request.method == "GET" and path in {"/v1/models", "/models"}:
            return JSONResponse(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL_ID,
                            "object": "model",
                            "created": 1700000000,
                            "owned_by": "capture",
                        }
                    ],
                }
            )

        response_id = response_id_from_path(path)
        if request.method == "GET" and response_id:
            response = store.responses.get(response_id)
            if response is None:
                return JSONResponse(error_payload("response_not_found", f"Response {response_id} not found"), status_code=404)
            return JSONResponse(response)

        if request.method == "DELETE" and response_id:
            deleted = store.responses.pop(response_id, None) is not None
            return JSONResponse({"id": response_id, "object": "response.deleted", "deleted": deleted})

        if request.method == "GET" and path.endswith("/input_items"):
            return JSONResponse({"object": "list", "data": [], "has_more": False})

        if request.method == "POST" and path in {"/v1/responses", "/responses"}:
            if not isinstance(body, dict):
                return JSONResponse(error_payload("invalid_json", "Expected a JSON object body."), status_code=400)
            response_id = f"resp_{uuid.uuid4().hex[:24]}"
            output = output_for_request(body, store)
            response = response_object(response_id, body, output)
            store.responses[response_id] = response
            if body.get("stream"):
                return StreamingResponse(iter_sse_events(response), media_type="text/event-stream")
            return JSONResponse(response)

        return JSONResponse(error_payload("not_found", f"Unhandled path {path}"), status_code=404)

    print(f"Capture provider listening on http://{args.host}:{args.port} (FastAPI backend)")
    print(f"Sanitized JSONL will be written to {args.output}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--output", type=Path, default=Path("docs/codex_wire_probe_logs/capture.jsonl"))
    parser.add_argument("--force-tool-call", action="store_true")
    parser.add_argument("--backend", choices=["auto", "fastapi", "stdlib"], default="auto")
    args = parser.parse_args()

    store = CaptureStore(args.output, force_tool_call=args.force_tool_call)
    if args.backend == "stdlib":
        run_stdlib_server(args, store)
    else:
        run_fastapi_server(args, store)


if __name__ == "__main__":
    main()
