from __future__ import annotations

import time
import uuid
import json
from dataclasses import dataclass, field
from typing import Any

import sduwrap

from responses_models import ResponsesRequest, UnsupportedInputError
from responses_store import ResponseStore
from tool_bridge import build_tool_prompt, parse_tool_calls


@dataclass
class AdapterMessage:
    role: str
    content: str

    def as_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class PreparedResponsesRequest:
    current_input: str
    history: list[AdapterMessage]
    conversation: list[dict[str, Any]]
    input_items: list[dict[str, Any]] = field(default_factory=list)

    def to_sdu_history(self) -> list[sduwrap.ChatSession]:
        sessions: list[sduwrap.ChatSession] = []
        for message in self.history:
            if not message.content:
                continue
            session = sduwrap.ChatSession()
            session.role = message.role
            session.content = message.content
            sessions.append(session)
        return sessions


def prepare_responses_request(request: ResponsesRequest, store: ResponseStore) -> PreparedResponsesRequest:
    messages: list[AdapterMessage] = []
    input_items = normalize_input_items(request.input)

    if request.previous_response_id:
        stored = store.get(request.previous_response_id)
        if not stored:
            raise UnsupportedInputError(
                "previous_response_not_found",
                f"previous_response_id {request.previous_response_id} was not found in the in-memory store.",
                "previous_response_id",
            )
        for item in stored.conversation:
            role = item.get("role")
            content = item.get("content")
            if isinstance(role, str) and isinstance(content, str):
                messages.append(AdapterMessage(role=role, content=content))

    prefix_parts: list[str] = []
    if request.instructions:
        prefix_parts.append(f"Instructions:\n{request.instructions}")

    tool_prompt = build_tool_prompt(request.tools, request.parallel_tool_calls)
    if tool_prompt:
        prefix_parts.append(tool_prompt)
    format_prompt = build_format_prompt(request)
    if format_prompt:
        prefix_parts.append(format_prompt)

    pending_tool_calls: dict[str, dict[str, Any]] = {}
    for item in input_items:
        if isinstance(item, dict) and item.get("type") in {"function_call", "custom_tool_call"}:
            call_id = item.get("call_id")
            if isinstance(call_id, str) and call_id:
                pending_tool_calls[call_id] = item
            continue
        if isinstance(item, dict) and item.get("type") in {"function_call_output", "custom_tool_call_output"}:
            call_id = item.get("call_id")
            converted = convert_tool_output_item(item, pending_tool_calls.get(call_id) if isinstance(call_id, str) else None)
        else:
            converted = convert_input_item(item)
        if converted.role in {"system", "developer"}:
            prefix_parts.append(f"{converted.role.title()} message:\n{converted.content}")
        else:
            messages.append(converted)

    if prefix_parts:
        messages.insert(0, AdapterMessage(role="system", content="\n\n".join(part for part in prefix_parts if part)))

    current_index = find_last_user_index(messages)
    if current_index is None:
        messages.append(AdapterMessage(role="user", content="Please continue."))
        current_index = len(messages) - 1

    history = messages[:current_index]
    current_input = messages[current_index].content
    trailing = messages[current_index + 1 :]
    if trailing:
        current_input += "\n\nAdditional context after the latest user message:\n" + format_messages(trailing)

    conversation = [message.as_dict() for message in messages[: current_index + 1]]
    return PreparedResponsesRequest(
        current_input=current_input,
        history=history,
        conversation=conversation,
        input_items=input_items,
    )


def normalize_input_items(input_value: Any) -> list[Any]:
    if input_value is None:
        return []
    if isinstance(input_value, str):
        return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": input_value}]}]
    if isinstance(input_value, list):
        return input_value
    if isinstance(input_value, dict):
        return [input_value]
    return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": str(input_value)}]}]


