from __future__ import annotations

import json
from typing import Any, Iterable


def sse_event(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def sse_comment(comment: str = "keepalive") -> str:
    safe = comment.replace("\n", " ")
    return f": {safe}\n\n"


def response_created_events(response: dict[str, Any]) -> Iterable[str]:
    empty_response = {**response, "output": []}
    yield sse_event("response.created", {"type": "response.created", "response": empty_response})
    yield sse_event("response.in_progress", {"type": "response.in_progress", "response": empty_response})


def message_start_events(item: dict[str, Any], output_index: int = 0) -> Iterable[str]:
    content_index = 0
    text_part = item["content"][0]
    yield sse_event(
        "response.output_item.added",
        {
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {**item, "content": []},
        },
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


def message_delta_event(item: dict[str, Any], delta: str, output_index: int = 0) -> str:
    return sse_event(
        "response.output_text.delta",
        {
            "type": "response.output_text.delta",
            "output_index": output_index,
            "content_index": 0,
            "item_id": item["id"],
            "delta": delta,
        },
    )


def message_done_events(item: dict[str, Any], output_index: int = 0) -> Iterable[str]:
    text_part = item["content"][0]
    text = text_part.get("text", "")
    yield sse_event(
        "response.output_text.done",
        {
            "type": "response.output_text.done",
            "output_index": output_index,
            "content_index": 0,
            "item_id": item["id"],
            "text": text,
        },
    )
    yield sse_event(
        "response.content_part.done",
        {
            "type": "response.content_part.done",
            "output_index": output_index,
            "content_index": 0,
            "item_id": item["id"],
            "part": text_part,
        },
    )
    yield sse_event(
        "response.output_item.done",
        {
            "type": "response.output_item.done",
            "output_index": output_index,
            "item": item,
        },
    )


def tool_call_events(item: dict[str, Any], output_index: int = 0) -> Iterable[str]:
    if item.get("type") == "custom_tool_call":
        custom_input = item.get("input", "")
        yield sse_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {**item, "input": ""},
            },
        )
        if custom_input:
            yield sse_event(
                "response.custom_tool_call_input.delta",
                {
                    "type": "response.custom_tool_call_input.delta",
                    "output_index": output_index,
                    "item_id": item["id"],
                    "delta": custom_input,
                },
            )
        yield sse_event(
            "response.custom_tool_call_input.done",
            {
                "type": "response.custom_tool_call_input.done",
                "output_index": output_index,
                "item_id": item["id"],
                "input": custom_input,
            },
        )
    else:
        arguments = item.get("arguments", "")
        yield sse_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {**item, "arguments": ""},
            },
        )
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
        {
            "type": "response.output_item.done",
            "output_index": output_index,
            "item": item,
        },
    )


def response_completed_event(response: dict[str, Any]) -> str:
    return sse_event("response.completed", {"type": "response.completed", "response": response})


def response_failed_event(response: dict[str, Any]) -> str:
    return sse_event("response.failed", {"type": "response.failed", "response": response})
