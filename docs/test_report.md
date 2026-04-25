# Test Report

Date: 2026-04-25

## Environment

| Item | Value |
| --- | --- |
| Working directory | `/Volumes/Hacking/SDU_DeepSeek` |
| Python venv | temporary `.codex-venv` |
| Python | `3.14` |
| Codex CLI | `codex-cli 0.125.0` locally; user reproduction used `0.123.0` |

Dependencies installed in a temporary local venv, then removed after testing:

```bash
python3 -m venv .codex-venv
.codex-venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

`requirements-dev.txt` was added for repeatable test dependency installs.

## Automated Tests

Command:

```bash
SDU_DEEPSEEK_SKIP_LOGIN=1 .codex-venv/bin/pytest -q
```

Result:

```text
73 passed, 3 skipped in 0.82s
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
| Fenced JSON tool call parsing | Passed |
| Bare `<apply_patch>` V4 regression parsing | Passed |
| Codex-style named XML tool parsing | Passed |
| Codex-style named XML tool streaming | Passed |
| `update_plan` empty-item repair | Passed |
| Legacy `Tool call requested:` text recovery | Passed |
| Non-replay tool history wording | Passed |
| Nested XML argument tool parsing | Passed |
| Self-closing XML attribute tool parsing | Passed |
| Previous tool-record replay recovery | Passed |
| Invalid tool markup stripping | Passed |
| Invalid JSON tool call handling | Passed |
| Unknown tool name handling | Passed |
| Schema mismatch handling | Passed |
| Custom/freeform tool parsing | Passed |
| Think-model apply_patch custom-tool streaming buffer | Passed |
| Think-model apply_patch fallback to declared `exec_command` | Passed |
| V4 bare apply_patch fallback to declared `exec_command` | Passed |
| V4 `<tool_plan>` compatibility mapped to declared `update_plan` | Passed |
| V4 long multi-turn tool loop does not stop on intermediate tool calls | Passed |
| Empty/filtered model output returns failed instead of empty completed | Passed |
| Tool-loop raw code block work returns failed instead of final completed | Passed |
| Unclosed custom-tool wrapper recovery | Passed |
| Top-level function argument recovery | Passed |
| Responses non-stream text route for V3.2, V3.2-think, V4 | Passed |
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
python3 -m py_compile main.py responses_models.py responses_adapter.py responses_store.py tool_bridge.py streaming.py scripts/capture_codex_requests.py scripts/capture_codex_session.py scripts/probe_sdu_capabilities.py examples/test_openai_responses_client.py
```

Result: passed.

## Live SDU Smoke

Command:

```bash
RUN_LIVE_SDU_TESTS=1 SDU_DEEPSEEK_SKIP_LOGIN=1 .codex-venv/bin/pytest -q tests/test_live_sdu.py
```

Result:

```text
Not rerun in this pass. The focused end-to-end checks below exercised the live SDU backend through local `/v1/responses` using the existing `cookies.json`.
```

The live tests load `cookies.json` into memory and send low-frequency text requests to the existing `compose_chat` endpoint. No cookies or credentials are printed.

## Codex End-To-End

Server command:

```bash
.codex-venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 18083
```

Current focused run used local Codex `0.125.0`, close to the user's `0.123.0` reproduction. The server loaded the existing `cookies.json`; no cookie values or credentials were printed.

Think-model apply_patch run:

```bash
env SDU_DEEPSEEK_API_KEY=dummy CODEX_HOME=/tmp/codex-sdu-e2e-home codex exec \
  --ephemeral --skip-git-repo-check --enable apply_patch_freeform --sandbox workspace-write \
  -C /tmp/sdu-codex-think-... \
  -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:18083/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=300000}' \
  -c model_provider="sdu_deepseek" \
  -m deepseek-ai/DeepSeek-V3.2-think \
  '请创建 file_organizer.py，只写一行 print("organizer ok")...'