def convert_input_item(item: Any) -> AdapterMessage:
    if isinstance(item, str):
        return AdapterMessage(role="user", content=item)
    if not isinstance(item, dict):
        return AdapterMessage(role="user", content=str(item))

    item_type = item.get("type")
    if item_type == "message" or "role" in item:
        role = str(item.get("role") or "user")
        content = extract_text_content(item.get("content"), param="input.content")
        return AdapterMessage(role=role, content=content)
    if item_type == "function_call":
        name = item.get("name", "tool")
        call_id = item.get("call_id", "")
        arguments = item.get("arguments", "")
        return AdapterMessage(
            role="assistant",
            content=(
                "[Previous tool call record - do not repeat as an answer]\n"
                f"name: {name}\n"
                f"call_id: {call_id}\n"
                f"arguments_json: {arguments}"
            ),
        )
    if item_type == "function_call_output":
        return convert_tool_output_item(item)
    if item_type == "custom_tool_call":
        name = item.get("name", "custom_tool")
        call_id = item.get("call_id", "")
        custom_input = item.get("input", "")
        return AdapterMessage(
            role="assistant",
            content=(
                "[Previous custom tool call record - do not repeat as an answer]\n"
                f"name: {name}\n"
                f"call_id: {call_id}\n"
                f"input:\n{custom_input}"
            ),
        )
    if item_type == "custom_tool_call_output":
        return convert_tool_output_item(item)
    if item_type in {"input_text", "output_text", "text"}:
        return AdapterMessage(role="user", content=str(item.get("text", "")))
    if item_type in {"input_image", "image_url"}:
        raise UnsupportedInputError(
            "unsupported_input_image",
            "SDU AI Assist compose_chat is text-only in the verified web endpoint; input_image is not supported by this local proxy.",
            "input",
        )
    if item_type in {"input_file", "file", "file_id"}:
        raise UnsupportedInputError(
            "unsupported_input_file",
            "SDU AI Assist compose_chat has no verified file-input/upload path; input_file is not supported by this local proxy.",
            "input",
        )
    return AdapterMessage(role="user", content=extract_text_content(item.get("content", item), param="input"))


def convert_tool_output_item(item: dict[str, Any], call_item: dict[str, Any] | None = None) -> AdapterMessage:
    call_id = item.get("call_id", "")
    output = item.get("output", "")
    if not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False)

    lines = ["[Tool result already returned by Codex; use it to continue the task]"]
    if call_item:
        name = call_item.get("name")
        if name:
            lines.append(f"tool_name: {name}")
        if call_item.get("type") == "custom_tool_call":
            lines.append(f"tool_input:\n{call_item.get('input', '')}")
        else:
            lines.append(f"tool_arguments_json: {call_item.get('arguments', '')}")
    lines.append(f"call_id: {call_id}")
    lines.append("output:")
    lines.append(output)
    return AdapterMessage(role="user", content="\n".join(lines))


def extract_text_content(content: Any, param: str = "input") -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for index, part in enumerate(content):
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                parts.append(str(part))
                continue
            part_type = part.get("type")
            if part_type in {"input_text", "output_text", "text"}:
                parts.append(str(part.get("text", "")))
            elif part_type == "input_image" or "image_url" in part:
                raise UnsupportedInputError(
                    "unsupported_input_image",
                    "SDU AI Assist compose_chat is text-only in the verified web endpoint; image inputs are not supported.",
                    f"{param}[{index}]",
                )
            elif part_type in {"input_file", "file"} or "file_id" in part:
                raise UnsupportedInputError(
                    "unsupported_input_file",
                    "SDU AI Assist compose_chat has no verified file-input/upload path; file inputs are not supported.",
                    f"{param}[{index}]",
                )
            elif "text" in part:
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    if isinstance(content, dict):
        content_type = content.get("type")
        if content_type in {"input_image", "image_url"} or "image_url" in content:
            raise UnsupportedInputError(
                "unsupported_input_image",
                "SDU AI Assist compose_chat is text-only in the verified web endpoint; image inputs are not supported.",
                param,
            )
        if content_type in {"input_file", "file"} or "file_id" in content:
            raise UnsupportedInputError(
                "unsupported_input_file",
                "SDU AI Assist compose_chat has no verified file-input/upload path; file inputs are not supported.",
                param,
            )
        if "text" in content:
            return str(content.get("text", ""))
    return str(content)


