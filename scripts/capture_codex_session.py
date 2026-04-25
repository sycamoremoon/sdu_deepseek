#!/usr/bin/env python3
"""Capture a sanitized Codex Responses session while proxying to the local SDU server."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin

import requests


SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "api-key"}


def item_types(value):
    if not isinstance(value, list):
        return []
    return [str(item.get("type") or ("message" if isinstance(item, dict) and "role" in item else "unknown")) for item in value if isinstance(item, dict)]


def tool_names(tools):
    names = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not name and isinstance(tool.get("function"), dict):
            name = tool["function"].get("name")
        if not name:
            name = tool.get("type")
        if name:
            names.append(str(name))
    return names


def contains_item_type(value, wanted):
    if isinstance(value, dict):
        if value.get("type") in wanted:
            return True
        return any(contains_item_type(item, wanted) for item in value.values())
    if isinstance(value, list):
        return any(contains_item_type(item, wanted) for item in value)
    return False


def output_summary(response):
    output = response.get("output") if isinstance(response, dict) else None
    if not isinstance(output, list):
        return {}
    return {
        "output_item_types": [item.get("type") for item in output if isinstance(item, dict)],
        "tool_names": [item.get("name") for item in output if isinstance(item, dict) and item.get("name")],
        "output_text_empty": not bool(str(response.get("output_text", "")).strip()),
        "status": response.get("status"),
        "error_code": (response.get("error") or {}).get("code") if isinstance(response.get("error"), dict) else None,
    }


def sse_event_type(raw_line):
    if raw_line.startswith("event: "):
        return raw_line[7:].strip()
    return None


class CaptureProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    @property
    def store(self):
        return self.server.store

    def append_record(self, record):
        self.store["output"].parent.mkdir(parents=True, exist_ok=True)
        with self.store["output"].open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def do_GET(self):
        self.proxy()

    def do_DELETE(self):
        self.proxy()

    def do_POST(self):
        self.proxy()

    def proxy(self):
        capture_id = f"cap_{uuid.uuid4().hex[:12]}"
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        request_json = None
        if body:
            try:
                request_json = json.loads(body)
            except json.JSONDecodeError:
                request_json = None

        request_record = {
            "capture_id": capture_id,
            "captured_at": int(time.time()),
            "kind": "request",
            "method": self.command,
            "path": self.path,
        }
        if isinstance(request_json, dict):
            request_record.update(
                {
                    "model": request_json.get("model"),
                    "stream": request_json.get("stream"),
                    "previous_response_id": request_json.get("previous_response_id"),
                    "store": request_json.get("store"),
                    "input_item_types": item_types(request_json.get("input")),
                    "has_function_call_output": contains_item_type(
                        request_json.get("input"),
                        {"function_call_output", "custom_tool_call_output"},
                    ),
                    "has_function_call_record": contains_item_type(
                        request_json.get("input"),
                        {"function_call", "custom_tool_call"},
                    ),
                    "tools_count": len(request_json.get("tools") or []),
                    "tool_names": tool_names(request_json.get("tools")),
                }
            )
        self.append_record(request_record)

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in SENSITIVE_HEADERS and key.lower() not in {"host", "content-length"}
        }
        upstream_url = urljoin(self.store["target_base"], self.path.lstrip("/"))
        try:
            upstream = requests.request(
                self.command,
                upstream_url,
                data=body or None,
                headers=headers,
                stream=True,
                timeout=(10, self.store["read_timeout"]),
            )
        except requests.RequestException as exc:
            payload = json.dumps({"error": {"message": str(exc), "type": "capture_proxy_error"}}, ensure_ascii=False).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.append_record({"capture_id": capture_id, "kind": "proxy_error", "message": type(exc).__name__})
            return

        content_type = upstream.headers.get("Content-Type", "application/octet-stream")
        self.send_response(upstream.status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", upstream.headers.get("Cache-Control", "no-cache"))
        self.send_header("Connection", "close")
        self.end_headers()

        response_record = {
            "capture_id": capture_id,
            "captured_at": int(time.time()),
            "kind": "response",
            "status_code": upstream.status_code,
            "content_type": content_type,
            "sse_events": [],
        }
        if "text/event-stream" in content_type:
            for line in upstream.iter_lines(decode_unicode=True):
                if line is None:
                    continue
                encoded = (line + "\n").encode("utf-8")
                self.wfile.write(encoded)
                self.wfile.flush()
                event_type = sse_event_type(line)
                if event_type:
                    response_record["sse_events"].append(event_type)
                if line.startswith("data: "):
                    try:
                        payload = json.loads(line[6:])
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, dict) and payload.get("type") in {"response.completed", "response.failed"}:
                        response = payload.get("response")
                        if isinstance(response, dict):
                            response_record.update(output_summary(response))
            self.wfile.write(b"\n")
        else:
            data = upstream.content
            self.wfile.write(data)
            try:
                payload = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = None
            if isinstance(payload, dict):
                response_record.update(output_summary(payload))
        self.append_record(response_record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--target-base", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="docs/codex_v4_stop_probe.jsonl")
    parser.add_argument("--read-timeout", type=float, default=660.0)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), CaptureProxy)
    server.store = {
        "target_base": args.target_base.rstrip("/") + "/",
        "output": Path(args.output),
        "read_timeout": args.read_timeout,
    }
    print(f"Capture proxy listening on http://{args.host}:{args.port}; target={server.store['target_base']}")
    print(f"Writing sanitized records to {server.store['output']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
