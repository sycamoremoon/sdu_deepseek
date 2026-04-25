#!/usr/bin/env python3
"""Smoke-test this proxy with the official OpenAI Python SDK.

Run the local server first:

    /home/damon/.local/venvs/tools/bin/python main.py

Then run:

    /home/damon/.local/venvs/tools/bin/python examples/test_openai_responses_client.py
"""

from __future__ import annotations

from openai import OpenAI, BadRequestError


MODEL = "deepseek-ai/DeepSeek-V3.2"


def main() -> None:
    client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="dummy")

    response = client.responses.create(model=MODEL, input="你好，请简短回答。")
    print("text:", response.output_text)

    print("stream:", end=" ", flush=True)
    with client.responses.stream(model=MODEL, input="请输出两个字：测试") as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                print(event.delta, end="", flush=True)
    print()

    tool_response = client.responses.create(
        model=MODEL,
        input="如果需要，请调用 echo 工具。",
        tools=[
            {
                "type": "function",
                "name": "echo",
                "description": "Echo input text.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ],
    )
    print("tool-capable output types:", [item.type for item in tool_response.output])

    second = client.responses.create(
        model=MODEL,
        previous_response_id=response.id,
        input="继续上一轮，用一句话回答。",
    )
    print("previous_response_id:", second.previous_response_id)

    try:
        client.responses.create(
            model=MODEL,
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                        {"type": "input_text", "text": "这是什么图？"},
                    ],
                }
            ],
        )
    except BadRequestError as exc:
        print("image unsupported:", exc.response.json()["error"]["code"])


if __name__ == "__main__":
    main()
