from tool_bridge import build_tool_prompt, parse_tool_calls


TOOLS = [
    {
        "type": "function",
        "name": "exec_command",
        "description": "Run a command",
        "parameters": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
            "additionalProperties": False,
        },
    }
]


def test_tool_prompt_contains_declared_schema():
    prompt = build_tool_prompt(TOOLS, parallel_tool_calls=False)
    assert "<tool_call>" in prompt
    assert "exec_command" in prompt
    assert "at most one" in prompt


def test_parse_valid_tool_call():
    result = parse_tool_calls('<tool_call>{"name":"exec_command","arguments":{"cmd":"pwd"}}</tool_call>', TOOLS)
    assert not result.errors
    assert len(result.calls) == 1
    item = result.calls[0].to_response_item()
    assert item["type"] == "function_call"
    assert item["name"] == "exec_command"
    assert item["arguments"] == '{"cmd":"pwd"}'


def test_parse_invalid_json_is_reported():
    result = parse_tool_calls("<tool_call>{bad json}</tool_call>", TOOLS)
    assert result.calls == []
    assert result.errors
    assert "Invalid tool_call JSON" in result.errors[0]


def test_parse_unknown_tool_is_reported():
    result = parse_tool_calls('<tool_call>{"name":"missing","arguments":{}}</tool_call>', TOOLS)
    assert result.calls == []
    assert result.errors == ["Unknown tool name: missing"]


def test_parse_schema_mismatch_is_reported():
    result = parse_tool_calls('<tool_call>{"name":"exec_command","arguments":{"cmd":123}}</tool_call>', TOOLS)
    assert result.calls == []
    assert "argument cmd should be string" in result.errors[0]


def test_parse_custom_tool_call():
    tools = [{"type": "custom", "name": "apply_patch", "format": {"type": "grammar"}}]
    result = parse_tool_calls('<tool_call>{"name":"apply_patch","input":"*** Begin Patch\\n*** End Patch\\n"}</tool_call>', tools)
    assert not result.errors
    item = result.calls[0].to_response_item()
    assert item["type"] == "custom_tool_call"
    assert item["name"] == "apply_patch"
    assert "Begin Patch" in item["input"]


def test_parse_codex_named_xml_tool_call():
    result = parse_tool_calls('<exec_command>{"cmd":"pwd && ls -la"}</exec_command>', TOOLS)
    assert not result.errors
    item = result.calls[0].to_response_item()
    assert item["type"] == "function_call"
    assert item["name"] == "exec_command"
    assert item["arguments"] == '{"cmd":"pwd && ls -la"}'


def test_parse_update_plan_named_xml_and_repair_empty_item():
    tools = [
        {
            "type": "function",
            "name": "update_plan",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"step": {"type": "string"}, "status": {"type": "string"}},
                            "required": ["step", "status"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["plan"],
                "additionalProperties": False,
            },
        }
    ]
    text = """
<update_plan>
{"plan":[{"step":"创建一个简单的猜数字游戏","status":"in_progress"},{}]}
</update_plan>
</tool_call>
"""
    result = parse_tool_calls(text, tools)
    assert not result.errors
    item = result.calls[0].to_response_item()
    assert item["name"] == "update_plan"
    assert item["arguments"] == '{"plan":[{"step":"创建一个简单的猜数字游戏","status":"in_progress"}]}'
    assert result.stripped_text == ""


def test_parse_legacy_tool_record_text():
    text = 'Tool call requested: exec_command call_id=call_123 arguments={"cmd":"cat hello.py"}'
    result = parse_tool_calls(text, TOOLS)
    assert not result.errors
    item = result.calls[0].to_response_item()
    assert item["name"] == "exec_command"
    assert item["arguments"] == '{"cmd":"cat hello.py"}'


def test_parse_codex_nested_xml_arguments_inside_tool_call():
    text = """<tool_call>
<exec_command>
<cmd>cat > hello.py << 'EOF'
#!/usr/bin/env python3
print("Hello from SDU Codex")
EOF</cmd>
</exec_command>
</tool_call>"""
    result = parse_tool_calls(text, TOOLS)
    assert not result.errors
    item = result.calls[0].to_response_item()
    assert item["name"] == "exec_command"
    arguments = item["arguments"]
    assert "hello.py" in arguments
    assert "Hello from SDU Codex" in arguments


def test_parse_self_closing_xml_attribute_tool_call():
    result = parse_tool_calls('<tool_call><exec_command cmd="cat hello.py" /></tool_call>', TOOLS)
    assert not result.errors
    item = result.calls[0].to_response_item()
    assert item["name"] == "exec_command"
    assert item["arguments"] == '{"cmd":"cat hello.py"}'


def test_parse_previous_tool_record_text():
    text = """[Previous tool call record - do not repeat as an answer]
name: exec_command
call_id: call_123
arguments_json: {"cmd":"cat hello.py"}"""
    result = parse_tool_calls(text, TOOLS)
    assert not result.errors
    item = result.calls[0].to_response_item()
    assert item["name"] == "exec_command"
    assert item["arguments"] == '{"cmd":"cat hello.py"}'