```

Result: Codex executed `apply_patch`, created `file_organizer.py`, then executed `cat file_organizer.py`. Final file content:

```python
print("organizer ok")
```

V3.2 and V4 function-tool smoke:

```bash
for model in deepseek-ai/DeepSeek-V3.2 deepseek-ai/DeepSeek-V4; do
  env SDU_DEEPSEEK_API_KEY=dummy CODEX_HOME=/tmp/codex-sdu-e2e-home codex exec \
    --ephemeral --skip-git-repo-check --sandbox read-only \
    -C /tmp/sdu-codex-${model##*/}-... \
    -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:18083/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=300000}' \
    -c model_provider="sdu_deepseek" \
    -m "$model" \
    '请必须调用 exec_command 工具运行 pwd...'
done
```

Result:

```text
deepseek-ai/DeepSeek-V3.2: Codex executed exec_command pwd and returned the temp working directory.
deepseek-ai/DeepSeek-V4: Codex executed exec_command pwd and returned the temp working directory.
```

No raw `<tool_call>`, `<apply_patch>`, or `<think>` markup appeared as the final assistant answer in these runs.

V4 regression probe for the reported long prompt:

```bash
env SDU_DEEPSEEK_API_KEY=dummy CODEX_HOME=/tmp/codex-sdu-v4-regression-home codex exec \
  --ignore-user-config --ephemeral --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
  -C /tmp/sdu-codex-v4-stop-... \
  -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:18086/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=600000, stream_max_retries=5}' \
  -c model_provider="sdu_deepseek" \
  -m deepseek-ai/DeepSeek-V4 \
  '帮我写一个python程序，关于文件整理的, 包括完整的测试用例和使用说明。实现细节随意不要向我提问'
```

Result: the sanitized capture in `docs/codex_v4_stop_probe.md` shows Codex continued across 17 streamed `/v1/responses` requests. It sent `function_call_output` items after local tool execution and kept the full tool list on each request. The service returned 11 function-call responses (`exec_command` and `update_plan`) and no empty `completed` responses.

The run reproduced two V4 model-output quirks:

1. V4 emitted `<tool_plan>{...}</tool_plan>`; this is now parsed into the declared `update_plan` function call.
2. V4 later emitted a raw Python code block plus a stray `</think>` while it was in an active tool loop. Because there was no safe filename/tool payload to infer, the service now returns `response.failed` for that pattern instead of a final `message`, preventing silent early completion. This is covered by `test_tool_loop_code_block_work_is_not_silent_completed`.

Older baseline runs from the previous report also validated simple text, V3.2 `exec_command`, and V3.2 file creation/read loops; those commands used a Linux venv path and are superseded by the focused run above for this branch.

Observed Codex warning remains for these custom model ids:

```text
Unknown model deepseek-ai/DeepSeek-V3.2-think is used. This will use fallback model metadata.
```

The warning is emitted by Codex's internal model metadata table before or independently of `/v1/models`; it did not prevent the local Responses tool loop from working.

## Known Limitations And Risks

| Risk | Detail |
| --- | --- |
| Tool reliability | Tool calls depend on the SDU model obeying the injected `<tool_call>` structure. Invalid calls are safely downgraded to text with compatibility warnings. |
| Multimodal | The verified SDU endpoint is text-only; image/file inputs return explicit unsupported errors. |
| Store durability | `previous_response_id` state is in-memory and lost on restart. |
| Token usage | Usage fields are character-based estimates, not tokenizer-accurate counts. |
| SDU web changes | If `compose_chat` request fields or stream format change, the proxy may need updates. |
| Custom tools | `custom_tool_call` is supported for grammar/freeform tools like `apply_patch`; if Codex does not declare `apply_patch` but does declare a shell-like function tool, patch markup can be converted to an `exec_command`/`shell` call that runs `apply_patch` locally in Codex. |
| Reasoning | Reasoning is parsed from SDU text markers and mapped best-effort; no native encrypted reasoning support is claimed. |
| Streaming tool calls | When tools are present, streaming buffers the model text until the call can be classified, avoiding raw `<tool_call>` / `<think>` leakage as `response.output_text.delta`. |
