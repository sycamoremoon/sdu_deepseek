import requests
import json
import uuid
import re

cookies = {}

url = "https://aiassist.sdu.edu.cn/site/ai/compose_chat"

THINK_OPEN_TAGS = ("<think>", "<think\\>")
THINK_CLOSE_TAGS = ("</think>", "</think\\>")

MODEL_CONFIG = {
    "DeepSeek-V4": {"compose_id": 73},
    "DeepSeek-V3.2-think": {"compose_id": 73},
    "DeepSeek-V3.2": {"compose_id": 73},
    "DeepSeek-R1": {"compose_id": 73},
    "DeepSeek-V3": {"compose_id": 73},
    "Qwen3-235B-A22B-Instruct": {"compose_id": 72},
    "Qwen3-235B-A22B-Thinking": {"compose_id": 72},
}


class ChatSession:
    def __init__(self):
        self.role = "user"
        self.content = ""


class ChatConfig:
    def __init__(self):
        self.compose_id = 73
        self.auth_tag = "本科生"
        self.deep_search = 2
        self.internet_search = 2
        self.model_name = "DeepSeek-V3.2-think"
        self.thinking_budget = 1000

    def set_model(self, model_name: str):
        if model_name in MODEL_CONFIG:
            self.model_name = model_name
            self.compose_id = MODEL_CONFIG[model_name]["compose_id"]
        return self


def history_to_form_data(history):
    form_data = {}
    idx = 0

    for chat_session in history:
        if chat_session.role == "system":
            form_data[f"history[{idx}][role]"] = "user"
            form_data[f"history[{idx}][content]"] = chat_session.content
            idx += 1
            form_data[f"history[{idx}][role]"] = "assistant"
            form_data[f"history[{idx}][content]"] = "我知道了"
            idx += 1
        elif chat_session.role == "user":
            form_data[f"history[{idx}][role]"] = "user"
            form_data[f"history[{idx}][content]"] = chat_session.content
            idx += 1
            form_data[f"history[{idx}][role]"] = "assistant"
            form_data[f"history[{idx}][content]"] = ""
            idx += 1
        elif chat_session.role == "assistant":
            if idx > 0 and form_data.get(f"history[{idx-1}][role]") == "assistant":
                form_data[f"history[{idx-1}][content]"] = chat_session.content
            else:
                form_data[f"history[{idx}][role]"] = "assistant"
                form_data[f"history[{idx}][content]"] = chat_session.content
                idx += 1

    return form_data


def make_chat_request(content, history, config):
    form_data = {}
    form_data["content"] = content
    form_data.update(history_to_form_data(history))
    form_data["compose_id"] = config.compose_id
    form_data["auth_tag"] = config.auth_tag
    form_data["deep_search"] = config.deep_search
    form_data["internet_search"] = config.internet_search
    form_data["model_name"] = config.model_name
    form_data["thinking_budget"] = config.thinking_budget
    form_data["chat_only_id"] = uuid.uuid4().hex

    return form_data


class ChatStream:
    def __init__(self):
        self.buffer = ""
        self.in_think = False

    @staticmethod
    def _find_first_tag(buffer, tags):
        found = [(buffer.find(tag), tag) for tag in tags if buffer.find(tag) != -1]
        if not found:
            return -1, None
        return min(found, key=lambda item: item[0])

    @staticmethod
    def _safe_flush_pos(buffer, tags):
        longest_partial = 0
        max_partial = max((len(tag) for tag in tags), default=0) - 1
        max_partial = min(len(buffer), max_partial)
        for partial_len in range(1, max_partial + 1):
            suffix = buffer[-partial_len:]
            if any(tag.startswith(suffix) for tag in tags):
                longest_partial = partial_len
        return len(buffer) - longest_partial

    def process(self, chunk):
        self.buffer += chunk
        reasoning_content = ""
        content = ""
        
        while True:
            if not self.in_think:
                think_start, think_tag = self._find_first_tag(self.buffer, THINK_OPEN_TAGS)
                if think_start != -1 and think_tag is not None:
                    content += self.buffer[:think_start]
                    self.buffer = self.buffer[think_start + len(think_tag):]
                    self.in_think = True
                else:
                    safe_pos = self._safe_flush_pos(self.buffer, THINK_OPEN_TAGS)
                    if safe_pos > 0:
                        content += self.buffer[:safe_pos]
                        self.buffer = self.buffer[safe_pos:]
                    break
            else:
                think_end, think_tag = self._find_first_tag(self.buffer, THINK_CLOSE_TAGS)
                if think_end != -1 and think_tag is not None:
                    reasoning_content += self.buffer[:think_end]
                    self.buffer = self.buffer[think_end + len(think_tag):]
                    self.in_think = False
                else:
                    safe_pos = self._safe_flush_pos(self.buffer, THINK_CLOSE_TAGS)
                    if safe_pos > 0:
                        reasoning_content += self.buffer[:safe_pos]
                        self.buffer = self.buffer[safe_pos:]
                    break
        
        return content, reasoning_content

    def finalize(self):
        content = ""
        reasoning_content = ""
        
        if self.in_think:
            reasoning_content = self.buffer
        else:
            content = self.buffer
        
        self.buffer = ""
        return content, reasoning_content


def chat(content, history, config):
    form_data = make_chat_request(content, history, config)
    response = requests.post(url, data=form_data, cookies=cookies, stream=True, timeout=(10, 660))
    
    if response.status_code != 200:
        print(f"[SDU API] Error: HTTP {response.status_code}")
        print(f"[SDU API] Response: {response.text[:500]}")
        yield {"content": f"API Error: HTTP {response.status_code}", "reasoning_content": ""}
        return
    
    stream = ChatStream()
    
    for line in response.iter_lines():
        if line:
            text = line.decode('utf-8')
            if text.startswith('data: '):
                text = text[6:]
                try:
                    json_data = json.loads(text)
                    if "d" in json_data and "answer" in json_data["d"]:
                        chunk = json_data["d"]["answer"]
                        c, r = stream.process(chunk)
                        if c or r:
                            yield {
                                "content": c,
                                "reasoning_content": r
                            }
                except json.JSONDecodeError:
                    pass
    
    c, r = stream.finalize()
    if c or r:
        yield {
            "content": c,
            "reasoning_content": r
        }


if __name__ == "__main__":
    for i in chat("如何评价山东大学", [], ChatConfig()):
        if i["reasoning_content"]:
            print(f"[Think]: {i['reasoning_content']}", end="")
        print(i["content"], end="")
