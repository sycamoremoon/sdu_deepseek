from fastapi.testclient import TestClient
from openai import BadRequestError, OpenAI

import main


def make_openai_client():
    return OpenAI(
        base_url="http://testserver/v1",
        api_key="dummy",
        http_client=TestClient(main.app),
    )


def test_openai_sdk_responses_create(monkeypatch):
    def fake_chat(content, history, config):
        yield {"content": "sdk ok", "reasoning_content": ""}

    monkeypatch.setattr(main.sduwrap, "chat", fake_chat)
    client = make_openai_client()
    response = client.responses.create(model="deepseek-ai/DeepSeek-V3.2", input="hi")
    assert response.output_text == "sdk ok"


def test_openai_sdk_responses_stream(monkeypatch):
    def fake_chat(content, history, config):
        yield {"content": "s", "reasoning_content": ""}
        yield {"content": "t", "reasoning_content": ""}

    monkeypatch.setattr(main.sduwrap, "chat", fake_chat)
    client = make_openai_client()
    deltas = []
    with client.responses.stream(model="deepseek-ai/DeepSeek-V3.2", input="hi") as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                deltas.append(event.delta)
    assert "".join(deltas) == "st"


def test_openai_sdk_tools_previous_and_image_error(monkeypatch):
    def fake_chat(content, history, config):
        if "tool" in content:
            yield {"content": '<tool_call>{"name":"echo","arguments":{"text":"hello"}}</tool_call>', "reasoning_content": ""}
        else:
            yield {"content": "plain", "reasoning_content": ""}

    monkeypatch.setattr(main.sduwrap, "chat", fake_chat)
    client = make_openai_client()
    tool_response = client.responses.create(
        model="deepseek-ai/DeepSeek-V3.2",
        input="tool please",
        tools=[
            {
                "type": "function",
                "name": "echo",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ],
    )
    assert tool_response.output[0].type == "function_call"

    second = client.responses.create(
        model="deepseek-ai/DeepSeek-V3.2",
        input="continue",
        previous_response_id=tool_response.id,
    )
    assert second.previous_response_id == tool_response.id

    try:
        client.responses.create(
            model="deepseek-ai/DeepSeek-V3.2",
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}],
                }
            ],
        )
    except BadRequestError as exc:
        assert exc.response.json()["error"]["code"] == "unsupported_input_image"
    else:
        raise AssertionError("Expected unsupported input_image error")
