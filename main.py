from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import uuid
import time
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
import queue
import threading
import os
from copy import deepcopy
from typing import Any, Dict, List, Tuple
import sduwrap
from sduwrap import ChatConfig
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
response_store = {}
response_store_lock = threading.Lock()

REASONING_EFFORT_TO_BUDGET = {
    "none": 0,
    "low": 500,
    "medium": 1000,
    "high": 2000,
    "xhigh": 4000,
}

TOOL_PROMPT_PREFIX = "[responses-tool-proxy]"


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


if not load_cookies():
    login()

app = FastAPI(
    title="SDU DeepSeek API",
    description="OpenAI-compatible API for SDU DeepSeek with Responses API proxy support",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_MAP = {
    "deepseek-ai/DeepSeek-V3.2": "DeepSeek-V3.2",
    "deepseek-ai/DeepSeek-R1": "DeepSeek-R1",
    "deepseek-ai/DeepSeek-V3": "DeepSeek-V3",
    "deepseek-ai/DeepSeek-V3.2-think": "DeepSeek-V3.2-think",
    "Qwen/Qwen3-235B-A22B-Instruct": "Qwen3-235B-A22B-Instruct",
    "Qwen/Qwen3-235B-A22B-Thinking": "Qwen3-235B-A22B-Thinking",
    "gpt-5-codex": "DeepSeek-V3.2-think",
    "gpt-5.1-codex": "DeepSeek-V3.2-think",
    "gpt-5.2-codex": "DeepSeek-V3.2-think",
    "gpt-5.3-codex": "DeepSeek-V3.2-think",
    "codex-mini-latest": "DeepSeek-V3.2-think",
}

MODELS_DATA = [
    {"id": "deepseek-ai/DeepSeek-V3.2", "owned_by": "deepseek-ai"},
    {"id": "deepseek-ai/DeepSeek-R1", "owned_by": "deepseek-ai"},
    {"id": "deepseek-ai/DeepSeek-V3", "owned_by": "deepseek-ai"},
    {"id": "deepseek-ai/DeepSeek-V3.2-think", "owned_by": "deepseek-ai"},
    {"id": "Qwen/Qwen3-235B-A22B-Instruct", "owned_by": "Qwen"},
    {"id": "Qwen/Qwen3-235B-A22B-Thinking", "owned_by": "Qwen"},
    {"id": "gpt-5-codex", "owned_by": "openai-proxy"},
    {"id": "gpt-5.1-codex", "owned_by": "openai-proxy"},
    {"id": "gpt-5.2-codex", "owned_by": "openai-proxy"},
    {"id": "gpt-5.3-codex", "owned_by": "openai-proxy"},
    {"id": "codex-mini-latest", "owned_by": "openai-proxy"},
]

executor = ThreadPoolExecutor(max_workers=4)


def parse_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type in {"text", "input_text", "output_text", "summary_text"}:
                    text_parts.append(item.get("text", ""))
                elif item_type == "input_image":
                    text_parts.append("[image input omitted]")
                elif item_type == "input_file":
                    file_label = item.get("filename") or item.get("file_id") or item.get("file_url") or "file"
                    text_parts.append(f"[file input: {file_label}]")
                elif "text" in item:
                    text_parts.append(str(item.get("text", "")))
            else:
                text_parts.append(str(item))
        return "".join(text_parts)
    if isinstance(content, dict):
        if "content" in content:
            return parse_content(content.get("content"))
        if "text" in content:
            return str(content.get("text", ""))
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def raise_openai_error(status_code: int, message: str, code: str, error_type: str = None):
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "message": message,
                "type": error_type or ("invalid_request_error" if status_code < 500 else "server_error"),
                "code": code,
            }
        },
    )


def normalize_role(role: str) -> str:
    if role in {"system", "developer"}:
        return "system"
    if role == "assistant":
        return "assistant"
    return "user"


def get_message_role(message: Any) -> str:
    if hasattr(message, "role"):
        return normalize_role(getattr(message, "role"))
    if isinstance(message, dict):
        return normalize_role(message.get("role"))
    return "user"


def get_message_content(message: Any) -> Any:
    if hasattr(message, "content"):
        return getattr(message, "content")
    if isinstance(message, dict):
        return message.get("content")
    return message