def find_last_user_index(messages: list[AdapterMessage]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "user":
            return index
    return None


def format_messages(messages: list[AdapterMessage]) -> str:
    return "\n\n".join(f"{message.role}: {message.content}" for message in messages if message.content)


def build_format_prompt(request: ResponsesRequest) -> str:
    candidate = request.text if request.text is not None else request.response_format
    if candidate is None:
        return ""
    if isinstance(candidate, dict):
        fmt = candidate.get("format", candidate)
        if isinstance(fmt, dict):
            fmt_type = fmt.get("type")
            if fmt_type in {None, "text"}:
                return ""
            if fmt_type in {"json_object", "json_schema"}:
                return (
                    "Response format compatibility instruction:\n"
                    "The upstream SDU endpoint does not enforce structured output natively. "
                    "Return valid JSON that best matches this requested format:\n"
                    f"{candidate}"
                )
            return f"Response format compatibility instruction: best-effort format requested: {candidate}"
    return ""


def build_response_object(
    request: ResponsesRequest,
    response_id: str,
    output: list[dict[str, Any]],
    created_at: int | None = None,
    status: str = "completed",
    reasoning_text: str = "",
    previous_response_id: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = created_at or int(time.time())
    output_text = collect_output_text(output)
    usage = estimate_usage(request, output_text, reasoning_text)
    response: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "model": request.model,
        "output": output,
        "output_text": output_text,
        "previous_response_id": previous_response_id,
        "reasoning": reasoning_object(reasoning_text),
        "tools": request.tools,
        "tool_choice": request.tool_choice,
        "parallel_tool_calls": bool(request.parallel_tool_calls) if request.parallel_tool_calls is not None else False,
        "store": bool(request.store) if request.store is not None else True,
        "usage": usage,
        "error": error,
        "incomplete_details": None,
    }
    if request.temperature is not None:
        response["temperature"] = request.temperature
    if request.top_p is not None:
        response["top_p"] = request.top_p
    if request.text is not None:
        response["text"] = request.text
    return response


def normalize_sdu_output(content: str, reasoning_text: str = "") -> tuple[str, str]:
    if "<think" not in content:
        return content, reasoning_text

    stream = sduwrap.ChatStream()
    visible, hidden = stream.process(content)
    tail_visible, tail_hidden = stream.finalize()
    normalized_content = visible + tail_visible
    extracted_reasoning = hidden + tail_hidden
    if extracted_reasoning:
        return normalized_content, reasoning_text + extracted_reasoning
    return content, reasoning_text


def build_output_items(content: str, reasoning_text: str, request: ResponsesRequest) -> tuple[list[dict[str, Any]], list[str]]:
    parse_result = parse_tool_calls(content, request.tools, request.parallel_tool_calls) if request.tools else None
    if parse_result and parse_result.calls:
        return [call.to_response_item() for call in parse_result.calls], parse_result.errors

    message_text = content
    errors: list[str] = []
    if parse_result:
        errors = parse_result.errors
        if parse_result.stripped_text:
            message_text = parse_result.stripped_text
        elif parse_result.had_tool_markup and parse_result.errors:
            message_text = "The model returned a tool call that could not be converted safely."
    return [message_output_item(message_text)], errors


def message_output_item(text: str) -> dict[str, Any]:
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


def collect_output_text(output: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for item in output:
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text":
                texts.append(str(part.get("text", "")))
    return "".join(texts)


def reasoning_object(reasoning_text: str) -> dict[str, Any]:
    if not reasoning_text:
        return {"summary": []}
    return {
        "summary": [
            {
                "type": "summary_text",
                "text": reasoning_text,
            }
        ],
        "text": reasoning_text,
    }


def estimate_usage(request: ResponsesRequest, output_text: str, reasoning_text: str = "") -> dict[str, Any]:
    input_chars = len(str(request.input or "")) + len(request.instructions or "")
    output_chars = len(output_text) + len(reasoning_text)
    input_tokens = max(0, input_chars // 4)
    output_tokens = max(0, output_chars // 4)
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": max(0, len(reasoning_text) // 4)},
        "total_tokens": input_tokens + output_tokens,
    }


def append_response_to_conversation(
    conversation: list[dict[str, Any]],
    output: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    updated = list(conversation)
    for item in output:
        if item.get("type") == "message":
            updated.append({"role": "assistant", "content": collect_output_text([item])})
        elif item.get("type") == "function_call":
            updated.append(
                {
                    "role": "assistant",
                    "content": (
                        "[Previous tool call record - do not repeat as an answer]\n"
                        f"name: {item.get('name')}\n"
                        f"call_id: {item.get('call_id')}\n"
                        f"arguments_json: {item.get('arguments', '')}"
                    ),
                }
            )
        elif item.get("type") == "custom_tool_call":
            updated.append(
                {
                    "role": "assistant",
                    "content": (
                        "[Previous custom tool call record - do not repeat as an answer]\n"
                        f"name: {item.get('name')}\n"
                        f"call_id: {item.get('call_id')}\n"
                        f"input:\n{item.get('input', '')}"
                    ),
                }
            )
    return updated
