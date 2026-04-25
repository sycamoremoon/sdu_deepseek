# Codex Wire Probe

Probe date: 2026-04-25

## Environment

| Item | Result |
| --- | --- |
| Codex CLI | `codex-cli 0.125.0-alpha.3` |
| Binary source | VS Code/Codex IDE extension: `/home/damon/.vscode-server/extensions/openai.chatgpt-26.422.30944/bin/linux-x86_64/codex` |
| Capture server | `scripts/capture_codex_requests.py`, listening on `127.0.0.1:18080` |
| Provider config | `base_url = "http://127.0.0.1:18080/v1"`, `wire_api = "responses"` |
| Raw sanitized log | `docs/codex_wire_probe_logs/capture.jsonl` |

The capture server redacts `Authorization`, cookies, tokens, secrets, and long string bodies. It now prefers FastAPI when dependencies are installed and falls back to the standard library in this bare environment.

## Commands Used

Core configuration used for each probe:

```bash
env SDU_DEEPSEEK_API_KEY=dummy CODEX_HOME=/tmp/codex-capture-home \
  codex exec --ignore-user-config --ephemeral --skip-git-repo-check --sandbox read-only \
  -c 'model_providers.sdu_capture={name="SDU Capture", base_url="http://127.0.0.1:18080/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses"}' \
  -c model_provider="sdu_capture" \
  -m deepseek-ai/DeepSeek-V3.2 \
  "..."
```

Additional probes:

| Scenario | Method |
| --- | --- |
| Simple text | Normal `codex exec` prompt. |
| Streaming | Same request; Codex used `Accept: text/event-stream` and `stream=true`. |
| Tool call loop | Capture server returned a safe `exec_command` function call with `{"cmd": "pwd"}`. |
| Image input | `codex exec --image /tmp/codex_probe_valid.png ...`. |
| `apply_patch` schema | `--enable apply_patch_freeform` and a known-model reasoning probe. |
| Multi-turn resume | First `codex exec` without `--ephemeral`, then `codex exec resume --last`. |
| Reasoning request field | `-c model_reasoning_effort="medium" -c model_reasoning_summary="auto"`, compared unknown DeepSeek model vs known `gpt-5.2`. |

## Observed HTTP Calls

In the CLI scenarios above, Codex only called:

```text
POST /v1/responses
```

No `GET /v1/models`, `GET /v1/responses/{id}`, `DELETE /v1/responses/{id}`, or `/input_items` calls were observed from `codex exec`. The compatibility layer should still implement them because Codex IDE or future CLI flows may call them, and they are cheap to support.

## Request Shape

Typical first request:

```json
{
  "model": "deepseek-ai/DeepSeek-V3.2",
  "instructions": "<large Codex base instructions>",
  "input": [
    {
      "type": "message",
      "role": "developer",
      "content": [
        {"type": "input_text", "text": "<permissions instructions>"},
        {"type": "input_text", "text": "<skills instructions>"}
      ]
    },
    {
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "<environment_context>"}]
    },
    {
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "user prompt"}]
    }
  ],
  "stream": true,
  "store": false,
  "tool_choice": "auto",
  "parallel_tool_calls": false,
  "reasoning": null,
  "include": [],
  "tools": ["..."]
}
```

Important fields to accept:

| Field | Observed value |
| --- | --- |
| `stream` | Always `true` in these Codex CLI probes. |
| `store` | `false`. |
| `parallel_tool_calls` | `false`. |
| `tool_choice` | `"auto"`. |
| `include` | `[]`, or `["reasoning.encrypted_content"]` for known reasoning models. |
| `client_metadata` | Includes a Codex installation id; treat as metadata and do not log raw values in normal service logs. |
| `prompt_cache_key` | Present and sensitive-like; redacted in probe logs. |

## Reasoning Field

For the unknown custom model `deepseek-ai/DeepSeek-V3.2`, Codex displayed reasoning settings in the CLI but sent:

```json
"reasoning": null
```

For known model `gpt-5.2` with:

```bash
-c model_reasoning_effort="medium" -c model_reasoning_summary="auto"
```

Codex sent:

```json
"reasoning": {"effort": "medium", "summary": "auto"},
"include": ["reasoning.encrypted_content"]
```

Implementation implication: the Responses parser should accept `reasoning` as `null` or an object. Response generation should expose SDU `reasoning_content` in safe summary/text compatibility fields, but should not claim native encrypted reasoning support.

## Tool Schema

Default tool list for the custom DeepSeek model:

```text
exec_command
write_stdin
update_plan
request_user_input
web_search
view_image
spawn_agent
send_input
resume_agent
wait_agent
close_agent
```

Most tools are standard Responses function tools:

```json
{
  "type": "function",
  "name": "exec_command",
  "description": "Runs a command in a PTY...",
  "parameters": {
    "type": "object",
    "required": ["cmd"],
    "properties": {
      "cmd": {"type": "string"},
      "workdir": {"type": "string"},
      "yield_time_ms": {"type": "number"},
      "sandbox_permissions": {"type": "string"}
    },
    "additionalProperties": false
  },
  "strict": false
}
```