def stringify_item(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def json_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def normalize_tool_definition(tool: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(tool, dict):
        return None

    tool_type = tool.get("type")
    if tool_type == "function":
        name = tool.get("name") or (tool.get("function") or {}).get("name")
        if not name:
            return None
        return {
            "type": "function_call",
            "name": name,
            "description": tool.get("description") or (tool.get("function") or {}).get("description") or "",
            "parameters": deepcopy(tool.get("parameters") or (tool.get("function") or {}).get("parameters") or {"type": "object", "properties": {}}),
        }

    if tool_type == "custom":
        name = tool.get("name")
        if not name:
            return None
        return {
            "type": "custom_tool_call",
            "name": name,
            "description": tool.get("description") or "",
            "input_schema": deepcopy(tool.get("input_schema") or tool.get("format") or {"type": "string"}),
        }

    return None


def build_tool_system_prompt(tools: List[Dict[str, Any]], tool_choice: Any) -> str | None:
    normalized_tools = [item for item in (normalize_tool_definition(tool) for tool in tools) if item]
    if not normalized_tools:
        return None

    tools_json = json.dumps(normalized_tools, ensure_ascii=False, indent=2)
    tool_choice_json = json.dumps(tool_choice if tool_choice is not None else "auto", ensure_ascii=False)
    return (
        f"{TOOL_PROMPT_PREFIX}\n"
        "You may use tools. Available tool definitions are below.\n"
        f"{tools_json}\n\n"
        "When you decide to call a tool, do not explain anything and do not wrap it in Markdown.\n"
        "Return exactly one XML block in this format:\n"
        "<tool_call>\n"
        "{\"tool_calls\":[{\"type\":\"function_call\"|\"custom_tool_call\",\"name\":\"tool_name\",\"arguments\":{...},\"input\":\"...\",\"call_id\":\"call_optional\"}]}\n"
        "</tool_call>\n\n"
        "Rules:\n"
        "- For function_call, provide `arguments` as a JSON object.\n"
        "- For custom_tool_call, provide `input` as a string. If you need structured data, JSON-stringify it into `input`.\n"
        "- Prefer a single tool call at a time unless parallel tool calls are clearly required.\n"
        "- If you are not calling a tool, answer normally as plain text and do not include <tool_call>.\n"
        f"- Requested tool_choice: {tool_choice_json}."
    )


def is_internal_tool_prompt_message(message: Dict[str, Any]) -> bool:
    return (
        isinstance(message, dict)
        and normalize_role(message.get("role")) == "system"
        and isinstance(message.get("content"), str)
        and message.get("content", "").startswith(TOOL_PROMPT_PREFIX)
    )


def build_tool_call_history_text(item: Dict[str, Any]) -> str:
    call_id = item.get("call_id") or f"call_{uuid.uuid4().hex[:16]}"
    if item.get("type") == "custom_tool_call":
        payload = {
            "tool_calls": [{
                "type": "custom_tool_call",
                "name": item.get("name") or call_id,
                "input": stringify_item(item.get("input")),
                "call_id": call_id,
            }]
        }
    else:
        arguments = item.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        payload = {
            "tool_calls": [{
                "type": "function_call",
                "name": item.get("name") or call_id,
                "arguments": arguments if arguments is not None else {},
                "call_id": call_id,
            }]
        }
    return f"<tool_call>\n{json.dumps(payload, ensure_ascii=False)}\n</tool_call>"


def build_tool_output_history_text(item: Dict[str, Any]) -> str:
    payload = {
        "type": item.get("type"),
        "name": item.get("name"),
        "call_id": item.get("call_id"),
        "output": item.get("output"),
    }
    return f"<tool_result>\n{json.dumps(payload, ensure_ascii=False)}\n</tool_result>"


def response_input_item_to_messages(item: Any) -> List[Dict[str, str]]:
    if item is None:
        return []
    if isinstance(item, str):
        return [{"role": "user", "content": item}]
    if not isinstance(item, dict):
        return [{"role": "user", "content": str(item)}]

    item_type = item.get("type")

    if item_type == "message" or "role" in item:
        return [{
            "role": normalize_role(item.get("role")),
            "content": parse_content(item.get("content")),
        }]

    if item_type in {"input_text", "input_image", "input_file"}:
        return [{"role": "user", "content": parse_content([item])}]

    if item_type in {"function_call_output", "custom_tool_call_output"}:
        return [{"role": "user", "content": build_tool_output_history_text(item)}]

    if item_type in {"function_call", "custom_tool_call"}:
        return [{"role": "assistant", "content": build_tool_call_history_text(item)}]

    if "content" in item:
        return [{
            "role": normalize_role(item.get("role")),
            "content": parse_content(item.get("content")),
        }]

    if "text" in item:
        return [{"role": "user", "content": str(item.get("text", ""))}]

    return [{"role": "user", "content": parse_content(item)}]


def get_request_history(history_messages: List[Any]):
    request_history = []
    for chat_session in history_messages:
        cs = sduwrap.ChatSession()
        cs.role = get_message_role(chat_session)
        cs.content = parse_content(get_message_content(chat_session))
        request_history.append(cs)
    return request_history


def merge_adjacent_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    merged = []
    for message in messages:
        role = normalize_role(message.get("role"))
        content = parse_content(message.get("content"))

        if not merged or merged[-1]["role"] != role:
            merged.append({
                "role": role,
                "content": content,
            })
            continue

        if content:
            previous_content = merged[-1]["content"]
            merged[-1]["content"] = f"{previous_content}\n{content}".strip() if previous_content else content

    return merged


def prepare_chat_messages(messages: List[Any]) -> Tuple[List[Any], str, int]:
    if not messages:
        raise_openai_error(400, "Invalid messages format", "invalid_messages")

    last_msg = messages[-1]
    if get_message_role(last_msg) != "user":
        raise_openai_error(400, "Invalid messages format", "invalid_messages")

    current_input = parse_content(get_message_content(last_msg))
    history = messages[:-1]
    prompt_tokens = len(current_input) + sum(
        len(parse_content(get_message_content(message))) for message in history
    )

    return history, current_input, prompt_tokens


def get_thinking_budget_from_responses_request(request_data: Dict[str, Any]) -> int:
    if request_data.get("thinking_budget") is not None:
        return request_data.get("thinking_budget") or 1000

    reasoning = request_data.get("reasoning") or {}
    effort = reasoning.get("effort")
    return REASONING_EFFORT_TO_BUDGET.get(effort, 1000)


async def stream_backend_chat(current_input: str, history_messages: List[Any], config: ChatConfig):
    q = queue.Queue()
    loop = asyncio.get_running_loop()
    request_history = get_request_history(history_messages)

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

    try:
        while True:
            chunk = await loop.run_in_executor(executor, q.get)
            if chunk is None:
                break
            if "error" in chunk:
                raise RuntimeError(chunk["error"])
            yield chunk
    finally:
        thread.join()


def run_backend_chat(current_input: str, history_messages: List[Any], config: ChatConfig) -> Tuple[str, str]:
    full_content = ""
    full_reasoning = ""
    request_history = get_request_history(history_messages)

    try:
        for chunk in sduwrap.chat(current_input, request_history, config):
            full_content += chunk.get("content", "")
            full_reasoning += chunk.get("reasoning_content", "")
    except Exception as e:
        raise_openai_error(502, f"Upstream chat failed: {e}", "upstream_chat_failed", "api_error")

    return full_content, full_reasoning


def build_responses_messages(request_data: Dict[str, Any]) -> List[Dict[str, str]]:
    messages = []
    previous_response_id = request_data.get("previous_response_id")

    if previous_response_id:
        with response_store_lock:
            stored = response_store.get(previous_response_id)
        if not stored:
            raise_openai_error(404, f"Response {previous_response_id} not found", "response_not_found")
        messages.extend(
            deepcopy(message)
            for message in stored["messages"]
            if not is_internal_tool_prompt_message(message)
        )

    instructions = request_data.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    tool_system_prompt = build_tool_system_prompt(
        request_data.get("tools") or [],
        request_data.get("tool_choice", "auto"),
    )
    if tool_system_prompt:
        messages.append({"role": "system", "content": tool_system_prompt})

    input_data = request_data.get("input")
    if isinstance(input_data, list):
        for item in input_data:
            messages.extend(response_input_item_to_messages(item))
    elif input_data is not None:
        messages.extend(response_input_item_to_messages(input_data))

    return merge_adjacent_messages(messages)


def build_responses_output_message(message_id: str, content: str, status: str = "completed") -> Dict[str, Any]:
    if status != "completed":
        return {
            "id": message_id,
            "type": "message",
            "status": status,
            "role": "assistant",
            "content": [],
        }

    return {
        "id": message_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{
            "type": "output_text",
            "text": content,
            "annotations": [],
        }],
    }


def build_responses_usage(prompt_tokens: int, content: str, reasoning: str) -> Dict[str, Any]:
    output_tokens = len(content) + len(reasoning)
    return {
        "input_tokens": prompt_tokens,
        "input_tokens_details": {
            "cached_tokens": 0,
        },
        "output_tokens": output_tokens,
        "output_tokens_details": {
            "reasoning_tokens": len(reasoning),
        },
        "total_tokens": prompt_tokens + output_tokens,
    }


def build_responses_custom_tool_call_message(message_id: str, tool_name: str, tool_input: str, call_id: str = None, status: str = "completed") -> Dict[str, Any]:
    return {
        "id": message_id,
        "type": "custom_tool_call",
        "status": status,
        "name": tool_name,
        "input": tool_input,
        "call_id": call_id or f"call_{uuid.uuid4().hex[:16]}",
    }


def build_assistant_history_from_output_items(output_items: List[Dict[str, Any]]) -> str:
    parts = []
    for item in output_items:
        item_type = item.get("type")
        if item_type == "message":
            text_parts = []
            for content_part in item.get("content") or []:
                if content_part.get("type") == "output_text":
                    text_parts.append(content_part.get("text", ""))
            text = "".join(text_parts).strip()
            if text:
                parts.append(text)
        elif item_type in {"function_call", "custom_tool_call"}:
            if item_type == "custom_tool_call":
                parts.append(build_tool_call_history_text({
                    "type": "custom_tool_call",
                    "name": item.get("name"),
                    "input": item.get("input", ""),
                    "call_id": item.get("call_id"),
                }))
            else:
                arguments = item.get("arguments", "{}")
                try:
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except json.JSONDecodeError:
                    pass
                parts.append(build_tool_call_history_text({
                    "type": "function_call",
                    "name": item.get("name"),
                    "arguments": arguments,
                    "call_id": item.get("call_id"),
                }))
    return "\n".join(part for part in parts if part)


def parse_assistant_output(content: str) -> Dict[str, Any]:
    text = (content or "").strip()
    if not text:
        return {"kind": "text", "text": "", "items": [], "output_text": "", "history_text": ""}

    cleaned = re.sub(r"^\s*```(?:xml|json)?\s*|\s*```\s*$", "", content or "", flags=re.DOTALL)
    pattern = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", flags=re.DOTALL)
    matches = list(pattern.finditer(cleaned))

    if not matches:
        output_items = [build_responses_output_message(f"msg_{uuid.uuid4().hex}", content or "", "completed")]
        return {
            "kind": "text",
            "text": content or "",
            "items": output_items,
            "output_text": content or "",
            "history_text": build_assistant_history_from_output_items(output_items),
        }

    items = []
    cursor = 0
    for match in matches:
        prefix = cleaned[cursor:match.start()]
        if prefix.strip():
            items.append(build_responses_output_message(f"msg_{uuid.uuid4().hex}", prefix.strip(), "completed"))

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            items.append(build_responses_output_message(f"msg_{uuid.uuid4().hex}", match.group(0), "completed"))
            cursor = match.end()
            continue

        raw_calls = payload.get("tool_calls")
        if raw_calls is None and all(key in payload for key in ("type", "name")):
            raw_calls = [payload]

        if not isinstance(raw_calls, list) or not raw_calls:
            items.append(build_responses_output_message(f"msg_{uuid.uuid4().hex}", match.group(0), "completed"))
            cursor = match.end()
            continue

        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                continue

            call_type = raw_call.get("type") or "function_call"
            call_id = raw_call.get("call_id") or f"call_{uuid.uuid4().hex[:16]}"
            name = raw_call.get("name") or call_id

            if call_type == "custom_tool_call":
                tool_input = stringify_item(raw_call.get("input"))
                items.append(build_responses_custom_tool_call_message(f"msg_{uuid.uuid4().hex}", name, tool_input, call_id, "completed"))
            else:
                arguments_value = raw_call.get("arguments")
                arguments_text = json_string(arguments_value if arguments_value is not None else {})
                items.append(build_responses_function_call_message(f"msg_{uuid.uuid4().hex}", name, arguments_text, call_id, "completed"))

        cursor = match.end()

    suffix = cleaned[cursor:]
    if suffix.strip():
        items.append(build_responses_output_message(f"msg_{uuid.uuid4().hex}", suffix.strip(), "completed"))

    if not items:
        items = [build_responses_output_message(f"msg_{uuid.uuid4().hex}", content or "", "completed")]

    output_text_parts = []
    has_tool_calls = False
    for item in items:
        if item.get("type") in {"function_call", "custom_tool_call"}:
            has_tool_calls = True
            continue
        for content_part in item.get("content") or []:
            if content_part.get("type") == "output_text":
                output_text_parts.append(content_part.get("text", ""))

    output_text = "\n".join(part for part in output_text_parts if part)
    return {
        "kind": "mixed" if has_tool_calls and output_text else ("tool_calls" if has_tool_calls else "text"),
        "text": output_text,
        "items": items,
        "output_text": output_text,
        "history_text": build_assistant_history_from_output_items(items),
    }


def build_responses_payload(
    request_data: Dict[str, Any],
    response_id: str,
    output_items: List[Dict[str, Any]],
    output_text: str,
    usage_text: str,
    reasoning_text: str,
    prompt_tokens: int,
    created_at: int,
    status: str,
) -> Dict[str, Any]:
    text_config = deepcopy(request_data.get("text") or {"format": {"type": "text"}})
    if "format" not in text_config:
        text_config["format"] = {"type": "text"}

    reasoning_request = deepcopy(request_data.get("reasoning") or {})

    payload = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": request_data.get("instructions"),
        "max_output_tokens": request_data.get("max_output_tokens"),
        "model": request_data.get("model"),
        "output": deepcopy(output_items),
        "parallel_tool_calls": request_data.get("parallel_tool_calls", True),
        "previous_response_id": request_data.get("previous_response_id"),
        "reasoning": {
            "effort": reasoning_request.get("effort"),
            "summary": None,
        },
        "store": request_data.get("store", True),
        "temperature": request_data.get("temperature", 1.0),
        "text": text_config,
        "tool_choice": request_data.get("tool_choice", "auto"),
        "tools": deepcopy(request_data.get("tools") or []),
        "top_p": request_data.get("top_p", 1.0),
        "truncation": request_data.get("truncation", "disabled"),
        "usage": None,
        "user": request_data.get("user"),
        "metadata": deepcopy(request_data.get("metadata") or {}),
    }

    if request_data.get("background") is not None:
        payload["background"] = request_data.get("background")
    if request_data.get("conversation") is not None:
        payload["conversation"] = deepcopy(request_data.get("conversation"))
    if request_data.get("max_tool_calls") is not None:
        payload["max_tool_calls"] = request_data.get("max_tool_calls")
    if request_data.get("service_tier") is not None:
        payload["service_tier"] = request_data.get("service_tier")

    if status == "completed":
        payload["completed_at"] = int(time.time())
        payload["usage"] = build_responses_usage(prompt_tokens, usage_text, reasoning_text)
        payload["output_text"] = output_text

    return payload


