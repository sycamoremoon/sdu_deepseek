import json

import pytest


def test_responses_non_stream_text(client, mock_sdu):
    mock_sdu([{"content": "你好", "reasoning_content": ""}])
    response = client.post(
        "/v1/responses",
        json={"model": "deepseek-ai/DeepSeek-V3.2", "input": "ping", "stream": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "response"
    assert body["output_text"] == "你好"
    assert body["output"][0]["content"][0]["type"] == "output_text"


@pytest.mark.parametrize(
    "model",
    [
        "deepseek-ai/DeepSeek-V3.2",
        "deepseek-ai/DeepSeek-V3.2-think",
        "deepseek-ai/DeepSeek-V4",
    ],
)
def test_responses_non_stream_text_for_supported_deepseek_models(client, mock_sdu, model):
    mock_sdu([{"content": f"answer for {model}", "reasoning_content": ""}])
    response = client.post(
        "/v1/responses",
        json={"model": model, "input": "ping", "stream": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == model
    assert body["output_text"] == f"answer for {model}"


def test_responses_stream_text(client, mock_sdu):
    mock_sdu([{"content": "你", "reasoning_content": ""}, {"content": "好", "reasoning_content": ""}])
    with client.stream(
        "POST",
        "/v1/responses",
        json={"model": "deepseek-ai/DeepSeek-V3.2", "input": "ping", "stream": True},
    ) as response:
        text = response.read().decode("utf-8")
    assert response.status_code == 200
    assert "event: response.created" in text
    assert "event: response.output_text.delta" in text
    assert "event: response.completed" in text
    assert "你好" in text


def test_reasoning_mapping_non_stream(client, mock_sdu):
    mock_sdu([{"content": "answer", "reasoning_content": "reason"}])
    response = client.post(
        "/v1/responses",
        json={"model": "deepseek-ai/DeepSeek-R1", "input": "ping"},
    )
    body = response.json()
    assert body["reasoning"]["summary"][0]["text"] == "reason"
    assert body["usage"]["output_tokens_details"]["reasoning_tokens"] >= 1


def test_reasoning_content_does_not_pollute_output_text(client, mock_sdu):
    mock_sdu([{"content": "最终回答", "reasoning_content": "这里是思考"}])
    response = client.post(
        "/v1/responses",
        json={"model": "deepseek-ai/DeepSeek-V3.2-think", "input": "ping"},
    )
    body = response.json()
    assert body["output_text"] == "最终回答"
    assert "这里是思考" not in body["output_text"]
    assert body["reasoning"]["summary"][0]["text"] == "这里是思考"


def test_think_tags_do_not_pollute_output_text(client, mock_sdu):
    mock_sdu([{"content": "<think>这里是思考过程</think>\n最终回答", "reasoning_content": ""}])
    response = client.post(
        "/v1/responses",
        json={"model": "deepseek-ai/DeepSeek-V3.2-think", "input": "ping"},
    )
    body = response.json()
    assert body["output_text"].strip() == "最终回答"
    assert "<think>" not in body["output_text"]
    assert "这里是思考过程" not in body["output_text"]


def test_tool_call_response(client, mock_sdu):
    mock_sdu([{"content": '<tool_call>{"name":"exec_command","arguments":{"cmd":"pwd"}}</tool_call>', "reasoning_content": ""}])
    response = client.post(
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "input": "need pwd",
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        },
    )
    body = response.json()
    assert body["output"][0]["type"] == "function_call"
    assert body["output"][0]["name"] == "exec_command"


def test_codex_named_xml_tool_call_response(client, mock_sdu):
    mock_sdu(
        [
            {
                "content": '<update_plan>{"plan":[{"step":"创建文件","status":"in_progress"},{}]}</update_plan></tool_call>',
                "reasoning_content": "",
            }
        ]
    )
    response = client.post(
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "input": "continue",
            "tools": [
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
                                },
                            }
                        },
                        "required": ["plan"],
                    },
                }
            ],
        },
    )
    body = response.json()
    assert body["output"][0]["type"] == "function_call"
    assert body["output"][0]["name"] == "update_plan"
    assert json.loads(body["output"][0]["arguments"]) == {"plan": [{"step": "创建文件", "status": "in_progress"}]}


