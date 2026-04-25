# Test Report

Date: 2026-04-25

## Environment

| Item | Value |
| --- | --- |
| Working directory | `/home/damon/SDU_DeepSeek` |
| Python venv | `/home/damon/.local/venvs/tools` |
| Python | `3.14.4` |
| Codex CLI | `codex-cli 0.125.0-alpha.3` |

Dependencies installed:

```bash
/home/damon/.local/venvs/tools/bin/pip install -r requirements.txt pytest httpx openai
```

`requirements-dev.txt` was added for repeatable test dependency installs.

## Automated Tests

Command:

```bash
SDU_DEEPSEEK_SKIP_LOGIN=1 /home/damon/.local/venvs/tools/bin/pytest -q
```

Result:

```text
46 passed, 3 skipped in 0.69s
```

Coverage areas:

| Area | Result |
| --- | --- |
| Responses request parsing | Passed |
| String input conversion | Passed |
| Message array conversion | Passed |
| Developer/system prefix handling | Passed |
| `function_call_output` conversion | Passed |
| `previous_response_id` in-memory store | Passed |
| Reasoning content response mapping | Passed |
| Plain `<think>...</think>` parsing | Passed |
| Split `<think>` chunk parsing | Passed |
| Tool prompt injection | Passed |
| Custom-tool XML prompt guidance | Passed |
| Valid tool call parsing | Passed |
| Codex-style named XML tool parsing | Passed |
| Codex-style named XML tool streaming | Passed |
| `update_plan` empty-item repair | Passed |
| Legacy `Tool call requested:` text recovery | Passed |
| Non-replay tool history wording | Passed |
| Nested XML argument tool parsing | Passed |
| Self-closing XML attribute tool parsing | Passed |
| Previous tool-record replay recovery | Passed |
| Invalid JSON tool call handling | Passed |
| Unknown tool name handling | Passed |
| Schema mismatch handling | Passed |
| Custom/freeform tool parsing | Passed |
| Unclosed custom-tool wrapper recovery | Passed |
| Top-level function argument recovery | Passed |
| Responses non-stream text route | Passed |
| Responses SSE text stream | Passed |
| Responses SSE function-call stream | Passed |
| `/responses` alias | Passed |
| GET/DELETE/input_items routes | Passed |
| `input_image` unsupported error | Passed |
| Chat Completions non-stream regression | Passed |
| Chat Completions stream regression | Passed |
| OpenAI Python SDK `responses.create` via TestClient | Passed |
| OpenAI Python SDK streaming via TestClient | Passed |
| OpenAI Python SDK tool/previous/image-error scenarios | Passed |

Syntax check:

```bash
/home/damon/.local/venvs/tools/bin/python -m py_compile main.py responses_models.py responses_adapter.py responses_store.py tool_bridge.py streaming.py scripts/capture_codex_requests.py scripts/probe_sdu_capabilities.py examples/test_openai_responses_client.py
```

Result: passed.

## Live SDU Smoke

Command:

```bash
RUN_LIVE_SDU_TESTS=1 SDU_DEEPSEEK_SKIP_LOGIN=1 /home/damon/.local/venvs/tools/bin/pytest -q tests/test_live_sdu.py
```

Result:

```text
3 passed in 25.23s
```

The live tests load `cookies.json` into memory and send low-frequency text requests to the existing `compose_chat` endpoint. No cookies or credentials are printed.

## Codex End-To-End

Server command:

```bash
/home/damon/.local/venvs/tools/bin/uvicorn main:app --host 127.0.0.1 --port 18081
```

Simple Responses provider run:

```bash
env SDU_DEEPSEEK_API_KEY=dummy CODEX_HOME=/tmp/codex-sdu-real-home codex exec \
  --ignore-user-config --ephemeral --skip-git-repo-check --sandbox read-only \
  -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:18081/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=300000}' \
  -c model_provider="sdu_deepseek" \
  -m deepseek-ai/DeepSeek-V3.2 \
  "请只回答：SDU Codex OK"
```

Result: Codex printed `SDU Codex OK`.

Tool bridge run:

```bash
env SDU_DEEPSEEK_API_KEY=dummy CODEX_HOME=/tmp/codex-sdu-real-home codex exec \
  --ignore-user-config --ephemeral --skip-git-repo-check --sandbox read-only \
  -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:18081/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=300000}' \
  -c model_provider="sdu_deepseek" \
  -m deepseek-ai/DeepSeek-V3.2 \
  '请必须调用 exec_command 工具运行 pwd。工具调用格式必须严格输出：<tool_call>{"name":"exec_command","arguments":{"cmd":"pwd"}}</tool_call>。拿到工具结果后，用一句话回答当前目录。'
```

Result:

```text
exec: /usr/sbin/bash -lc pwd
Output: /home/damon/SDU_DeepSeek
Codex final answer: 当前目录是 `/home/damon/SDU_DeepSeek`。
```

This verifies the full loop: Codex -> local `/v1/responses` -> SDU text backend -> local function_call item -> Codex tool execution -> `function_call_output` -> SDU final answer.

File creation/read regression:

```bash
/home/damon/.local/venvs/tools/bin/uvicorn main:app --host 127.0.0.1 --port 18082
env SDU_DEEPSEEK_API_KEY=dummy CODEX_HOME=/tmp/codex-sdu-real-home codex exec \
  --ignore-user-config --ephemeral --skip-git-repo-check --sandbox workspace-write \
  -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:18082/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=300000}' \
  -c model_provider="sdu_deepseek" \
  -m deepseek-ai/DeepSeek-V3.2 \
  "请在当前目录创建 hello.py，内容打印 Hello from SDU Codex，然后读取它并告诉我结果。"
```

Result: Codex created `hello.py`, ran `cat hello.py`, and produced a final answer showing:

```python
#!/usr/bin/env python3
print('Hello from SDU Codex')
```

This specifically validates the regression that previously exposed raw `<update_plan>` / XML-like tool markup to the CLI. The current bridge parses those variants into Responses tool items instead of leaking them as assistant text.

## Known Limitations And Risks

| Risk | Detail |
| --- | --- |
| Tool reliability | Tool calls depend on the SDU model obeying the injected `<tool_call>` structure. Invalid calls are safely downgraded to text with compatibility warnings. |
| Multimodal | The verified SDU endpoint is text-only; image/file inputs return explicit unsupported errors. |
| Store durability | `previous_response_id` state is in-memory and lost on restart. |
| Token usage | Usage fields are character-based estimates, not tokenizer-accurate counts. |
| SDU web changes | If `compose_chat` request fields or stream format change, the proxy may need updates. |
| Custom tools | `custom_tool_call` is best-effort for grammar/freeform tools like `apply_patch`; Codex default path primarily uses function tools. |
| Reasoning | Reasoning is parsed from SDU text markers and mapped best-effort; no native encrypted reasoning support is claimed. |
