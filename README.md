# 注意
> 本工具仅供技术研究，使用者应于24小时内删除从山东大学DeepSeek获取的非公开数据。开发者不对任何学术诚信审查问题负责，请优先联系信息化办公室申请官方API权限。
> 本程序不保证您的信息安全，使用者应自行承担风险。请勿将本程序用于非法用途，否则后果自负。
> 开发者不对使用本程序导致的任何问题负责。
> 请勿滥用！！！

# 鸣谢

感谢山东大学数智化支撑研究院（信息办）为山大学子提供的免费DeepSeek服务。

感谢@zeroHYH同学为本程序提供山大统一身份认证的登录支持，使得免于使用网页填表。

# 程序开发宗旨

本程序的目的是为了方便使用DeepSeek的同学，提供一个简单的API接口，方便调用。
网页版没法嵌入到诸如翻译工具或者集成开发环境中，因此开发了这个程序。为了方便同学
能够因地制宜地使用DeepSeek。

# 使用方法

## 使用打包好的程序

直接从 **右边** 的 `Releases` 下载最新版本的程序，解压后运行即可。

在登陆成功后会显示类似的信息：
```bash
INFO:     Started server process [45608]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

第一次启动程序时，会要求输入学号和密码（用于统一身份认证的），登录后会自动保存登录状态（程序所在的目录下会生成cookies.json），下次启动程序时会自动登录。

如果长时间未使用，登录状态可能会过期，此时请手动删除cookies.json文件，然后重新启动程序。

程序将在 `http://localhost:8000` 上运行。

## API 接口

本程序提供 OpenAI 兼容的 API 接口：

- `GET /v1/models` - 获取模型列表
- `GET /v1/models/{model_id}` - 获取模型信息
- `POST /v1/chat/completions` - 传统聊天完成接口
- `POST /v1/responses` - Responses API 兼容入口，可供新版 Codex / Agent 客户端接入
- `GET /v1/responses/{response_id}` - 获取已缓存的响应对象

## 支持的模型

| 模型 ID | 说明 |
|---------|------|
| `deepseek-ai/DeepSeek-V3.2` | DeepSeek V3.2 |
| `deepseek-ai/DeepSeek-R1` | DeepSeek R1 (深度思考) |
| `deepseek-ai/DeepSeek-V3` | DeepSeek V3 |
| `deepseek-ai/DeepSeek-V3.2-think` | DeepSeek V3.2 + 深度思考 |
| `Qwen/Qwen3-235B-A22B-Instruct` | Qwen3 235B |
| `Qwen/Qwen3-235B-A22B-Thinking` | Qwen3 235B + 深度思考 |
| `gpt-5-codex` | Codex 兼容别名，实际映射到 DeepSeek-V3.2-think |
| `gpt-5.1-codex` | Codex 兼容别名，实际映射到 DeepSeek-V3.2-think |
| `gpt-5.2-codex` | Codex 兼容别名，实际映射到 DeepSeek-V3.2-think |
| `gpt-5.3-codex` | Codex 兼容别名，实际映射到 DeepSeek-V3.2-think |
| `codex-mini-latest` | Codex 兼容别名，实际映射到 DeepSeek-V3.2-think |

## 请求示例

### 非流式请求

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-V3.2",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

### 流式请求

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-V3.2",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### Responses API 请求

```bash
curl -X POST http://localhost:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.3-codex",
    "instructions": "You are a coding assistant.",
    "input": "请帮我分析这个项目的入口文件",
    "stream": false
  }'
```

### Responses API 流式请求

```bash
curl -N -X POST http://localhost:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.3-codex",
    "input": "你好",
    "stream": true
  }'
```

## 特殊参数

- `thinking_budget`: 思考预算，默认 1000，仅对支持深度思考的模型有效

```json
{
  "model": "deepseek-ai/DeepSeek-R1",
  "messages": [{"role": "user", "content": "请解释量子力学"}],
  "thinking_budget": 2000,
  "stream": true
}
```

## 推理内容 (Reasoning Content)

对于支持深度思考的模型（如 DeepSeek-R1），响应中会包含 `reasoning_content` 字段：

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "这是回答内容",
      "reasoning_content": "这是思考过程"
    }
  }]
}
```

## Responses API 兼容说明

- 代理层会把 `/v1/responses` 的 `input` / `instructions` / `previous_response_id` 映射为内部的 chat history，再转发给山大 DeepSeek 网页接口。
- 已实现 Responses 风格的 SSE 事件序列；普通文本响应会输出 `response.output_text.*`，工具调用会输出 `response.function_call.arguments.*` 或 `response.custom_tool_call.input.*`。
- 已支持内存态 `previous_response_id` 上下文续接；只在当前进程内有效，重启服务后会失效。
- `reasoning.effort` 会被转换为内部 `thinking_budget`。
- 若客户端请求的是 Codex 模型名，会被映射到 `DeepSeek-V3.2-think`。
- 当请求中包含 `tools` 时，代理层会自动把工具定义注入系统提示，并把模型返回的结构化 `<tool_call>...</tool_call>` 文本重新映射为 Responses 的工具调用输出项。

## 当前限制

- 这是协议兼容层，不是真正的 Responses 原生后端。
- 工具调用能力依赖提示工程和文本解析，不是网页后端原生返回 `tool_calls`；如果模型没有遵守 `<tool_call>` 输出格式，代理层会把结果当普通文本处理。
- `previous_response_id` 的缓存目前保存在内存中，不会持久化到磁盘。

## 从源码运行

您需要这样做，说明您大概率不需要本教程指导
