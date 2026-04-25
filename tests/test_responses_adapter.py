import pytest

from responses_adapter import build_output_items, prepare_responses_request
from responses_models import ResponsesRequest, UnsupportedInputError
from responses_store import ResponseStore


def test_input_string_becomes_current_user_message():
    request = ResponsesRequest(model="deepseek-ai/DeepSeek-V3.2", input="你好")
    prepared = prepare_responses_request(request, ResponseStore())
    assert prepared.current_input == "你好"
    assert prepared.history == []


def test_message_array_preserves_history_and_developer_prefix():
    request = ResponsesRequest(
        model="deepseek-ai/DeepSeek-V3.2",
        instructions="Be concise.",
        input=[
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Dev note."}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "First"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Second"}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Third"}]},
        ],
    )
    prepared = prepare_responses_request(request, ResponseStore())
    assert prepared.current_input == "Third"
    assert prepared.history[0].role == "system"
    assert "Be concise" in prepared.history[0].content
    assert "Dev note" in prepared.history[0].content
    assert [m.role for m in prepared.history[1:]] == ["user", "assistant"]


def test_function_call_output_is_model_visible_user_context():
    request = ResponsesRequest(
        model="deepseek-ai/DeepSeek-V3.2",
        input=[
            {"type": "function_call_output", "call_id": "call_1", "output": "tool result"},
        ],
    )
    prepared = prepare_responses_request(request, ResponseStore())
    assert "call_1" in prepared.current_input
    assert "tool result" in prepared.current_input


def test_function_call_history_uses_non_replay_record():
    request = ResponsesRequest(
        model="deepseek-ai/DeepSeek-V3.2",
        input=[
            {"type": "function_call", "name": "exec_command", "call_id": "call_1", "arguments": '{"cmd":"pwd"}'},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            {"type": "message", "role": "user", "content": "continue"},
        ],
    )
    prepared = prepare_responses_request(request, ResponseStore())
    assert "Previous tool call record" not in "\n".join(message.content for message in prepared.history)
    assert "tool_name: exec_command" in prepared.history[-1].content
    assert 'tool_arguments_json: {"cmd":"pwd"}' in prepared.history[-1].content
    assert "Tool call requested:" not in prepared.history[-1].content


def test_previous_response_id_prepends_store_history():
    store = ResponseStore()
    store.put(
        {"id": "resp_prev", "object": "response"},
        conversation=[{"role": "user", "content": "old"}, {"role": "assistant", "content": "answer"}],
    )
    request = ResponsesRequest(
        model="deepseek-ai/DeepSeek-V3.2",
        previous_response_id="resp_prev",
        input="new",
    )
    prepared = prepare_responses_request(request, store)
    assert [m.content for m in prepared.history] == ["old", "answer"]
    assert prepared.current_input == "new"


def test_unknown_previous_response_id_errors():
    request = ResponsesRequest(
        model="deepseek-ai/DeepSeek-V3.2",
        previous_response_id="resp_missing",
        input="new",
    )
    with pytest.raises(UnsupportedInputError) as exc:
        prepare_responses_request(request, ResponseStore())
    assert exc.value.code == "previous_response_not_found"


def test_input_image_is_unsupported():
    request = ResponsesRequest(
        model="deepseek-ai/DeepSeek-V3.2",
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_image", "image_url": "data:image/png;base64,abc"}],
            }
        ],
    )
    with pytest.raises(UnsupportedInputError) as exc:
        prepare_responses_request(request, ResponseStore())
    assert exc.value.code == "unsupported_input_image"


def test_reasoning_and_tool_call_output_mapping():
    request = ResponsesRequest(
        model="deepseek-ai/DeepSeek-V3.2",
        input="run tool",
        tools=[
            {
                "type": "function",
                "name": "exec_command",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
            }
        ],
    )
    output, errors = build_output_items(
        '<tool_call>{"name":"exec_command","arguments":{"cmd":"pwd"}}</tool_call>',
        "thinking",
        request,
    )
    assert not errors
    assert output[0]["type"] == "function_call"