def test_tool_call_output_roundtrip(client, mock_sdu):
    seen = {}

    def fake_chat(content, history, config):
        seen["content"] = content
        yield {"content": "final", "reasoning_content": ""}

    import main

    from pytest import MonkeyPatch

    monkeypatch = MonkeyPatch()
    monkeypatch.setattr(main.sduwrap, "chat", fake_chat)
    try:
        response = client.post(
            "/v1/responses",
            json={
                "model": "deepseek-ai/DeepSeek-V3.2",
                "input": [{"type": "function_call_output", "call_id": "call_1", "output": "result text"}],
            },
        )
    finally:
        monkeypatch.undo()
    assert response.status_code == 200
    assert "result text" in seen["content"]
    assert response.json()["output_text"] == "final"


def test_previous_response_id_and_get_delete(client, mock_sdu):
    mock_sdu([{"content": "first", "reasoning_content": ""}])
    first = client.post("/v1/responses", json={"model": "deepseek-ai/DeepSeek-V3.2", "input": "one"}).json()
    response_id = first["id"]

    mock_sdu([{"content": "second", "reasoning_content": ""}])
    second = client.post(
        "/v1/responses",
        json={"model": "deepseek-ai/DeepSeek-V3.2", "input": "two", "previous_response_id": response_id, "store": False},
    )
    assert second.status_code == 200
    assert second.json()["previous_response_id"] == response_id

    fetched = client.get(f"/v1/responses/{response_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == response_id

    input_items = client.get(f"/v1/responses/{response_id}/input_items")
    assert input_items.status_code == 200
    assert input_items.json()["object"] == "list"

    deleted = client.delete(f"/v1/responses/{response_id}")
    assert deleted.json()["deleted"] is True
    assert client.get(f"/v1/responses/{response_id}").status_code == 404


def test_input_image_returns_clear_error(client):
    response = client.post(
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": "data:image/png;base64,abc"}],
                }
            ],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_input_image"


def test_alias_without_v1(client, mock_sdu):
    mock_sdu([{"content": "alias", "reasoning_content": ""}])
    response = client.post("/responses", json={"model": "deepseek-ai/DeepSeek-V3.2", "input": "ping"})
    assert response.status_code == 200
    assert response.json()["output_text"] == "alias"


def test_stream_tool_call_events(client, mock_sdu):
    mock_sdu([{"content": '<tool_call>{"name":"exec_command","arguments":{"cmd":"pwd"}}</tool_call>', "reasoning_content": ""}])
    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "input": "need pwd",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        },
    ) as response:
        text = response.read().decode("utf-8")
    assert "event: response.function_call_arguments.delta" in text
    assert "event: response.completed" in text
    assert '"name":"exec_command"' in text


def test_custom_tool_call_response_with_unclosed_wrapper(client, mock_sdu):
    mock_sdu(
        [
            {
                "content": '<tool_call>{"name":"apply_patch","input":"*** Begin Patch\n*** Add File: hello.py\n+print(\\"hi\\")\n*** End Patch\n"}]()}',
                "reasoning_content": "",
            }
        ]
    )
    response = client.post(
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V4",
            "input": "write file",
            "tools": [
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "format": {"type": "grammar"},
                }
            ],
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["output"][0]["type"] == "custom_tool_call"
    assert body["output"][0]["name"] == "apply_patch"
    assert "Add File: hello.py" in body["output"][0]["input"]


