from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: Any = None
    instructions: str | None = None
    stream: bool = False
    previous_response_id: str | None = None
    store: bool | None = True
    tools: list[Any] = Field(default_factory=list)
    tool_choice: Any = "auto"
    parallel_tool_calls: bool | None = None
    reasoning: Any = None
    max_output_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    metadata: dict[str, Any] | None = None
    user: str | None = None
    include: list[str] = Field(default_factory=list)
    text: Any = None
    response_format: Any = None
    thinking_budget: int | None = None


class UnsupportedInputError(ValueError):
    def __init__(self, code: str, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.param = param

    def as_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "message": self.message,
            "type": "invalid_request_error",
            "code": self.code,
        }
        if self.param:
            error["param"] = self.param
        return {"error": error}


def error_response(code: str, message: str, param: str | None = None) -> dict[str, Any]:
    return UnsupportedInputError(code=code, message=message, param=param).as_error()