def sse_event(event_name: str, payload: Dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def maybe_store_response(response_id: str, messages: List[Dict[str, str]], payload: Dict[str, Any]):
    if not payload.get("store", True):
        return

    with response_store_lock:
        response_store[response_id] = {
            "messages": deepcopy(messages),
            "response": deepcopy(payload),
        }


def build_responses_function_call_message(message_id: str, tool_name: str, arguments: str, call_id: str = None, status: str = "completed") -> Dict[str, Any]:
    return {
        "id": message_id,
        "type": "function_call",
        "status": status,
        "name": tool_name,
        "arguments": arguments,
        "call_id": call_id or f"call_{uuid.uuid4().hex[:16]}"
    }

def get_config_for_model(model: str, thinking_budget: int = 1000, tools: List[Dict] = None) -> ChatConfig:
    config = ChatConfig()
    internal_model = MODEL_MAP.get(model, "DeepSeek-V3.2-think")
    config.set_model(internal_model)
    config.thinking_budget = thinking_budget
    if tools:
        config.tools = tools
    return config


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
    thinking_budget = getattr(request, "thinking_budget", 1000) or 1000
    config = get_config_for_model(model, thinking_budget)
    history, current_input, prompt_tokens = prepare_chat_messages(messages)

    if stream:
        async def generate_stream():
            completion_tokens = 0
            response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
            created = int(time.time())

            try:
                async for chunk in stream_backend_chat(current_input, history, config):
                    content = chunk.get("content", "")
                    reasoning = chunk.get("reasoning_content", "")
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
                        yield f"data: {stream_response.model_dump_json()}\n\n"

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
            except RuntimeError as e:
                error_payload = {
                    "error": {
                        "message": str(e),
                        "type": "api_error",
                        "code": "upstream_chat_failed",
                    }
                }
                yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
                return

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

    full_content, full_reasoning = run_backend_chat(current_input, history, config)
    completion_tokens = len(full_content) + len(full_reasoning)
    update_stats(prompt_tokens, completion_tokens)

    message = ChatMessage(
        role="assistant",
        content=full_content,
    )
    if full_reasoning:
        message.reasoning_content = full_reasoning

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

    return response.model_dump()


@app.post("/v1/responses")
async def openai_responses(request: Request):
    try:
        request_data = await request.json()
    except json.JSONDecodeError:
        raise_openai_error(400, "Invalid JSON body", "invalid_json")

    if not isinstance(request_data, dict):
        raise_openai_error(400, "Request body must be a JSON object", "invalid_request")

    model = request_data.get("model")
    if not model:
        raise_openai_error(400, "Missing required field: model", "missing_model")

    messages = build_responses_messages(request_data)
    history, current_input, prompt_tokens = prepare_chat_messages(messages)
    tools = request_data.get("tools", [])
    config = get_config_for_model(model, get_thinking_budget_from_responses_request(request_data), tools)

    response_id = f"resp_{uuid.uuid4().hex}"
    message_id = f"msg_{uuid.uuid4().hex}"
    created_at = int(time.time())
    stream = bool(request_data.get("stream"))

    if stream:
        async def generate_stream():
            full_content = ""
            full_reasoning = ""
            initial_response = build_responses_payload(
                request_data=request_data,
                response_id=response_id,
                output_items=[],
                output_text="",
                usage_text="",
                reasoning_text="",
                prompt_tokens=prompt_tokens,
                created_at=created_at,
                status="in_progress",
            )

            yield sse_event("response.created", {
                "type": "response.created",
                "response": initial_response,
            })
            yield sse_event("response.in_progress", {
                "type": "response.in_progress",
                "response": initial_response,
            })

            try:
                async for chunk in stream_backend_chat(current_input, history, config):
                    content = chunk.get("content", "")
                    reasoning = chunk.get("reasoning_content", "")
                    full_content += content
                    full_reasoning += reasoning
            except RuntimeError as e:
                yield sse_event("error", {
                    "type": "error",
                    "error": {
                        "message": str(e),
                        "type": "api_error",
                        "code": "upstream_chat_failed",
                    },
                })
                return

            parsed_output = parse_assistant_output(full_content)
            output_items = parsed_output["items"]
            stored_assistant_content = parsed_output["history_text"] or full_content
            output_text = parsed_output["output_text"]
            usage_text = full_content

            for index, completed_item in enumerate(output_items):
                item_type = completed_item.get("type")
                if item_type == "custom_tool_call":
                    in_progress_item = build_responses_custom_tool_call_message(
                        completed_item["id"],
                        completed_item["name"],
                        "",
                        completed_item["call_id"],
                        "in_progress",
                    )
                    yield sse_event("response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": index,
                        "item": in_progress_item,
                    })
                    tool_input = completed_item["input"]
                    if tool_input:
                        yield sse_event("response.custom_tool_call.input.delta", {
                            "type": "response.custom_tool_call.input.delta",
                            "item_id": completed_item["id"],
                            "output_index": index,
                            "call_id": completed_item["call_id"],
                            "delta": tool_input,
                        })
                    yield sse_event("response.custom_tool_call.input.done", {
                        "type": "response.custom_tool_call.input.done",
                        "item_id": completed_item["id"],
                        "output_index": index,
                        "call_id": completed_item["call_id"],
                        "input": tool_input,
                    })
                elif item_type == "function_call":
                    in_progress_item = build_responses_function_call_message(
                        completed_item["id"],
                        completed_item["name"],
                        "",
                        completed_item["call_id"],
                        "in_progress",
                    )
                    yield sse_event("response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": index,
                        "item": in_progress_item,
                    })
                    arguments_text = completed_item["arguments"]
                    if arguments_text:
                        yield sse_event("response.function_call.arguments.delta", {
                            "type": "response.function_call.arguments.delta",
                            "item_id": completed_item["id"],
                            "output_index": index,
                            "call_id": completed_item["call_id"],
                            "delta": arguments_text,
                        })
                    yield sse_event("response.function_call.arguments.done", {
                        "type": "response.function_call.arguments.done",
                        "item_id": completed_item["id"],
                        "output_index": index,
                        "call_id": completed_item["call_id"],
                        "arguments": arguments_text,
                    })
                else:
                    text_parts = [
                        content_part.get("text", "")
                        for content_part in (completed_item.get("content") or [])
                        if content_part.get("type") == "output_text"
                    ]
                    item_text = "".join(text_parts)
                    yield sse_event("response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": index,
                        "item": build_responses_output_message(completed_item["id"], "", "in_progress"),
                    })
                    yield sse_event("response.content_part.added", {
                        "type": "response.content_part.added",
                        "item_id": completed_item["id"],
                        "output_index": index,
                        "content_index": 0,
                        "part": {
                            "type": "output_text",
                            "text": "",
                            "annotations": [],
                        },
                    })
                    if item_text:
                        yield sse_event("response.output_text.delta", {
                            "type": "response.output_text.delta",
                            "item_id": completed_item["id"],
                            "output_index": index,
                            "content_index": 0,
                            "delta": item_text,
                        })
                    yield sse_event("response.output_text.done", {
                        "type": "response.output_text.done",
                        "item_id": completed_item["id"],
                        "output_index": index,
                        "content_index": 0,
                        "text": item_text,
                    })
                    yield sse_event("response.content_part.done", {
                        "type": "response.content_part.done",
                        "item_id": completed_item["id"],
                        "output_index": index,
                        "content_index": 0,
                        "part": {
                            "type": "output_text",
                            "text": item_text,
                            "annotations": [],
                        },
                    })

                yield sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": index,
                    "item": completed_item,
                })

            update_stats(prompt_tokens, len(full_content) + len(full_reasoning))

            final_response = build_responses_payload(
                request_data=request_data,
                response_id=response_id,
                output_items=output_items,
                output_text=output_text,
                usage_text=usage_text,
                reasoning_text=full_reasoning,
                prompt_tokens=prompt_tokens,
                created_at=created_at,
                status="completed",
            )
            conversation_messages = messages + [{
                "role": "assistant",
                "content": stored_assistant_content,
            }]
            maybe_store_response(response_id, conversation_messages, final_response)

            yield sse_event("response.completed", {
                "type": "response.completed",
                "response": final_response,
            })

        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    full_content, full_reasoning = run_backend_chat(current_input, history, config)
    update_stats(prompt_tokens, len(full_content) + len(full_reasoning))
    parsed_output = parse_assistant_output(full_content)
    output_items = parsed_output["items"]
    output_text = parsed_output["output_text"]
    stored_assistant_content = parsed_output["history_text"] or full_content

    response_payload = build_responses_payload(
        request_data=request_data,
        response_id=response_id,
        output_items=output_items,
        output_text=output_text,
        usage_text=full_content,
        reasoning_text=full_reasoning,
        prompt_tokens=prompt_tokens,
        created_at=created_at,
        status="completed",
    )
    conversation_messages = messages + [{
        "role": "assistant",
        "content": stored_assistant_content,
    }]
    maybe_store_response(response_id, conversation_messages, response_payload)

    return response_payload


@app.get("/v1/responses/{response_id}")
async def get_response(response_id: str):
    with response_store_lock:
        stored = response_store.get(response_id)

    if not stored:
        raise_openai_error(404, f"Response {response_id} not found", "response_not_found")

    return deepcopy(stored["response"])


if __name__ == "__main__":
    print(f"\n{'='*50}")
    print("SDU DeepSeek API Server")
    print(f"{'='*50}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