def test_custom_tool_call_response_with_old_codex_function_like_apply_patch(client, mock_sdu):
    mock_sdu(
        [
            {
                "content": '<tool_call>\n<apply_patch>\n*** Begin Patch\n*** New File: file_organizer.py\n+print("hi")\n*** End Patch\n</apply_patch>\n</tool_call>',
                "reasoning_content": "",
            }
        ]
    )
    response = client.post(
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2-think",
            "input": "write file",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "apply_patch",
                        "description": "Use the `apply_patch` tool to edit files. This is a FREEFORM tool.",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
                    },
                }
            ],
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["output"][0]["type"] == "custom_tool_call"
    assert body["output"][0]["name"] == "apply_patch"
    assert "New File: file_organizer.py" in body["output"][0]["input"]


def test_think_apply_patch_without_custom_tool_falls_back_to_exec_command(client, mock_sdu):
    mock_sdu(
        [
            {
                "content": (
                    "我来创建一个Python文件整理程序。\n\n"
                    "<think>我需要修改文件</think>\n"
                    "<tool_call>\n<apply_patch>\n"
                    "*** Begin Patch\n"
                    "*** Add File: file_organizer.py\n"
                    '+print("hi")\n'
                    "*** End Patch\n"
                    "</apply_patch>\n</tool_call>"
                ),
                "reasoning_content": "",
            }
        ]
    )
    response = client.post(
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2-think",
            "input": "write file",
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["output_text"] == ""
    assert body["output"][0]["type"] == "function_call"
    assert body["output"][0]["name"] == "exec_command"
    arguments = json.loads(body["output"][0]["arguments"])
    assert "apply_patch <<'PATCH'" in arguments["cmd"]
    assert "Add File: file_organizer.py" in arguments["cmd"]
    assert "<tool_call>" not in body["output_text"]
    assert "<apply_patch>" not in body["output_text"]


def test_function_tool_response_with_top_level_arguments(client, mock_sdu):
    mock_sdu(
        [
            {
                "content": '<tool_call>{"name":"exec_command","cmd":"python3 /home/test/test2/file_organizer.py --help","workdir":"/home/test/test2"}</tool_call>',
                "reasoning_content": "",
            }
        ]
    )
    response = client.post(
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V4",
            "input": "run help",
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cmd": {"type": "string"},
                            "workdir": {"type": "string"},
                        },
                        "required": ["cmd"],
                        "additionalProperties": False,
                    },
                }
            ],
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["output"][0]["type"] == "function_call"
    assert body["output"][0]["name"] == "exec_command"
    assert json.loads(body["output"][0]["arguments"]) == {
        "cmd": "python3 /home/test/test2/file_organizer.py --help",
        "workdir": "/home/test/test2",
    }


def test_v4_bare_apply_patch_without_custom_tool_falls_back_to_exec_command(client, mock_sdu):
    mock_sdu(
        [
            {
                "content": (
                    "我来为你创建一个文件整理工具。\n\n"
                    "<apply_patch>\n"
                    "*** Begin Patch\n"
                    "*** Create File: /home/damon/test/file_organizer.py\n"
                    "@@\n"
                    '+print("organizer ok")\n'
                    "*** End Patch\n"
                    "</apply_patch>"
                ),
                "reasoning_content": "",
            }
        ]
    )
    response = client.post(
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V4",
            "input": "write file",
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["output_text"] == ""
    assert body["output"][0]["type"] == "function_call"
    assert body["output"][0]["name"] == "exec_command"
    arguments = json.loads(body["output"][0]["arguments"])
    assert "apply_patch <<'PATCH'" in arguments["cmd"]
    assert "Add File: /home/damon/test/file_organizer.py" in arguments["cmd"]
    assert "Create File" not in arguments["cmd"]
    assert "<apply_patch>" not in body["output_text"]


def test_stream_think_then_tool_call_events(client, mock_sdu):
    mock_sdu(
        [
            {
                "content": '<think>先检查目录，再执行命令。</think><exec_command>{"cmd":"pwd"}</exec_command>',
                "reasoning_content": "",
            }
        ]
    )
    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2-think",
            "input": "need pwd",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        },
    ) as response:
        text = response.read().decode("utf-8")
    assert response.status_code == 200
    assert "event: response.function_call_arguments.delta" in text
    assert '"name":"exec_command"' in text
    assert "<think>" not in text
    assert "response.reasoning_summary_text.delta" not in text


