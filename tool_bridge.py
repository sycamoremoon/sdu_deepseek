from __future__ import annotations

import json
import re
import uuid
from html import unescape
from dataclasses import dataclass
from typing import Any


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
TOOL_CALL_TAG_RE = re.compile(r"</?tool_call>", re.IGNORECASE)
TOOL_CALL_OPEN_RE = re.compile(r"<tool_call>\s*", re.IGNORECASE)
CODE_FENCE_RE = re.compile(r"```(?:json|tool_call)?\s*(?P<body>\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
TOOL_RECORD_RE = re.compile(
    r"(?:^|\n)\s*Tool call requested:\s*(?P<name>[A-Za-z_][\w.-]*)\s+"
    r"(?:call_id=\S+\s+)?arguments=(?P<arguments>\{.*?\})\s*(?=$|\n)",
    re.DOTALL,
)
CUSTOM_TOOL_RECORD_RE = re.compile(
    r"(?:^|\n)\s*Custom tool call requested:\s*(?P<name>[A-Za-z_][\w.-]*)\s+"
    r"(?:call_id=\S+\s+)?input=(?P<input>.*?)(?=$|\n)",
    re.DOTALL,
)
PREVIOUS_TOOL_RECORD_RE = re.compile(
    r"\[Previous tool call record[^\]]*\]\s*"
    r"name:\s*(?P<name>[A-Za-z_][\w.-]*)\s+"
    r"call_id:\s*\S+\s+"
    r"arguments_json:\s*(?P<arguments>\{.*?\})\s*(?=$|\n)",
    re.DOTALL,
)
KNOWN_FREEFORM_TOOL_NAMES = {"apply_patch"}


@dataclass
class ToolSpec:
    name: str
    description: str = ""
    parameters: dict[str, Any] | None = None
    type: str = "function"
    raw: dict[str, Any] | None = None

    @property
    def is_function_like(self) -> bool:
        return self.type == "function"


@dataclass
class ParsedToolCall:
    name: str
    arguments: dict[str, Any] | str
    call_id: str
    item_type: str = "function_call"

    def to_response_item(self) -> dict[str, Any]:
        if self.item_type == "custom_tool_call":
            custom_input = self.arguments if isinstance(self.arguments, str) else json.dumps(self.arguments, ensure_ascii=False)
            return {
                "id": f"ctc_{uuid.uuid4().hex[:16]}",
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": self.call_id,
                "name": self.name,
                "input": custom_input,
            }
        return {
            "id": f"fc_{uuid.uuid4().hex[:16]}",
            "type": "function_call",
            "status": "completed",
            "call_id": self.call_id,
            "name": self.name,
            "arguments": json.dumps(self.arguments, ensure_ascii=False, separators=(",", ":")),
        }


@dataclass
class ToolParseResult:
    calls: list[ParsedToolCall]
    errors: list[str]
    stripped_text: str
    had_tool_markup: bool = False


@dataclass
class _ToolCallCandidate:
    source: str
    span: tuple[int, int]
    payload: str
    name: str | None = None


def extract_tool_specs(raw_tools: list[Any] | None) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for raw in raw_tools or []:
        if not isinstance(raw, dict):
            continue
        tool_type = str(raw.get("type") or "function")
        format_info = raw.get("format") if isinstance(raw.get("format"), dict) else {}
        if tool_type == "function" and isinstance(raw.get("function"), dict):
            function = raw["function"]
            name = function.get("name")
            description = function.get("description") or raw.get("description") or ""
            parameters = function.get("parameters") if isinstance(function.get("parameters"), dict) else {}
        else:
            name = raw.get("name") or raw.get("type")
            description = raw.get("description") or ""
            parameters = raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {}
        if not name:
            continue
        normalized_name = str(name)
        normalized_description = str(description)
        inferred_custom = (
            tool_type == "custom"
            or normalized_name in KNOWN_FREEFORM_TOOL_NAMES
            or format_info.get("type") == "grammar"
            or "FREEFORM" in normalized_description.upper()
        )
        specs.append(
            ToolSpec(
                name=normalized_name,
                description=normalized_description,
                parameters=parameters,
                type="custom" if inferred_custom else tool_type,
                raw=raw,
            )
        )
    return specs


def build_tool_prompt(tools: list[Any] | None, parallel_tool_calls: bool | None) -> str:
    specs = extract_tool_specs(tools)
    if not specs:
        return ""

    serializable = []
    for spec in specs:
        serializable.append(
            {
                "name": spec.name,
                "type": spec.type,
                "description": spec.description,
                "parameters": spec.parameters or {},
            }
        )

    limit_text = "You may output multiple <tool_call> blocks." if parallel_tool_calls else "Output at most one <tool_call> block."
    return (
        "Tool calling compatibility instructions:\n"
        "The backend does not provide native tool execution. If a tool is needed, respond only with strict tool call markup:\n"
        '<tool_call>{"name":"tool_name","arguments":{...}}</tool_call>\n'
        "Codex-style named XML is also accepted when you cannot produce the JSON wrapper:\n"
        '<tool_name>{"arg":"value"}</tool_name>\n'
        "For custom/freeform tools, prefer named XML so the body is not JSON-escaped, for example:\n"
        "<apply_patch>\n*** Begin Patch\n...\n*** End Patch\n</apply_patch>\n"
        "If you must use the JSON wrapper for a custom/freeform tool, use a string field named input.\n"
        f"{limit_text}\n"
        "When calling a tool, output only the tool call and stop. Do not wrap it in Markdown or explanatory prose.\n"
        "Use only tool names declared below. If no tool is needed, answer normally.\n"
        "Declared tools:\n"
        f"{json.dumps(serializable, ensure_ascii=False, indent=2)}"
    )


def parse_tool_calls(text: str, raw_tools: list[Any] | None, parallel_tool_calls: bool | None = False) -> ToolParseResult:
    specs = extract_tool_specs(raw_tools)
    specs_by_name = {spec.name: spec for spec in specs}
    candidates = _extract_tool_call_candidates(text or "", specs_by_name)
    if not candidates:
        return ToolParseResult(calls=[], errors=[], stripped_text=text, had_tool_markup=False)

    calls: list[ParsedToolCall] = []
    errors: list[str] = []
    consumed_spans: list[tuple[int, int]] = []
    candidate_spans = [candidate.span for candidate in candidates]
    for candidate in candidates:
        parsed = _parse_candidate(candidate, specs_by_name)
        if parsed[1]:
            errors.append(parsed[1])
            continue
        name, payload = parsed[0]
        spec = specs_by_name.get(name)
        if not spec:
            errors.append(f"Unknown tool name: {name}")
            continue
        call = _build_parsed_call(name, payload, spec)
        if isinstance(call, str):
            errors.append(call)
            continue
        calls.append(call)
        consumed_spans.append(candidate.span)

        if calls and not parallel_tool_calls:
            break

    stripped_spans = consumed_spans if calls else candidate_spans
    stripped_text = _strip_consumed_tool_markup(text or "", stripped_spans)
    return ToolParseResult(calls=calls, errors=errors, stripped_text=stripped_text, had_tool_markup=True)


def _extract_tool_call_candidates(text: str, specs_by_name: dict[str, ToolSpec]) -> list[_ToolCallCandidate]:
    candidates: list[_ToolCallCandidate] = []
    wrapper_spans: list[tuple[int, int]] = []
    for match in TOOL_CALL_RE.finditer(text):
        wrapper_spans.append(match.span())
        candidates.append(_ToolCallCandidate(source="tool_call", span=match.span(), payload=match.group(1).strip()))
    for match in TOOL_CALL_OPEN_RE.finditer(text):
        span = (match.start(), len(text))
        if _span_inside_any(span, wrapper_spans):
            continue
        if "</tool_call>" in text[match.end():]:
            continue
        candidates.append(_ToolCallCandidate(source="open_tool_call", span=span, payload=text[match.end():].strip()))
    for match in CODE_FENCE_RE.finditer(text):
        candidates.append(_ToolCallCandidate(source="code_fence", span=match.span(), payload=match.group("body").strip()))

    stripped = text.strip()
    if stripped.startswith("{") and '"name"' in stripped:
        candidates.append(_ToolCallCandidate(source="json_object", span=(0, len(text)), payload=stripped))

    if not specs_by_name:
        return candidates

    escaped_names = sorted((re.escape(name) for name in specs_by_name), key=len, reverse=True)
    if not escaped_names:
        return candidates
    named_re = re.compile(rf"<(?P<name>{'|'.join(escaped_names)})>\s*(?P<body>.*?)\s*</(?P=name)>", re.DOTALL)
    for match in named_re.finditer(text):
        if _span_inside_any(match.span(), wrapper_spans):
            continue
        candidates.append(
            _ToolCallCandidate(
                source="named_xml",
                span=match.span(),
                payload=match.group("body").strip(),
                name=match.group("name"),
            )
        )
    self_closing_re = re.compile(rf"<(?P<name>{'|'.join(escaped_names)})(?P<attrs>\s+[^>]*)/>", re.DOTALL)
    for match in self_closing_re.finditer(text):
        if _span_inside_any(match.span(), wrapper_spans):
            continue
        candidates.append(
            _ToolCallCandidate(
                source="self_closing_xml",
                span=match.span(),
                payload=match.group("attrs").strip(),
                name=match.group("name"),
            )
        )
    for match in TOOL_RECORD_RE.finditer(text):
        candidates.append(
            _ToolCallCandidate(
                source="tool_record",
                span=match.span(),
                payload=match.group("arguments").strip(),
                name=match.group("name"),
            )
        )
    for match in CUSTOM_TOOL_RECORD_RE.finditer(text):
        candidates.append(
            _ToolCallCandidate(
                source="custom_tool_record",
                span=match.span(),
                payload=match.group("input").strip(),
                name=match.group("name"),
            )
        )
    for match in PREVIOUS_TOOL_RECORD_RE.finditer(text):
        candidates.append(
            _ToolCallCandidate(
                source="tool_record",
                span=match.span(),
                payload=match.group("arguments").strip(),
                name=match.group("name"),
            )
        )
    candidates.sort(key=lambda item: item.span[0])
    return candidates


def _span_inside_any(span: tuple[int, int], containers: list[tuple[int, int]]) -> bool:
    return any(start <= span[0] and span[1] <= end for start, end in containers)


def _parse_candidate(
    candidate: _ToolCallCandidate,
    specs_by_name: dict[str, ToolSpec],
) -> tuple[tuple[str, dict[str, Any]] | None, str | None]:
    if candidate.source in {"named_xml", "tool_record", "custom_tool_record", "self_closing_xml"}:
        if not candidate.name:
            return None, "Named tool call missing tool name."
        spec = specs_by_name.get(candidate.name)
        if not spec:
            return None, f"Unknown tool name: {candidate.name}"
        if candidate.source == "self_closing_xml":
            payload = _parse_xml_attributes(candidate.payload, spec)
            if payload is None:
                return None, f"Invalid {candidate.name} XML attributes."
            return (candidate.name, payload), None
        if spec.type == "custom" or candidate.source == "custom_tool_record":
            return (candidate.name, {"input": _strip_code_fence(candidate.payload)}), None
        payload, error = _loads_json_object(candidate.payload)
        if error:
            xml_arguments = _parse_xml_arguments(candidate.payload, spec.parameters or {})
            if xml_arguments is None:
                return None, f"Invalid {candidate.name} tool JSON: {error}"
            payload = xml_arguments
        return (candidate.name, {"arguments": payload}), None

    payload, error = _loads_json_object(candidate.payload)
    if error:
        nested = _parse_wrapped_named_xml(candidate.payload, specs_by_name)
        if nested:
            return nested, None
        apply_patch_shell = _parse_apply_patch_shell_fallback(candidate.payload, specs_by_name)
        if apply_patch_shell:
            return apply_patch_shell, None
        loose = _parse_loose_tool_object(candidate.payload, specs_by_name)
        if loose:
            return loose, None
        return None, f"Invalid tool_call JSON: {error}"

    name = payload.get("name")
    if not isinstance(name, str) or not name:
        shorthand = _parse_tool_name_shorthand(payload, specs_by_name)
        if shorthand:
            return shorthand, None
        return None, "Tool call payload missing string name."
    normalized, normalize_error = _normalize_named_payload(name, payload, specs_by_name)
    if normalize_error:
        return None, normalize_error
    return (name, normalized), None


def _parse_apply_patch_shell_fallback(
    payload: str,
    specs_by_name: dict[str, ToolSpec],
) -> tuple[str, dict[str, Any]] | None:
    if "apply_patch" in specs_by_name:
        return None
    shell_spec = _select_shell_like_tool(specs_by_name)
    if shell_spec is None:
        return None

    match = re.search(r"<apply_patch>\s*(?P<body>.*?)\s*</apply_patch>", payload, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    patch = _strip_code_fence(match.group("body").strip())
    if not patch.startswith("*** Begin Patch") or "*** End Patch" not in patch:
        return None

    command_key = _shell_command_key(shell_spec)
    command = _apply_patch_command(patch)
    return shell_spec.name, {"arguments": {command_key: command}}


def _select_shell_like_tool(specs_by_name: dict[str, ToolSpec]) -> ToolSpec | None:
    for name in ("exec_command", "shell", "local_shell"):
        spec = specs_by_name.get(name)
        if spec and spec.type != "custom":
            return spec
    return None


def _shell_command_key(spec: ToolSpec) -> str:
    properties = spec.parameters.get("properties", {}) if isinstance(spec.parameters, dict) else {}
    if isinstance(properties, dict):
        if "cmd" in properties:
            return "cmd"
        if "command" in properties:
            return "command"
    return "cmd"


def _apply_patch_command(patch: str) -> str:
    delimiter = "PATCH"
    while delimiter in patch:
        delimiter += "_EOF"
    return f"apply_patch <<'{delimiter}'\n{patch}\n{delimiter}"


def _parse_wrapped_named_xml(
    payload: str,
    specs_by_name: dict[str, ToolSpec],
) -> tuple[str, dict[str, Any]] | None:
    if not specs_by_name:
        return None
    escaped_names = sorted((re.escape(name) for name in specs_by_name), key=len, reverse=True)
    named_re = re.compile(rf"^\s*<(?P<name>{'|'.join(escaped_names)})>\s*(?P<body>.*?)\s*</(?P=name)>\s*$", re.DOTALL)
    match = named_re.match(payload)
    if not match:
        return _parse_self_closing_named_xml(payload, specs_by_name)
    name = match.group("name")
    spec = specs_by_name[name]
    if spec.type == "custom":
        return name, {"input": _strip_code_fence(match.group("body").strip())}
    parsed, error = _loads_json_object(match.group("body").strip())
    if error:
        parsed = _parse_xml_arguments(match.group("body").strip(), spec.parameters or {})
        if parsed is None:
            return None
    return name, {"arguments": parsed}


def _parse_tool_name_shorthand(
    payload: dict[str, Any],
    specs_by_name: dict[str, ToolSpec],
) -> tuple[str, dict[str, Any]] | None:
    if len(payload) != 1:
        return None
    name = next(iter(payload))
    if name not in specs_by_name:
        return None
    spec = specs_by_name[name]
    value = payload[name]
    if spec.type == "custom":
        return name, {"input": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}
    return name, {"arguments": value}


def _normalize_named_payload(
    name: str,
    payload: dict[str, Any],
    specs_by_name: dict[str, ToolSpec],
) -> tuple[dict[str, Any] | None, str | None]:
    spec = specs_by_name.get(name)
    if not spec:
        return None, f"Unknown tool name: {name}"

    if spec.type == "custom":
        if isinstance(payload.get("input"), str):
            return {"input": payload["input"]}, None
        if isinstance(payload.get("arguments"), str):
            return {"input": payload["arguments"]}, None
        return None, f"Custom tool {name} requires string input."

    if isinstance(payload.get("arguments"), dict):
        return {"arguments": payload["arguments"]}, None
    if isinstance(payload.get("arguments"), str):
        return {"arguments": payload["arguments"]}, None

    reserved_keys = {"name", "type", "call_id", "status"}
    arguments = {key: value for key, value in payload.items() if key not in reserved_keys}
    if arguments:
        return {"arguments": arguments}, None
    return {"arguments": {}}, None


def _parse_self_closing_named_xml(
    payload: str,
    specs_by_name: dict[str, ToolSpec],
) -> tuple[str, dict[str, Any]] | None:
    if not specs_by_name:
        return None
    escaped_names = sorted((re.escape(name) for name in specs_by_name), key=len, reverse=True)
    self_closing_re = re.compile(rf"^\s*<(?P<name>{'|'.join(escaped_names)})(?P<attrs>\s+[^>]*)/>\s*$", re.DOTALL)
    match = self_closing_re.match(payload)
    if not match:
        return None
    name = match.group("name")
    parsed = _parse_xml_attributes(match.group("attrs").strip(), specs_by_name[name])
    if parsed is None:
        return None
    return name, parsed


def _parse_loose_tool_object(
    payload: str,
    specs_by_name: dict[str, ToolSpec],
) -> tuple[str, dict[str, Any]] | None:
    cleaned = _strip_code_fence(payload).strip()
    name_match = re.search(r'"name"\s*:\s*"(?P<name>[A-Za-z_][\w.-]*)"', cleaned)
    if not name_match:
        return None
    name = name_match.group("name")
    spec = specs_by_name.get(name)
    if not spec:
        return None

    if spec.type != "custom":
        return None

    input_match = re.search(r'"input"\s*:\s*"', cleaned)
    if not input_match:
        return None
    raw_input = cleaned[input_match.end():]
    end_match = re.search(r'"\s*}\s*(?:</tool_call>|\]\(\)|\)\s*)?$', raw_input, re.DOTALL)
    if end_match:
        raw_input = raw_input[:end_match.start()]
    return name, {"input": _decode_loose_json_string(raw_input)}


def _parse_xml_attributes(attrs: str, spec: ToolSpec) -> dict[str, Any] | None:
    matches = list(re.finditer(r"(?P<key>[A-Za-z_][\w.-]*)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)", attrs, re.DOTALL))
    if not matches:
        return None
    if spec.type == "custom":
        for match in matches:
            if match.group("key") in {"input", "arguments"}:
                return {"input": unescape(match.group("value"))}
        return {"input": unescape(matches[0].group("value"))}

    properties = spec.parameters.get("properties", {}) if isinstance(spec.parameters, dict) else {}
    if not isinstance(properties, dict):
        properties = {}
    arguments: dict[str, Any] = {}
    for match in matches:
        key = match.group("key")
        expected = properties.get(key, {}).get("type") if isinstance(properties.get(key), dict) else None
        arguments[key] = _coerce_xml_argument(unescape(match.group("value")), expected)
    return {"arguments": arguments}


def _build_parsed_call(name: str, payload: dict[str, Any], spec: ToolSpec) -> ParsedToolCall | str:
    if spec.type == "custom":
        custom_input = payload.get("input")
        if custom_input is None:
            custom_input = payload.get("arguments")
        if not isinstance(custom_input, str):
            return f"Custom tool {name} requires string input."
        return ParsedToolCall(name=name, arguments=custom_input, call_id=f"call_{uuid.uuid4().hex[:16]}", item_type="custom_tool_call")

    arguments = payload.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(_strip_code_fence(arguments))
        except json.JSONDecodeError as exc:
            return f"Arguments for {name} must be JSON: {exc.msg}"
    if not isinstance(arguments, dict):
        return f"Arguments for {name} must be a JSON object."

    arguments = _repair_arguments(arguments, spec.parameters or {})
    schema_errors = validate_arguments(arguments, spec.parameters or {})
    if schema_errors:
        return "; ".join(f"{name}: {error}" for error in schema_errors)
    return ParsedToolCall(name=name, arguments=arguments, call_id=f"call_{uuid.uuid4().hex[:16]}")


def _loads_json_object(raw_payload: str) -> tuple[dict[str, Any] | None, str | None]:
    cleaned = _strip_code_fence(raw_payload)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        try:
            payload, _ = json.JSONDecoder().raw_decode(cleaned)
        except json.JSONDecodeError:
            return None, exc.msg
    if not isinstance(payload, dict):
        return None, "payload must be a JSON object"
    return payload, None


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _decode_loose_json_string(text: str) -> str:
    return (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _parse_xml_arguments(payload: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    matches = list(re.finditer(r"<(?P<key>[A-Za-z_][\w.-]*)>\s*(?P<value>.*?)\s*</(?P=key)>", payload, re.DOTALL))
    if not matches:
        return None
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}

    arguments: dict[str, Any] = {}
    for match in matches:
        key = match.group("key")
        value = _strip_code_fence(unescape(match.group("value")))
        expected = properties.get(key, {}).get("type") if isinstance(properties.get(key), dict) else None
        arguments[key] = _coerce_xml_argument(value, expected)
    return arguments


def _coerce_xml_argument(value: str, expected: Any) -> Any:
    stripped = value.strip()
    if expected in {"object", "array", "number", "integer", "boolean", "null"} or isinstance(expected, list):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    return stripped


def _repair_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Apply narrow repairs for common LLM formatting slips before tool dispatch."""
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return arguments
    repaired = dict(arguments)
    for key, value in arguments.items():
        prop = properties.get(key)
        if not isinstance(prop, dict) or prop.get("type") != "array" or not isinstance(value, list):
            continue
        item_schema = prop.get("items", {})
        required = item_schema.get("required", []) if isinstance(item_schema, dict) else []
        if required:
            repaired[key] = [item for item in value if item != {}]
    return repaired


def _strip_consumed_tool_markup(text: str, consumed_spans: list[tuple[int, int]]) -> str:
    if not consumed_spans:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in sorted(consumed_spans):
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    stripped_text = "".join(pieces)
    stripped_text = TOOL_CALL_TAG_RE.sub("", stripped_text)
    return stripped_text.strip()


def validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    if isinstance(required, list):
        for key in required:
            if key not in arguments:
                errors.append(f"missing required argument {key}")

    if not isinstance(properties, dict):
        return errors

    for key, value in arguments.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            if schema.get("additionalProperties") is False:
                errors.append(f"unexpected argument {key}")
            continue
        expected = prop.get("type")
        if expected and not _matches_json_type(value, expected):
            errors.append(f"argument {key} should be {expected}")
    return errors


def _matches_json_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_json_type(value, item) for item in expected)
    return {
        "string": isinstance(value, str),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "null": value is None,
    }.get(str(expected), True)


def summarize_tool_result(item: dict[str, Any]) -> str:
    call_id = item.get("call_id", "")
    output = item.get("output", "")
    if not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False)
    return f"Tool result for call_id {call_id}:\n{output}"
