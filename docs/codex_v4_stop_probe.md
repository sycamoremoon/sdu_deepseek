# Codex V4 Stop Probe

Date: 2026-04-25

## Environment

| Item | Value |
| --- | --- |
| Branch | `webapi_back` |
| Codex CLI | `codex-cli 0.125.0` |
| Local API server | `http://127.0.0.1:18085/v1` |
| Capture proxy | `http://127.0.0.1:18086/v1` |
| Raw sanitized capture | `docs/codex_v4_stop_probe.jsonl` |

The capture proxy records only state-machine metadata: model id, stream flag, input item types, whether tool outputs are present, tool names, output item types, SSE event names, status, and error code. It does not record Authorization, cookies, passwords, or full prompts/tool outputs.

## Probe Command

```bash
env SDU_DEEPSEEK_API_KEY=dummy CODEX_HOME=/tmp/codex-sdu-v4-stop-home-... \
  codex exec --ignore-user-config --ephemeral --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  -C /tmp/sdu-codex-v4-stop-... \
  -c 'model_providers.sdu_deepseek={name="SDU DeepSeek Local", base_url="http://127.0.0.1:18086/v1", env_key="SDU_DEEPSEEK_API_KEY", wire_api="responses", stream_idle_timeout_ms=600000, stream_max_retries=5}' \
  -c model_provider="sdu_deepseek" \
  -m deepseek-ai/DeepSeek-V4 \
  '帮我写一个python程序，关于文件整理的, 包括完整的测试用例和使用说明。实现细节随意不要向我提问'
```

## Findings

| Question | Observed |
| --- | --- |
| Did Codex request `/v1/responses` after earlier tool calls? | Yes. The capture contains 17 streamed `/v1/responses` requests in one run. |
| Did Codex include tool results? | Yes. After the first tool call, later requests include `function_call` and `function_call_output` input items. |
| Was `previous_response_id` used? | No in this Codex run. Codex sent `store=false` and explicit accumulated input items instead. The in-memory `previous_response_id` path remains covered by tests. |
| Were tools still sent? | Yes. Each request carried 11 tools, including `exec_command` and `update_plan`. |
| Did the server return empty `completed` responses? | No after the fix. Filtered/empty outputs are returned as `response.failed` with `sdu_empty_output`. |
| Did tool calls use Responses SSE events? | Yes. Function calls streamed `response.output_item.added`, `response.function_call_arguments.delta`, `response.function_call_arguments.done`, `response.output_item.done`, then `response.completed`. |
| Was there SSE idle timeout evidence? | Codex retried several streamed turns after `response.failed`; retries continued the task rather than silently ending. |

## Captured Sequence Summary

```text
responses: 17
function_call responses: 11
  exec_command: 9
  update_plan: 2
response.failed / sdu_empty_output: 5
message responses: 1
```

Two root causes were visible:

1. V4 sometimes emits `<tool_plan>{...}</tool_plan>` instead of the declared `update_plan` function call. Before the parser fix, Codex treated this as final assistant text and stopped. The parser now maps this wrapper to `update_plan` when that tool is declared.
2. Later in a long tool loop, V4 emitted a raw Markdown Python code block plus a stray `</think>` while it was supposed to write a test file. That output has no safe filename or tool arguments, so the proxy must not guess and execute anything. The proxy now treats this pattern as a failed model turn during an active tool loop instead of a successful final message, which prevents silent early completion and lets Codex retry.

The remaining limitation is model compliance: if V4 repeatedly emits plain code blocks without a declared tool call, the proxy can reject the turn safely, but it cannot infer a file path and synthesize local edits without risking incorrect tool execution.
