# SDU Web Capabilities Probe

Probe date: 2026-04-25

## Scope And Safety

The live probe was restricted to the endpoint already used by this repository:

```text
https://aiassist.sdu.edu.cn/site/ai/compose_chat
```

No unrelated sites or unreferenced APIs were scanned. `cookies.json` was used only as an HTTP cookie source; cookies were never printed to stdout, logs, or docs. The live probe sent three low-frequency requests:

1. Form-data `ping`.
2. JSON `ping`.
3. Form-data request with minimal history.

The raw sanitized machine-readable probe output is in `docs/sdu_web_capabilities_probe.json`.

## Live Results Summary

| Probe | Result |
| --- | --- |
| Form-data chat | HTTP 200, `Content-Type: text/event-stream; charset=utf-8`, chunked `data:` lines. |
| JSON chat | HTTP 200 but business error body: `{"e":1,"d":[],"m":"compose_id不能为空"}`. This indicates the endpoint did not parse JSON body as the expected form contract. |
| History form | HTTP 200, stream returned short answer, confirming `history[n][role]` and `history[n][content]` form fields are accepted. |

## Capability Matrix

| Capability | Probe method | Request format | Return format | Support | Evidence summary | Compatibility strategy |
| --- | --- | --- | --- | --- | --- | --- |
| Known endpoint scope | Static source inspection | `sduwrap.url` points only to `/site/ai/compose_chat` | Streaming `data:` lines | Yes | Current repo only calls this SDU AI endpoint. | Keep live probing and proxy traffic scoped to this endpoint unless same-origin web evidence is added. |
| Form-data chat | Static + live | `application/x-www-form-urlencoded` fields: `content`, history, `compose_id`, `auth_tag`, `deep_search`, `internet_search`, `model_name`, `thinking_budget`, `chat_only_id` | `text/event-stream` | Yes | Live form probe returned HTTP 200 event stream. | Responses adapter should convert input messages into the existing form contract. |
| JSON body chat | Live | JSON object with equivalent fields | JSON business error | No / not via current endpoint | JSON probe returned `compose_id不能为空`, even though `compose_id` was present in JSON. | Do not send JSON to SDU `compose_chat`; keep using form data. |
| Plain text `content` | Static + live | `content=<text>` | `d.answer` text chunks | Yes | `sduwrap.make_chat_request()` and live form probe both use text content. | Convert Responses text parts to one prompt string. |
| Structured content parts | Static | No native structure in form data | Text stream only | No evidence | Current form has one `content` text field and text history fields. | Flatten text parts; reject or explain unsupported non-text parts. |
| History | Static + live | `history[n][role]`, `history[n][content]` | Text stream | Yes | Live history probe returned HTTP 200. `sduwrap.history_to_form_data()` defines the mapping. | Preserve assistant/user history; convert Responses output_text to assistant history. |
| System role | Static | No separate system field | Text stream only | Best effort only | Existing code maps `system` to user history plus assistant `我知道了`. | Merge system/developer instructions into a prefixed text block or converted history. |
| Developer role | Static | No developer role in current form mapping | Text stream only | No native evidence | Chat Completions path does not handle developer role. Codex sends developer messages. | Merge developer messages with `instructions` into model-visible prefix text. |
| `model_name` | Static + live | Form field | Text stream | Yes | Live probe with `DeepSeek-V3.2` succeeded. | Use existing `MODEL_MAP` and `ChatConfig.set_model()`. |
| `compose_id` | Static + live | Form field | Required by endpoint | Yes | JSON probe error says `compose_id` missing when not form-parsed; form probe with `compose_id=73` succeeded. | Keep model-to-compose mapping in one shared place. |
| `thinking_budget` | Static | Form field | Reasoning may appear in text markers | Best effort | Existing code sends `thinking_budget`; live non-thinking probe had no think marker. | Pass through when provided; map parsed reasoning safely. |
| `deep_search` | Static | Form field | Not separately typed in stream | Unknown effect | Existing code sends default `2`; no separate capability signal was observed. | Preserve existing default and allow future request/config override. |
| `internet_search` | Static | Form field | Not separately typed in stream | Unknown effect | Existing code sends default `2`; no separate capability signal was observed. | Preserve existing default and allow future request/config override. |
| Stream transport | Static + live | `POST` form with `stream=True` at HTTP client level | SSE-like `data:` lines | Yes | Live response was `text/event-stream`, chunked; parsed top-level keys included `d`, `e`, `m`. | Continue parsing SDU stream internally; expose OpenAI Responses semantic SSE outward. |
| Reasoning content | Static + live | `thinking_budget` + model selection | Escaped `<think\>...</think\>` parsed by local code | Best effort | `ChatStream` splits escaped think tags. Live V3.2 ping had no marker. | Put parsed `reasoning_content` into Responses-compatible reasoning summary/text compatibility fields; avoid claiming native encrypted reasoning. |
| Native function/tool calls | Static | No tool schema fields in form | Text stream only | No evidence | Current endpoint accepts no `tools`, `tool_choice`, or tool result field. | Implement protocol bridge: inject tool specs into prompt, parse strict `<tool_call>{...}</tool_call>` and Codex-style `<tool_name>{...}</tool_name>`, return Responses `function_call`. |
| Tool result回灌 | Static | No native field | Text stream only | No evidence | No SDU-side call/result protocol in current code. | Convert Codex `function_call_output` items into text context before the next SDU call. |
| Native web search/plugin control | Static | `deep_search` and `internet_search` numeric fields only | Text stream only | Unknown / not typed | No plugin/tool result schema was found. | Keep existing fields; do not expose as OpenAI tools unless verified. |
| File upload | Static scoped inspection | No multipart or upload endpoint in repo | N/A | No evidence | No file id, multipart, or upload URL appears in current code. | Return `unsupported_input_file` for Responses file input unless a same-origin upload endpoint is verified. |
| Image input | Static scoped inspection | No image field in `compose_chat` form | N/A | No evidence | Current request is text + history only. | Return `unsupported_input_image` for Codex `input_image`, or optional documented text-only placeholder fallback. |
| Voice input | Static scoped inspection | No audio field in repo | N/A | No evidence | No voice endpoint or audio payload in current code. | Do not claim support. |

## Chosen SDU-Side Mapping

The compatibility layer should treat SDU AI Assist as a text-only streaming backend unless future same-origin evidence proves otherwise.

Selected mapping:

1. Responses `instructions`, `developer`, and `system` content become a model-visible prefix.
2. Responses user text parts become current SDU `content`.
3. Prior user/assistant messages become SDU `history[n]` form fields through a cleaned-up version of `history_to_form_data()`.
4. Responses `function_call_output` becomes a text block such as `工具 exec_command 返回结果如下: ...`.
5. SDU text chunks become Responses `message` output text or `function_call` items if the tool bridge parser detects a valid tool call block.
6. SDU reasoning chunks parsed from escaped think tags become best-effort Responses reasoning metadata.
7. `input_image` and `input_file` return clear unsupported errors by default.

## Limits

The probe did not inspect browser devtools traffic or unreferenced upload endpoints. That is intentional: the requested boundary only permits existing repo endpoints, actual same-origin AI calls, and necessary local Codex capture. If a future pass uses browser-observed same-origin upload APIs, it should add the exact endpoint, request format, and a non-sensitive test file/image result here.