`web_search` appears as a built-in style tool:

```json
{"type": "web_search", "external_web_access": false}
```

With `apply_patch_freeform` enabled, and also in the known `gpt-5.2` reasoning probe, Codex included:

```json
{
  "type": "custom",
  "name": "apply_patch",
  "description": "Use the `apply_patch` tool to edit files. This is a FREEFORM tool...",
  "format": {
    "type": "grammar",
    "syntax": "lark",
    "definition": "start: begin_patch hunk+ end_patch ..."
  }
}
```

Implementation implication: the compatibility layer must not assume every tool has `type="function"` or JSON parameters. Unknown and custom tool types must not crash parsing. P0 can ignore non-function tools safely; P1 should include their names/descriptions in the tool prompt bridge and return a compatible tool item only when the item type is known.

## Tool Call Loop

When the capture server streamed a function call item:

```json
{
  "type": "function_call",
  "call_id": "call_...",
  "name": "exec_command",
  "arguments": "{\"cmd\":\"pwd\"}"
}
```

Codex executed the local tool and sent the next `/v1/responses` request with both the model call and the result appended to `input`:

```json
[
  {"type": "function_call", "call_id": "call_...", "name": "exec_command", "arguments": "{\"cmd\":\"pwd\"}"},
  {"type": "function_call_output", "call_id": "call_...", "output": "Chunk ID: ...\\nOutput:\\n/home/damon/SDU_DeepSeek\\n"}
]
```

Implementation implication: the local SDU proxy must never execute Codex tools. It only needs to return `function_call` items and later convert `function_call_output` into model-visible text for SDU.

## Image Input

With a valid PNG passed through `--image`, Codex sent image content inside the user message:

```json
{
  "type": "message",
  "role": "user",
  "content": [
    {"type": "input_text", "text": "<image name=[Image #1]>"},
    {
      "type": "input_image",
      "image_url": "data:image/png;base64,...",
      "detail": "high"
    },
    {"type": "input_text", "text": "</image>"},
    {"type": "input_text", "text": "请描述这张 1x1 测试图片。"}
  ]
}
```

Implementation implication: Responses request parsing must detect `input_image` parts. Because SDU native image support was not found in the scoped probe, the service should return a clear `unsupported_input_image` error or a documented text-only fallback.

## Multi-Turn State

`codex exec resume --last` did not use `previous_response_id`. It sent:

```json
"store": false,
"previous_response_id": null,
"input": [
  "...developer context...",
  "...environment context...",
  {"role": "user", "content": [{"type": "input_text", "text": "first turn"}]},
  {"role": "assistant", "content": [{"type": "output_text", "text": "first answer"}]},
  {"role": "user", "content": [{"type": "input_text", "text": "second turn"}]}
]
```

Implementation implication: explicit `input` history is required for Codex today. `previous_response_id` and `store` should still be implemented for SDK compatibility and future Codex behavior.

## Streaming Events Accepted By Codex

The capture server's minimal stream worked with Codex using these events:

```text
response.created
response.in_progress
response.output_item.added
response.content_part.added
response.output_text.delta
response.output_text.done
response.content_part.done
response.output_item.done
response.completed
```

Function call streaming worked with:

```text
response.output_item.added
response.function_call_arguments.delta
response.function_call_arguments.done
response.output_item.done
response.completed
```

Implementation implication: `/v1/responses stream=true` must emit semantic Responses SSE events, not Chat Completions `data:` chunks.

## Selected Compatibility Plan

Based on the probe:

1. Implement `/v1/responses` as the primary Codex path and keep `/responses` as an alias.
2. Convert Responses `input` messages to the existing SDU text/history form contract.
3. Preserve old `/v1/chat/completions`, `/v1/models`, and `/v1/models/{model_id}` behavior with regression tests.
4. Implement Response streaming with semantic event names listed above.
5. Implement an in-memory response store for SDK compatibility, even though current Codex CLI sends explicit history and `store=false`.
6. Implement function-tool protocol compatibility by prompting SDU to emit strict `<tool_call>{...}</tool_call>` blocks, while also accepting Codex-style `<tool_name>{...}</tool_name>` blocks such as `<update_plan>{...}</update_plan>`, then converting valid blocks to `function_call` items.
7. Treat `apply_patch` custom grammar tools and unknown built-ins as safe degradation initially; do not crash, and document partial support.
8. Return explicit unsupported errors for `input_image` and `input_file` unless a same-origin SDU upload/image endpoint is later verified.

## External References

OpenAI's Responses API is the target protocol for stateful, multimodal, tool-using interactions. The official API reference documents `POST /v1/responses` request fields such as `input`, `instructions`, `previous_response_id`, `store`, `stream`, `tools`, and `reasoning`: https://platform.openai.com/docs/api-reference/responses/create

Codex custom-provider configuration was validated primarily by local `codex --help`, `codex exec --help`, and successful live capture. OpenAI's Codex config reference documents `model_providers` configuration: https://developers.openai.com/codex/config-reference
