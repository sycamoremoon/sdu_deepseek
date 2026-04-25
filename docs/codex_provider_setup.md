# Codex Provider Setup

This proxy can be used as a Codex CLI / Codex IDE custom provider through the OpenAI Responses wire protocol.

## Start The Local Server

Use the requested venv:

```bash
cd /home/damon/SDU_DeepSeek
/home/damon/.local/venvs/tools/bin/pip install -r requirements.txt -r requirements-dev.txt
/home/damon/.local/venvs/tools/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

The server still authenticates to SDU through the existing `cookies.json` / `credentials.json` / login flow. The API key that Codex sends to this local server can be any dummy value; it is not an SDU password, cookie, or token.

## Codex Config

Add a provider similar to this in `~/.codex/config.toml`:

```toml
[model_providers.sdu_deepseek]
name = "SDU DeepSeek Local"
base_url = "http://127.0.0.1:8000/v1"
env_key = "SDU_DEEPSEEK_API_KEY"
wire_api = "responses"
stream_idle_timeout_ms = 600000
stream_max_retries = 5

model_provider = "sdu_deepseek"
model = "deepseek-ai/DeepSeek-V4"
```

Set a dummy local API key:

```bash
export SDU_DEEPSEEK_API_KEY=dummy
```

One-shot CLI example without editing config:

```bash
env SDU_DEEPSEEK_API_KEY=dummy codex exec \
  -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:8000/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=600000, stream_max_retries=5}' \
  -c model_provider="sdu_deepseek" \
  -m deepseek-ai/DeepSeek-V3.2 \
  "请只回答：SDU Codex OK"
```

For long Codex tasks, prefer the 10 minute idle timeout above. The server also emits SSE keepalive comments while it is waiting for the SDU upstream stream, but this timeout is still useful when the model spends a long time thinking before the next token. It is a guardrail, not a substitute for the Responses state machine: tool calls must still be returned as tool/function call items, not as text.

## Recommended Model

Start with:

```text
deepseek-ai/DeepSeek-V4
```

Reasoning models such as `deepseek-ai/DeepSeek-R1` and `deepseek-ai/DeepSeek-V3.2-think` are exposed, but reasoning is parsed from SDU text markers and mapped best-effort to Responses compatibility fields. It is not native OpenAI encrypted reasoning.

The `/v1/models` endpoint currently exposes at least:

```text
deepseek-ai/DeepSeek-V4
deepseek-ai/DeepSeek-V3.2
deepseek-ai/DeepSeek-V3.2-think
```

## Codex Model Metadata Warning

Codex may print a warning like:

```text
Model metadata for `deepseek-ai/DeepSeek-V4` not found. Defaulting to fallback metadata.
```

This comes from Codex's local model metadata catalog. In the probed Codex CLI builds, `codex exec` did not call `/v1/models` before issuing `/v1/responses`, so this server cannot fully remove that warning just by returning the model from `/v1/models`. It is not the cause of an early tool-loop stop, but fallback metadata can affect Codex defaults such as context size and reasoning/tool strategy.

If your Codex build supports a custom model catalog setting, use a local catalog similar to this and point Codex at it according to that build's configuration syntax:

```json
{
  "deepseek-ai/DeepSeek-V4": {
    "context_window": 131072,
    "max_output_tokens": 8192,
    "supports_reasoning_summaries": false
  },
  "deepseek-ai/DeepSeek-V3.2": {
    "context_window": 131072,
    "max_output_tokens": 8192,
    "supports_reasoning_summaries": false
  },
  "deepseek-ai/DeepSeek-V3.2-think": {
    "context_window": 131072,
    "max_output_tokens": 8192,
    "supports_reasoning_summaries": true
  }
}
```

Codex releases differ here; if the local catalog option is unavailable, leave the warning in place and rely on the explicit provider settings. Do not change the Responses wire format to silence this warning.

## Capability Levels

| Capability | Status |
| --- | --- |
| Plain text | Supported through SDU `compose_chat` form-data. |
| Responses streaming | Supported with semantic Responses SSE events. |
| Multi-turn explicit input history | Supported; this is what Codex 0.125 sends today. |
| `previous_response_id` | Supported in-memory for SDK/future compatibility. |
| Function tools | Protocol-compatible bridge through `<tool_call>{...}</tool_call>`, Codex-style `<tool_name>{...}</tool_name>`, simple nested XML arguments such as `<exec_command><cmd>...</cmd></exec_command>`, and self-closing XML attributes such as `<exec_command cmd="..." />`. |
| Custom/freeform tools such as `apply_patch` | Parsed as `custom_tool_call` when the model emits a valid custom tool call, but reliability depends on model compliance. |
| Image/file input | Not natively supported by the verified SDU endpoint; returns `unsupported_input_image` / `unsupported_input_file`. |
| Token usage | Character-based estimate. |
| Store durability | In-memory only; restart loses `resp_*` history. |
| Empty upstream output | Returned as `response.failed` / HTTP 502 instead of an empty `completed` response, so Codex does not silently stop mid task. |

See `docs/sdu_web_capabilities.md` for the SDU-side probe evidence.

## Security Notes

- Do not put SDU credentials in Codex config.
- Do not use the dummy API key as an SDU credential.
- Do not commit `cookies.json`, `credentials.json`, captured raw tokens, or session data.
- The local service does not execute Codex tools; Codex executes local tools and sends tool outputs back as `function_call_output`.
