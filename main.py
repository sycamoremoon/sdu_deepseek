from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import uuid
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
import queue
import threading
import os
import sduwrap
from sduwrap import ChatConfig
from responses_adapter import (
    append_response_to_conversation,
    build_output_items,
    build_response_object,
    message_output_item,
    normalize_sdu_output,
    prepare_responses_request,
)
from responses_models import ResponsesRequest, UnsupportedInputError, error_response
from responses_store import response_store
from streaming import (
    message_delta_event,
    message_done_events,
    message_start_events,
    response_completed_event,
    response_created_events,
    response_failed_event,
    sse_comment,
    tool_call_events,
)
from fastchat.protocol.openai_api_protocol import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionResponseChoice,
    ChatCompletionResponseStreamChoice,
    ChatCompletionStreamResponse,
    ChatMessage,
    DeltaMessage,
    UsageInfo,
    ModelList,
    ModelCard,
)

COOKIES_FILE = "./cookies.json"
CREDENTIALS_FILE = "./credentials.json"

token_stats = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "requests": 0,
}
stats_lock = threading.Lock()


def load_cookies():
    global sduwrap
    try:
        with open(COOKIES_FILE, "r") as f:
            sduwrap.cookies = json.load(f)
            if not sduwrap.cookies:
                raise FileNotFoundError
        print("[Cookies] Loaded from file")
        return True
    except FileNotFoundError:
        print("[Cookies] No cookies.json found")
        return False


def save_cookies():
    with open(COOKIES_FILE, "w") as f:
        json.dump(sduwrap.cookies, f)
    print("[Cookies] Saved to file")


