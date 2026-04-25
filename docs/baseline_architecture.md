# Baseline Architecture

Probe date: 2026-04-25

## Current Local API

`main.py` exposes three OpenAI-compatible Chat Completions endpoints:

| Method | Path | Current behavior |
| --- | --- | --- |
| `GET` | `/v1/models` | Returns static `ModelList` data from `MODELS_DATA`. |
| `GET` | `/v1/models/{model_id}` | Returns a matching static `ModelCard` or OpenAI-style 404 error. |
| `POST` | `/v1/chat/completions` | Accepts FastChat `ChatCompletionRequest`, converts the last user message to SDU text input, and returns Chat Completions JSON or `data:` streaming chunks. |

No Responses API routes exist yet.

## ChatCompletionRequest Handling

`main.py` imports request/response models from `fastchat.protocol.openai_api_protocol`.

Flow:

1. `request.messages`, `request.stream`, `request.model`, and optional `thinking_budget` are read.
2. The target model is converted through `MODEL_MAP` by `get_config_for_model()`.
3. The request must contain at least one message, and the last message must be `role="user"`.
4. `parse_content()` accepts string content and list content with `{"type": "text", "text": ...}` parts.
5. Messages before the last user message become SDU history.
6. Token usage is estimated by character length.
7. `sduwrap.chat(current_input, request_history, config)` is called for both streaming and non-streaming paths.

## SDU Web Request Construction

`sduwrap.py` calls one known AI Assist endpoint:

```text
https://aiassist.sdu.edu.cn/site/ai/compose_chat
```

`make_chat_request()` creates `application/x-www-form-urlencoded` form data:

| Field | Source |
| --- | --- |
| `content` | Current user text. |
| `history[n][role]` / `history[n][content]` | Converted `ChatSession` history. |
| `compose_id` | `ChatConfig.compose_id`, model dependent. |
| `auth_tag` | Defaults to `本科生`. |
| `deep_search` | Defaults to `2`. |
| `internet_search` | Defaults to `2`. |
| `model_name` | Defaults to `DeepSeek-V3.2-think`. |
| `thinking_budget` | Defaults to `1000`. |
| `chat_only_id` | Random UUID hex. |

History conversion is lossy:

| Input role | SDU form strategy |
| --- | --- |
| `system` | Converted to a user history message, followed by assistant `我知道了`. |
| `user` | Converted to user history plus an empty assistant slot. |
| `assistant` | Fills the previous assistant slot when possible, otherwise appends an assistant history item. |
| `developer` | Not currently handled by chat completions; Responses adapter should merge it into instructions/system text. |

## Streaming Parser

`sduwrap.chat()` posts form data with `stream=True` and iterates over response lines.

Observed/parser contract:

| Layer | Format |
| --- | --- |
| HTTP response | `text/event-stream; charset=utf-8`, chunked. |
| Line prefix | `data: `. |
| JSON shape | Top-level keys include `d`, `e`, `m`; text is read from `d.answer`. |
| Reasoning split | `ChatStream` parses escaped `<think\>` and `</think\>` markers into `reasoning_content`; other text becomes `content`. |

`main.py` maps `content` to Chat Completions `delta.content` or final `message.content`, and maps `reasoning_content` to non-standard `delta.reasoning_content` or `message.reasoning_content`.

## Models And Parameters

Local model IDs are mapped to SDU model names:

| OpenAI-facing model | SDU `model_name` | `compose_id` |
| --- | --- | --- |
| `deepseek-ai/DeepSeek-V3.2` | `DeepSeek-V3.2` | `73` |
| `deepseek-ai/DeepSeek-R1` | `DeepSeek-R1` | `73` |
| `deepseek-ai/DeepSeek-V3` | `DeepSeek-V3` | `73` |
| `deepseek-ai/DeepSeek-V3.2-think` | `DeepSeek-V3.2-think` | `73` |
| `Qwen/Qwen3-235B-A22B-Instruct` | `Qwen3-235B-A22B-Instruct` | `72` |
| `Qwen/Qwen3-235B-A22B-Thinking` | `Qwen3-235B-A22B-Thinking` | `72` |

`thinking_budget`, `deep_search`, and `internet_search` are passed as SDU form fields. `temperature`, `top_p`, `max_tokens`, tool fields, and multimodal fields are not currently mapped.

## Baseline Run Status

The repository Python environment currently lacks runtime dependencies:

```text
ModuleNotFoundError: No module named 'fastapi'
ModuleNotFoundError: No module named 'requests'
```

Because of that, the existing FastAPI service was not started in this probe pass. The live SDU endpoint was probed with a dependency-free `urllib` script instead. Before implementing Responses routes and regression tests, install the existing `requirements.txt` dependencies in an isolated environment.
