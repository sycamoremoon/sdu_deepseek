#!/usr/bin/env python3
"""Conservative SDU AI Assist capability probe.

The probe is intentionally scoped to the compose_chat endpoint already used by
this repository. It never prints cookies or credentials, and live probing is
opt-in via RUN_LIVE_SDU_PROBE=1 or --live.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://aiassist.sdu.edu.cn/site/ai/compose_chat"
DEFAULT_MODEL = "DeepSeek-V3.2"


@dataclass
class ProbeResult:
    capability: str
    method: str
    request_format: str
    response_format: str
    supported: str
    evidence_summary: str
    compatibility_strategy: str


def load_cookies(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def base_form(content: str) -> dict[str, Any]:
    return {
        "content": content,
        "compose_id": 73,
        "auth_tag": "本科生",
        "deep_search": 2,
        "internet_search": 2,
        "model_name": DEFAULT_MODEL,
        "thinking_budget": 1000,
        "chat_only_id": uuid.uuid4().hex,
    }


def summarize_stream(raw: bytes) -> dict[str, Any]:
    sample = raw[:4000].decode("utf-8", errors="replace")
    lines = [line for line in sample.splitlines() if line.strip()]
    data_lines = [line for line in lines if line.startswith("data:")]
    parsed_keys: list[list[str]] = []
    answer_fragments: list[str] = []
    for line in data_lines[:10]:
        text = line[5:].strip()
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            parsed_keys.append(sorted(item.keys()))
            answer = item.get("d", {}).get("answer") if isinstance(item.get("d"), dict) else None
            if isinstance(answer, str):
                answer_fragments.append(answer[:80])
    return {
        "line_count_sample": len(lines),
        "data_line_count_sample": len(data_lines),
        "parsed_top_level_keys": parsed_keys[:5],
        "answer_fragment_count": len(answer_fragments),
        "answer_fragments": answer_fragments[:3],
        "contains_think_marker": "<think" in sample or "<think\\>" in sample,
    }


def post_form(endpoint: str, cookies: dict[str, str], form: dict[str, Any], timeout: float) -> dict[str, Any]:
    encoded = urlencode(form).encode("utf-8")
    request = Request(
        endpoint,
        data=encoded,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Cookie": cookie_header(cookies),
            "User-Agent": "SDUDeepSeekCapabilityProbe/1.0",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(8192)
            return {
                "ok": True,
                "status": response.status,
                "headers": safe_response_headers(dict(response.headers.items())),
                "elapsed_ms": int((time.time() - started) * 1000),
                "stream_summary": summarize_stream(raw),
            }
    except HTTPError as exc:
        raw = exc.read(1000)
        return {
            "ok": False,
            "status": exc.code,
            "headers": safe_response_headers(dict(exc.headers.items())),
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": raw.decode("utf-8", errors="replace")[:500],
        }
    except URLError as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": str(exc.reason),
        }


def post_json(endpoint: str, cookies: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    raw_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint,
        data=raw_payload,
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "Cookie": cookie_header(cookies),
            "User-Agent": "SDUDeepSeekCapabilityProbe/1.0",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(2048)
            return {
                "ok": True,
                "status": response.status,
                "headers": safe_response_headers(dict(response.headers.items())),
                "elapsed_ms": int((time.time() - started) * 1000),
                "body_sample": raw.decode("utf-8", errors="replace")[:500],
            }
    except HTTPError as exc:
        raw = exc.read(1000)
        return {
            "ok": False,
            "status": exc.code,
            "headers": safe_response_headers(dict(exc.headers.items())),
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": raw.decode("utf-8", errors="replace")[:500],
        }
    except URLError as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": str(exc.reason),
        }


def safe_response_headers(headers: dict[str, str]) -> dict[str, str]:
    allowed = {
        "content-type",
        "cache-control",
        "x-accel-buffering",
        "transfer-encoding",
        "connection",
        "server",
    }
    return {key: value for key, value in headers.items() if key.lower() in allowed}


def static_results() -> list[ProbeResult]:
    return [
        ProbeResult(
            capability="Known SDU endpoint scope",
            method="Static source inspection",
            request_format="sduwrap.url points to /site/ai/compose_chat only",
            response_format="Streaming lines beginning with data:",
            supported="yes",
            evidence_summary="The repository only calls https://aiassist.sdu.edu.cn/site/ai/compose_chat for AI chat.",
            compatibility_strategy="Keep probes and compatibility layer scoped to compose_chat unless same-origin web evidence is added.",
        ),
        ProbeResult(
            capability="Form data chat request",
            method="Static source inspection",
            request_format="application/x-www-form-urlencoded fields: content, history[n][role], history[n][content], compose_id, auth_tag, deep_search, internet_search, model_name, thinking_budget, chat_only_id",
            response_format="SSE-like data lines containing JSON with d.answer",
            supported="yes",
            evidence_summary="sduwrap.make_chat_request builds form data and sduwrap.chat posts with data=form_data, stream=True.",
            compatibility_strategy="Responses API should convert text turns to the existing content/history form contract.",
        ),
        ProbeResult(
            capability="Native tool/function calling",
            method="Static source inspection",
            request_format="No tool fields in compose_chat form data",
            response_format="Only text chunks from d.answer are parsed",
            supported="no evidence",
            evidence_summary="No function/tool/plugin schema or tool result field is present in current code.",
            compatibility_strategy="Implement protocol-level tool bridge by prompt injection and local parsing; never execute Codex tools server-side.",
        ),
        ProbeResult(
            capability="Native image/file input",
            method="Static source inspection",
            request_format="No multipart, file id, image_url, or upload endpoint in current code",
            response_format="Only text chunks from d.answer are parsed",
            supported="no evidence",
            evidence_summary="The known compose_chat request accepts content text and history text only.",
            compatibility_strategy="Return explicit unsupported_input_image/unsupported_input_file unless a same-origin upload API is verified later.",
        ),
        ProbeResult(
            capability="Reasoning content",
            method="Static source inspection",
            request_format="thinking_budget is sent with model_name; deep_search/internet_search are numeric form fields",
            response_format="sduwrap.ChatStream splits escaped <think\\>...</think\\> markers into reasoning_content",
            supported="best effort",
            evidence_summary="Reasoning is parsed from model text markers, not a separate typed response field in the current code.",
            compatibility_strategy="Map parsed reasoning_content to Responses reasoning summary/text compatibility fields.",
        ),
    ]


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    cookies = load_cookies(args.cookies)
    live_enabled = args.live or os.environ.get("RUN_LIVE_SDU_PROBE") == "1"
    report: dict[str, Any] = {
        "generated_at": int(time.time()),
        "endpoint": args.endpoint,
        "live_enabled": live_enabled,
        "cookies_present": bool(cookies),
        "static_results": [asdict(item) for item in static_results()],
        "live_results": {},
    }

    if not live_enabled:
        report["live_results"]["skipped"] = "Set RUN_LIVE_SDU_PROBE=1 or pass --live to send low-frequency live requests."
        return report
    if not cookies:
        report["live_results"]["skipped"] = "cookies.json was not found or did not contain a cookie object."
        return report

    form = base_form("ping")
    report["live_results"]["form_ping"] = post_form(args.endpoint, cookies, form, args.timeout)
    json_payload = base_form("ping json format probe")
    report["live_results"]["json_ping"] = post_json(args.endpoint, cookies, json_payload, args.timeout)
    history_form = base_form("请只回答 OK")
    history_form.update(
        {
            "history[0][role]": "user",
            "history[0][content]": "系统提示探测：请保持简短。",
            "history[1][role]": "assistant",
            "history[1][content]": "我知道了",
        }
    )
    report["live_results"]["history_probe"] = post_form(args.endpoint, cookies, history_form, args.timeout)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--cookies", type=Path, default=Path("cookies.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/sdu_web_capabilities_probe.json"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    report = run_probe(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote sanitized probe report to {args.output}")
    if report.get("live_results", {}).get("skipped"):
        print(report["live_results"]["skipped"])


if __name__ == "__main__":
    main()