def load_credentials():
    try:
        with open(CREDENTIALS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_credentials(sdu_id: str, password: str, fingerprint: str = None):
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump({
            "sdu_id": sdu_id,
            "password": password,
            "fingerprint": fingerprint
        }, f)
    print("[Credentials] Saved to file")


def login(sdu_id: str = None, password: str = None, fingerprint: str = None):
    import sdu_aiassist_login as login_module
    import getpass
    
    if not sdu_id or not password:
        creds = load_credentials()
        if creds:
            sdu_id = creds.get("sdu_id")
            password = creds.get("password")
            fingerprint = creds.get("fingerprint")
    
    if not sdu_id:
        sdu_id = input("Please enter your SDU ID: ")
    if not password:
        password = getpass.getpass("Please enter your password: ")
    if not fingerprint:
        fingerprint_input = input("Enter device fingerprint (press Enter to auto-generate): ").strip()
        if fingerprint_input:
            fingerprint = fingerprint_input
        else:
            fingerprint = str(uuid.uuid4())
            print(f"[Login] Generated fingerprint: {fingerprint}")
    
    print(f"[Login] Logging in as {sdu_id}...")
    
    result = login_module.login(sdu_id, password, fingerprint)
    cookies = result.get("cookies", {})
    
    if not cookies:
        print("[Login] Failed!")
        return False
    
    sduwrap.cookies = cookies
    save_cookies()
    
    save_credentials(sdu_id, password, fingerprint)
    
    print("[Login] Success!")
    return True


def check_and_refresh_cookies():
    if not sduwrap.cookies:
        print("[Refresh] No cookies, need login")
        return login()
    
    test_content = "ping"
    try:
        list(sduwrap.chat(test_content, [], ChatConfig()))
        print("[Refresh] Cookies valid")
        return True
    except Exception as e:
        print(f"[Refresh] Cookies expired: {e}")
        return login()


def update_stats(prompt_tokens: int, completion_tokens: int):
    with stats_lock:
        token_stats["prompt_tokens"] += prompt_tokens
        token_stats["completion_tokens"] += completion_tokens
        token_stats["total_tokens"] += prompt_tokens + completion_tokens
        token_stats["requests"] += 1
        
        print(f"\n{'='*60}")
        print(f"[Stats] Requests: {token_stats['requests']} | "
              f"Prompt: {token_stats['prompt_tokens']} | "
              f"Completion: {token_stats['completion_tokens']} | "
              f"Total: {token_stats['total_tokens']}")
        print(f"{'='*60}\n", flush=True)


def print_stats_summary():
    with stats_lock:
        print(f"\n[Stats Summary] "
              f"Requests: {token_stats['requests']} | "
              f"Prompt: {token_stats['prompt_tokens']} | "
              f"Completion: {token_stats['completion_tokens']} | "
              f"Total: {token_stats['total_tokens']}")


if os.environ.get("SDU_DEEPSEEK_SKIP_LOGIN") == "1":
    print("[Cookies] Skipped login because SDU_DEEPSEEK_SKIP_LOGIN=1")
elif not load_cookies():
    login()

app = FastAPI(title="SDU DeepSeek API", description="OpenAI-compatible API for SDU DeepSeek")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_MAP = {
    "deepseek-ai/DeepSeek-V4": "DeepSeek-V4",
    "deepseek-ai/DeepSeek-V3.2": "DeepSeek-V3.2",
    "deepseek-ai/DeepSeek-R1": "DeepSeek-R1",
    "deepseek-ai/DeepSeek-V3": "DeepSeek-V3",
    "deepseek-ai/DeepSeek-V3.2-think": "DeepSeek-V3.2-think",
    "Qwen/Qwen3-235B-A22B-Instruct": "Qwen3-235B-A22B-Instruct",
    "Qwen/Qwen3-235B-A22B-Thinking": "Qwen3-235B-A22B-Thinking",
}

MODELS_DATA = [
    {"id": "deepseek-ai/DeepSeek-V4", "owned_by": "deepseek-ai"},
    {"id": "deepseek-ai/DeepSeek-V3.2", "owned_by": "deepseek-ai"},
    {"id": "deepseek-ai/DeepSeek-R1", "owned_by": "deepseek-ai"},
    {"id": "deepseek-ai/DeepSeek-V3", "owned_by": "deepseek-ai"},
    {"id": "deepseek-ai/DeepSeek-V3.2-think", "owned_by": "deepseek-ai"},
    {"id": "Qwen/Qwen3-235B-A22B-Instruct", "owned_by": "Qwen"},
    {"id": "Qwen/Qwen3-235B-A22B-Thinking", "owned_by": "Qwen"},
]

executor = ThreadPoolExecutor(max_workers=4)


def parse_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "".join(text_parts)
    return str(content)


def get_config_for_model(model: str, thinking_budget: int = 1000) -> ChatConfig:
    config = ChatConfig()
    internal_model = MODEL_MAP.get(model, "DeepSeek-V3.2-think")
    config.set_model(internal_model)
    config.thinking_budget = thinking_budget
    return config


def get_response_thinking_budget(request: ResponsesRequest) -> int:
    if request.thinking_budget:
        return request.thinking_budget
    reasoning = request.reasoning
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if effort == "high":
            return 2000
        if effort == "medium":
            return 1000
        if effort == "low":
            return 500
    return 1000


def collect_sdu_response(current_input: str, history, config: ChatConfig) -> tuple[str, str]:
    full_content = ""
    full_reasoning = ""
    for chunk in sduwrap.chat(current_input, history, config):
        full_content += chunk.get("content", "")
        full_reasoning += chunk.get("reasoning_content", "")
    return normalize_sdu_output(full_content, full_reasoning)


def should_stream_reasoning_events(request: ResponsesRequest) -> bool:
    if os.environ.get("SDU_DEEPSEEK_STREAM_REASONING") == "1":
        return True
    include = request.include
    if isinstance(include, list):
        return any(isinstance(item, str) and "reason" in item.lower() for item in include)
    return False


def debug_enabled() -> bool:
    return os.environ.get("SDU_DEEPSEEK_DEBUG") == "1"


def tool_names(tools) -> list[str]:
    names: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not name and isinstance(tool.get("function"), dict):
            name = tool["function"].get("name")
        if not name:
            name = tool.get("type")
        if name:
            names.append(str(name))
    return names


def input_item_types(items) -> list[str]:
    result: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            result.append(str(item.get("type") or ("message" if "role" in item else "unknown")))
        else:
            result.append(type(item).__name__)
    return result


def has_function_call_output(items) -> bool:
    for item in items or []:
        if isinstance(item, dict) and item.get("type") in {"function_call_output", "custom_tool_call_output"}:
            return True
    return False


def debug_request_event(stage: str, request: ResponsesRequest, response_id: str, prepared=None, extra: dict | None = None):
    if not debug_enabled():
        return
    payload = {
        "stage": stage,
        "response_id": response_id,
        "model": request.model,
        "stream": bool(request.stream),
        "previous_response_id": request.previous_response_id,
        "input_item_types": input_item_types(getattr(prepared, "input_items", [])),
        "has_function_call_output": has_function_call_output(getattr(prepared, "input_items", [])),
        "tools_count": len(request.tools or []),
        "tool_names": tool_names(request.tools),
        "stored_conversation_hit": bool(getattr(prepared, "stored_conversation_hit", False)),
    }
    if extra:
        payload.update(extra)
    print("[ResponsesState]", json.dumps(payload, ensure_ascii=False), flush=True)


def debug_sse_event(response_id: str, event_type: str):
    if not debug_enabled():
        return
    print("[ResponsesSSE]", json.dumps({"response_id": response_id, "event": event_type}, ensure_ascii=False), flush=True)


def debug_compat_event(stage: str, request: ResponsesRequest, output: list[dict], reasoning: str, tool_errors: list[str], response_id: str | None = None):
    if not debug_enabled():
        return
    items = []
    for item in output:
        summary = {"type": item.get("type"), "name": item.get("name")}
        arguments = item.get("arguments")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                summary["argument_keys"] = sorted(parsed.keys())
        if item.get("type") == "custom_tool_call":
            summary["has_input"] = bool(item.get("input"))
        items.append(summary)
    print(
        "[ResponsesCompat]",
        json.dumps(
            {
                "stage": stage,
                "response_id": response_id,
                "model": request.model,
                "stream": bool(request.stream),
                "has_reasoning": bool(reasoning),
                "output_items": items,
                "tool_error_count": len(tool_errors),
                "output_text_empty": output_text_is_empty(output),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def output_has_tool_call(output: list[dict]) -> bool:
    return any(item.get("type") in {"function_call", "custom_tool_call"} for item in output)


def output_text_is_empty(output: list[dict]) -> bool:
    for item in output:
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text" and str(part.get("text", "")).strip():
                return False
    return not output_has_tool_call(output)


def build_empty_output_failed_response(
    request: ResponsesRequest,
    response_id: str,
    created_at: int,
    previous_response_id: str | None,
    tool_errors: list[str] | None = None,
) -> dict:
    message = "SDU backend produced no convertible assistant output."
    if tool_errors:
        message = "SDU backend produced a tool call that could not be converted safely."
    return build_response_object(
        request,
        response_id=response_id,
        created_at=created_at,
        output=[],
        status="failed",
        previous_response_id=previous_response_id,
        error={"message": message, "type": "sdu_empty_output", "code": "sdu_empty_output"},
    )


def response_not_found(response_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=error_response(
            "response_not_found",
            f"Response {response_id} not found in the in-memory store.",
            "response_id",
        ),
    )


@app.get("/v1/responses/{response_id}/input_items")
@app.get("/responses/{response_id}/input_items")
async def get_response_input_items(response_id: str):
    stored = response_store.get(response_id)
    if not stored:
        return response_not_found(response_id)
    return {
        "object": "list",
        "data": stored.input_items,
        "has_more": False,
        "first_id": stored.input_items[0].get("id") if stored.input_items and isinstance(stored.input_items[0], dict) else None,
        "last_id": stored.input_items[-1].get("id") if stored.input_items and isinstance(stored.input_items[-1], dict) else None,
    }


@app.get("/v1/responses/{response_id}")
@app.get("/responses/{response_id}")
async def get_response(response_id: str):
    response = response_store.get_response(response_id)
    if not response:
        return response_not_found(response_id)
    return response


@app.delete("/v1/responses/{response_id}")
@app.delete("/responses/{response_id}")
async def delete_response(response_id: str):
    deleted = response_store.delete(response_id)
    return {"id": response_id, "object": "response.deleted", "deleted": deleted}


@app.post("/v1/responses")
@app.post("/responses")
async def openai_responses(request: ResponsesRequest):
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    created_at = int(time.time())
    try:
        prepared = prepare_responses_request(request, response_store)
    except UnsupportedInputError as exc:
        return JSONResponse(status_code=400, content=exc.as_error())

    config = get_config_for_model(request.model, get_response_thinking_budget(request))
    debug_request_event("request_received", request, response_id, prepared)

    if request.stream:
        return StreamingResponse(
            generate_responses_stream(request, prepared, config, response_id, created_at),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    prompt_tokens = len(prepared.current_input) + sum(len(message.content) for message in prepared.history)
    try:
        full_content, full_reasoning = collect_sdu_response(
            prepared.current_input,
            prepared.to_sdu_history(),
            config,
        )
    except Exception as exc:
        response = build_response_object(
            request,
            response_id=response_id,
            created_at=created_at,
            output=[],
            status="failed",
            previous_response_id=request.previous_response_id,
            error={"message": str(exc), "type": "sdu_backend_error", "code": "sdu_backend_error"},
        )
        response_store.put(response, prepared.conversation, prepared.input_items, persistent=request.store is not False)
        return JSONResponse(status_code=502, content=response)

    output, tool_errors = build_output_items(full_content, full_reasoning, request)
    debug_compat_event("non_stream_output", request, output, full_reasoning, tool_errors, response_id=response_id)
    if not output or output_text_is_empty(output):
        response = build_empty_output_failed_response(request, response_id, created_at, request.previous_response_id, tool_errors)
        if tool_errors:
            response["compatibility_warnings"] = tool_errors
        response_store.put(response, prepared.conversation, prepared.input_items, persistent=request.store is not False)
        debug_request_event("response_failed_empty_output", request, response_id, prepared, {"tool_error_count": len(tool_errors)})
        return JSONResponse(status_code=502, content=response)

    response = build_response_object(
        request,
        response_id=response_id,
        created_at=created_at,
        output=output,
        reasoning_text=full_reasoning,
        previous_response_id=request.previous_response_id,
    )
    if tool_errors:
        response["compatibility_warnings"] = tool_errors
    conversation = append_response_to_conversation(prepared.conversation, output)
    response_store.put(response, conversation, prepared.input_items, persistent=request.store is not False)
    debug_request_event(
        "response_completed",
        request,
        response_id,
        prepared,
        {
            "output_item_types": [item.get("type") for item in output],
            "has_function_call": output_has_tool_call(output),
            "output_text_empty": output_text_is_empty(output),
        },
    )

    completion_tokens = len(response.get("output_text", "")) + len(full_reasoning)
    update_stats(prompt_tokens, completion_tokens)
    return response


async def generate_responses_stream(
    request: ResponsesRequest,
    prepared,
    config: ChatConfig,
    response_id: str,
    created_at: int,
):
    seed_response = build_response_object(
        request,
        response_id=response_id,
        created_at=created_at,
        output=[],
        status="in_progress",
        previous_response_id=request.previous_response_id,
    )
    for event in response_created_events(seed_response):
        debug_sse_event(response_id, "response.created" if "response.created" in event else "response.in_progress")
        yield event

    q = queue.Queue()
    loop = asyncio.get_event_loop()
    history = prepared.to_sdu_history()

    def run_chat():
        try:
            for chunk in sduwrap.chat(prepared.current_input, history, config):
                q.put(chunk)
        except Exception as exc:
            q.put({"error": str(exc)})
        finally:
            q.put(None)

    thread = threading.Thread(target=run_chat)
    thread.start()

    def get_next_chunk():
        try:
            return q.get(timeout=15)
        except queue.Empty:
            return {"keepalive": True}

    content = ""
    reasoning = ""
    prompt_tokens = len(prepared.current_input) + sum(len(message.content) for message in prepared.history)
    buffer_for_tools = bool(request.tools)
    emit_reasoning_events = should_stream_reasoning_events(request)
    stream_item = None

    if not buffer_for_tools:
        stream_item = message_output_item("")
        for event in message_start_events(stream_item):
            if "event: " in event:
                debug_sse_event(response_id, event.split("\n", 1)[0].replace("event: ", ""))
            yield event

    failed_response = None
    while True:
        chunk = await loop.run_in_executor(executor, get_next_chunk)
        if chunk is None:
            break
        if chunk.get("keepalive"):
            debug_sse_event(response_id, "keepalive")
            yield sse_comment("keepalive")
            continue
        if "error" in chunk:
            failed_response = build_response_object(
                request,
                response_id=response_id,
                created_at=created_at,
                output=[],
                status="failed",
                previous_response_id=request.previous_response_id,
                error={"message": chunk["error"], "type": "sdu_backend_error", "code": "sdu_backend_error"},
            )
            debug_sse_event(response_id, "response.failed")
            yield response_failed_event(failed_response)
            break

        delta = chunk.get("content", "")
        reasoning_delta = chunk.get("reasoning_content", "")
        if reasoning_delta:
            reasoning += reasoning_delta
            if emit_reasoning_events:
                debug_sse_event(response_id, "response.reasoning_summary_text.delta")
                yield (
                    "event: response.reasoning_summary_text.delta\n"
                    f"data: {json.dumps({'type': 'response.reasoning_summary_text.delta', 'delta': reasoning_delta}, ensure_ascii=False)}\n\n"
                )
        if delta:
            content += delta
            if stream_item is not None:
                stream_item["content"][0]["text"] += delta
                debug_sse_event(response_id, "response.output_text.delta")
                yield message_delta_event(stream_item, delta)

    thread.join()

    if failed_response is not None:
        response_store.put(failed_response, prepared.conversation, prepared.input_items, persistent=request.store is not False)
        return

    content, reasoning = normalize_sdu_output(content, reasoning)

    if buffer_for_tools:
        output, tool_errors = build_output_items(content, reasoning, request)
        debug_compat_event("stream_buffered_output", request, output, reasoning, tool_errors, response_id=response_id)
        if not output or output_text_is_empty(output):
            failed_response = build_empty_output_failed_response(request, response_id, created_at, request.previous_response_id, tool_errors)
            if tool_errors:
                failed_response["compatibility_warnings"] = tool_errors
            response_store.put(failed_response, prepared.conversation, prepared.input_items, persistent=request.store is not False)
            debug_sse_event(response_id, "response.failed")
            yield response_failed_event(failed_response)
            return
        for output_index, item in enumerate(output):
            if item.get("type") in {"function_call", "custom_tool_call"}:
                for event in tool_call_events(item, output_index):
                    if "event: " in event:
                        debug_sse_event(response_id, event.split("\n", 1)[0].replace("event: ", ""))
                    yield event
            else:
                for event in message_start_events(item, output_index):
                    if "event: " in event:
                        debug_sse_event(response_id, event.split("\n", 1)[0].replace("event: ", ""))
                    yield event
                text = item.get("content", [{}])[0].get("text", "")
                if text:
                    debug_sse_event(response_id, "response.output_text.delta")
                    yield message_delta_event(item, text, output_index)
                for event in message_done_events(item, output_index):
                    if "event: " in event:
                        debug_sse_event(response_id, event.split("\n", 1)[0].replace("event: ", ""))
                    yield event
    else:
        output = [stream_item] if stream_item is not None else [message_output_item(content)]
        tool_errors = []
        debug_compat_event("stream_text_output", request, output, reasoning, tool_errors, response_id=response_id)
        if not output or output_text_is_empty(output):
            failed_response = build_empty_output_failed_response(request, response_id, created_at, request.previous_response_id, tool_errors)
            response_store.put(failed_response, prepared.conversation, prepared.input_items, persistent=request.store is not False)
            debug_sse_event(response_id, "response.failed")
            yield response_failed_event(failed_response)
            return
        if stream_item is not None:
            for event in message_done_events(stream_item):
                if "event: " in event:
                    debug_sse_event(response_id, event.split("\n", 1)[0].replace("event: ", ""))
                yield event

    if reasoning and emit_reasoning_events:
        debug_sse_event(response_id, "response.reasoning_summary_text.done")
        yield (
            "event: response.reasoning_summary_text.done\n"
            f"data: {json.dumps({'type': 'response.reasoning_summary_text.done', 'text': reasoning}, ensure_ascii=False)}\n\n"
        )

    response = build_response_object(
        request,
        response_id=response_id,
        created_at=created_at,
        output=output,
        reasoning_text=reasoning,
        previous_response_id=request.previous_response_id,
    )
    if tool_errors:
        response["compatibility_warnings"] = tool_errors
    conversation = append_response_to_conversation(prepared.conversation, output)
    response_store.put(response, conversation, prepared.input_items, persistent=request.store is not False)
    debug_request_event(
        "stream_response_completed",
        request,
        response_id,
        prepared,
        {
            "output_item_types": [item.get("type") for item in output],
            "has_function_call": output_has_tool_call(output),
            "output_text_empty": output_text_is_empty(output),
        },
    )
    update_stats(prompt_tokens, len(response.get("output_text", "")) + len(reasoning))
    debug_sse_event(response_id, "response.completed")
    yield response_completed_event(response)


@app.get("/v1/models")
async def list_models():
    models = [
        ModelCard(
            id=m["id"],
            object="model",
            created=1700000000,
            owned_by=m["owned_by"],
        )
        for m in MODELS_DATA
    ]
    return ModelList(data=models).model_dump()


@app.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    for m in MODELS_DATA:
        if m["id"] == model_id:
            model = ModelCard(
                id=m["id"],
                object="model",
                created=1700000000,
                owned_by=m["owned_by"],
            )
            return model.model_dump()
    raise HTTPException(status_code=404, detail={"error": {"message": f"Model {model_id} not found", "type": "invalid_request_error", "code": "model_not_found"}})


@app.post("/v1/chat/completions")
async def openai_chat_completion(request: ChatCompletionRequest):
    messages = request.messages
    stream = request.stream
    model = request.model
    thinking_budget = getattr(request, 'thinking_budget', 1000) or 1000
    
    config = get_config_for_model(model, thinking_budget)
    
    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"message": "Invalid messages format", "type": "invalid_request_error", "code": "invalid_messages"}})
    
    last_msg = messages[-1]
    last_role = last_msg.role if hasattr(last_msg, 'role') else last_msg.get('role')
    if last_role != "user":
        raise HTTPException(status_code=400, detail={"error": {"message": "Invalid messages format", "type": "invalid_request_error", "code": "invalid_messages"}})
    
    raw_content = last_msg.content if hasattr(last_msg, 'content') else last_msg.get('content')
    current_input = parse_content(raw_content)
    
    history = messages[:-1]
    
    prompt_tokens = len(str(current_input)) + sum(len(parse_content(m.content if hasattr(m, 'content') else m.get('content'))) for m in history)
    
    if stream:
        async def generate_stream():
            completion_tokens = 0
            response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
            created = int(time.time())
            
            q = queue.Queue()
            loop = asyncio.get_event_loop()
            
            request_history = []
            for chat_session in history:
                cs = sduwrap.ChatSession()
                cs.role = chat_session.role if hasattr(chat_session, 'role') else chat_session.get('role')
                raw_hist_content = chat_session.content if hasattr(chat_session, 'content') else chat_session.get('content')
                cs.content = parse_content(raw_hist_content)
                request_history.append(cs)
            
            def run_chat():
                try:
                    for chunk in sduwrap.chat(current_input, request_history, config):
                        q.put(chunk)
                except Exception as e:
                    q.put({"error": str(e)})
                finally:
                    q.put(None)
            
            thread = threading.Thread(target=run_chat)
            thread.start()
            
            while True:
                chunk = await loop.run_in_executor(executor, q.get)
                if chunk is None:
                    break
                if "error" in chunk:
                    break
                
                content = chunk.get("content", "")
                reasoning = chunk.get("reasoning_content", "")
                content, reasoning = normalize_sdu_output(content, reasoning)
                completion_tokens += len(content) + len(reasoning)
                
                if reasoning:
                    stream_response = ChatCompletionStreamResponse(
                        id=response_id,
                        object="chat.completion.chunk",
                        created=created,
                        model=model,
                        choices=[
                            ChatCompletionResponseStreamChoice(
                                index=0,
                                delta=DeltaMessage(reasoning_content=reasoning),
                                finish_reason=None,
                            )
                        ],
                    )
                    payload = stream_response.model_dump()
                    payload["choices"][0]["delta"]["reasoning_content"] = reasoning
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                
                if content:
                    stream_response = ChatCompletionStreamResponse(
                        id=response_id,
                        object="chat.completion.chunk",
                        created=created,
                        model=model,
                        choices=[
                            ChatCompletionResponseStreamChoice(
                                index=0,
                                delta=DeltaMessage(content=content),
                                finish_reason=None,
                            )
                        ],
                    )
                    yield f"data: {stream_response.model_dump_json()}\n\n"
            
            thread.join()
            
            update_stats(prompt_tokens, completion_tokens)
            
            final_response = ChatCompletionStreamResponse(
                id=response_id,
                object="chat.completion.chunk",
                created=created,
                model=model,
                choices=[
                    ChatCompletionResponseStreamChoice(
                        index=0,
                        delta=DeltaMessage(),
                        finish_reason="stop",
                    )
                ],
            )
            yield f"data: {final_response.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
        
        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    
    else:
        full_content = ""
        full_reasoning = ""
        
        request_history = []
        for chat_session in history:
            cs = sduwrap.ChatSession()
            cs.role = chat_session.role if hasattr(chat_session, 'role') else chat_session.get('role')
            raw_hist_content = chat_session.content if hasattr(chat_session, 'content') else chat_session.get('content')
            cs.content = parse_content(raw_hist_content)
            request_history.append(cs)
        
        for chunk in sduwrap.chat(current_input, request_history, config):
            full_content += chunk.get("content", "")
            full_reasoning += chunk.get("reasoning_content", "")
        full_content, full_reasoning = normalize_sdu_output(full_content, full_reasoning)
        
        completion_tokens = len(full_content) + len(full_reasoning)
        update_stats(prompt_tokens, completion_tokens)
        
        message = ChatMessage(
            role="assistant",
            content=full_content,
        )
        
        response = ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
            object="chat.completion",
            created=int(time.time()),
            model=model,
            choices=[
                ChatCompletionResponseChoice(
                    index=0,
                    message=message,
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )
        
        payload = response.model_dump()
        if full_reasoning:
            payload["choices"][0]["message"]["reasoning_content"] = full_reasoning
        return payload


if __name__ == "__main__":
    print(f"\n{'='*50}")
    print("SDU DeepSeek API Server")
    print(f"{'='*50}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
