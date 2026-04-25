def test_chat_completions_non_stream_regression(client, mock_sdu):
    mock_sdu([{"content": "chat answer", "reasoning_content": "chat reason"}])
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "chat answer"
    assert body["choices"][0]["message"]["reasoning_content"] == "chat reason"


def test_chat_completions_stream_regression(client, mock_sdu):
    mock_sdu([{"content": "A", "reasoning_content": ""}, {"content": "B", "reasoning_content": ""}])
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "deepseek-ai/DeepSeek-V3.2",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    ) as response:
        text = response.read().decode("utf-8")
    assert response.status_code == 200
    assert "data:" in text
    assert "[DONE]" in text
    assert "AB" not in text
    assert '"content":"A"' in text
    assert '"content":"B"' in text
