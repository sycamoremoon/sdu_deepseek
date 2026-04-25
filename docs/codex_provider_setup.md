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
stream_idle_timeout_ms = 300000

model_provider = "sdu_deepseek"
model = "deepseek-ai/DeepSeek-V3.2"
```

Set a dummy local API key:

```bash
export SDU_DEEPSEEK_API_KEY=dummy
```

One-shot CLI example without editing config:

```bash
env SDU_DEEPSEEK_API_KEY=dummy codex exec \
  -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:8000/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=300000}' \
  -c model_provider="sdu_deepseek" \
  -m deepseek-ai/DeepSeek-V3.2 \
  "请只回答：SDU Codex OK"
```

## Recommended Model

Start with:

```text
deepseek-ai/DeepSeek-V3.2
```

Reasoning models such as `deepseek-ai/DeepSeek-R1` and `deepseek-ai/DeepSeek-V3.2-think` are exposed, but reasoning is parsed from SDU text markers and mapped best-effort to Responses compatibility fields. It is not native OpenAI encrypted reasoning.

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

See `docs/sdu_web_capabilities.md` for the SDU-side probe evidence.

## Security Notes

- Do not put SDU credentials in Codex config.
- Do not use the dummy API key as an SDU credential.
- Do not commit `cookies.json`, `credentials.json`, captured raw tokens, or session data.
- The local service does not execute Codex tools; Codex executes local tools and sends tool outputs back as `function_call_output`.
