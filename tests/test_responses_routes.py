import json


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