def test_stream_codex_named_xml_tool_call_events(client, mock_sdu):
    mock_sdu([{"content": '<update_plan>{"plan":[{"step":"创建文件","status":"in_progress"},{}]}</update_plan></tool_call>', "reasoning_content": ""}])
    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "input": "continue",
            "stream": True,
            "tools": [
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
                                },
                            }
                        },
                        "required": ["plan"],
                    },
                }
            ],
        },
    ) as response:
        text = response.read().decode("utf-8")
    assert "event: response.function_call_arguments.delta" in text
    assert "event: response.completed" in text
    assert '"name":"update_plan"' in text
    assert "{}" not in text


def test_stream_legacy_tool_record_events(client, mock_sdu):
    mock_sdu([{"content": 'Tool call requested: exec_command call_id=call_1 arguments={"cmd":"cat hello.py"}', "reasoning_content": ""}])
    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "input": "continue",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        },
    ) as response:
        text = response.read().decode("utf-8")
    assert "event: response.function_call_arguments.delta" in text
    assert '"name":"exec_command"' in text
    assert "Tool call requested:" not in text


def test_stream_nested_xml_argument_tool_events(client, mock_sdu):
    mock_sdu(
        [
            {
                "content": """<tool_call>
<exec_command>
<cmd>cat > hello.py << 'EOF'
#!/usr/bin/env python3
print("Hello from SDU Codex")
EOF</cmd>
</exec_command>
</tool_call>""",
                "reasoning_content": "",
            }
        ]
    )
    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "input": "create file",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        },
    ) as response:
        text = response.read().decode("utf-8")
    assert "event: response.function_call_arguments.delta" in text
    assert '"name":"exec_command"' in text
    assert "Hello from SDU Codex" in text


def test_stream_self_closing_xml_attribute_tool_events(client, mock_sdu):
    mock_sdu([{"content": '<tool_call><exec_command cmd="cat hello.py" /></tool_call>', "reasoning_content": ""}])
    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "input": "inspect file",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        },
    ) as response:
        text = response.read().decode("utf-8")
    assert "event: response.function_call_arguments.delta" in text
    assert '"arguments":"{\\"cmd\\":\\"cat hello.py\\"}"' in text


def test_stream_think_apply_patch_custom_tool_is_buffered_and_not_leaked(client, mock_sdu):
    mock_sdu(
        [
            {"content": "<think>我需要修改", "reasoning_content": ""},
            {"content": "文件</think>\n我来创建文件。\n<tool_call>\n<apply_patch>\n*** Begin Patch\n", "reasoning_content": ""},
            {"content": "*** Add File: file_organizer.py\n+print(\"hi\")\n*** End Patch\n</apply_patch>\n</tool_call>", "reasoning_content": ""},
        ]
    )
    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2-think",
            "input": "write file",
            "stream": True,
            "tools": [
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Use the `apply_patch` tool to edit files. This is a FREEFORM tool.",
                    "format": {"type": "grammar"},
                }
            ],
        },
    ) as response:
        text = response.read().decode("utf-8")
    assert response.status_code == 200
    assert "event: response.custom_tool_call_input.delta" in text
    assert "event: response.completed" in text
    assert '"name":"apply_patch"' in text
    assert "<tool_call>" not in text
    assert "<apply_patch>" not in text
    assert "<think>" not in text
    assert "response.output_text.delta" not in text


def test_stream_events_are_json(client, mock_sdu):
    mock_sdu([{"content": "ok", "reasoning_content": ""}])
    with client.stream(
        "POST",
        "/v1/responses",
        json={"model": "deepseek-ai/DeepSeek-V3.2", "input": "ping", "stream": True},
    ) as response:
        text = response.read().decode("utf-8")
    data_lines = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
    assert data_lines
    for line in data_lines:
        json.loads(line)
