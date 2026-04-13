import requests
import json
import uuid
import re

cookies = {}

url = "https://aiassist.sdu.edu.cn/site/ai/compose_chat"

MODEL_CONFIG = {
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
        self.tools = []

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

    def process(self, chunk):
        self.buffer += chunk
        reasoning_content = ""
        content = ""
        
        while True:
            if not self.in_think:
                think_start = self.buffer.find('<think\\>')
                if think_start != -1:
                    content += self.buffer[:think_start]
                    self.buffer = self.buffer[think_start + 8:]
                    self.in_think = True
                else:
                    safe_pos = len(self.buffer)
                    for i in range(len(self.buffer)):
                        if self.buffer[i] == '<' and i + 7 <= len(self.buffer):
                            if self.buffer[i:i+7] == '<think\\>':
                                safe_pos = i
                                break
                    if safe_pos > 0:
                        content += self.buffer[:safe_pos]
                        self.buffer = self.buffer[safe_pos:]
                    break
            else:
                think_end = self.buffer.find('</think\\>')
                if think_end != -1:
                    reasoning_content += self.buffer[:think_end]
                    self.buffer = self.buffer[think_end + 9:]
                    self.in_think = False
                else:
                    safe_pos = len(self.buffer)
                    for i in range(len(self.buffer)):
                        if self.buffer[i] == '<' and i + 9 <= len(self.buffer):
                            if self.buffer[i:i+9] == '</think\\>':
                                safe_pos = i
                                break
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
    response = requests.post(url, data=form_data, cookies=cookies, stream=True)
    
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
