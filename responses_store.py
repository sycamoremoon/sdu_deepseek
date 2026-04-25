from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoredResponse:
    response: dict[str, Any]
    conversation: list[dict[str, Any]] = field(default_factory=list)
    input_items: list[dict[str, Any]] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))
    persistent: bool = True


class ResponseStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._responses: dict[str, StoredResponse] = {}

    def put(
        self,
        response: dict[str, Any],
        conversation: list[dict[str, Any]],
        input_items: list[dict[str, Any]] | None = None,
        persistent: bool = True,
    ) -> None:
        response_id = response["id"]
        stored = StoredResponse(
            response=deepcopy(response),
            conversation=deepcopy(conversation),
            input_items=deepcopy(input_items or []),
            persistent=persistent,
        )
        with self._lock:
            self._responses[response_id] = stored

    def get(self, response_id: str) -> StoredResponse | None:
        with self._lock:
            stored = self._responses.get(response_id)
            return deepcopy(stored) if stored else None

    def get_response(self, response_id: str) -> dict[str, Any] | None:
        stored = self.get(response_id)
        return stored.response if stored else None

    def delete(self, response_id: str) -> bool:
        with self._lock:
            return self._responses.pop(response_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._responses.clear()


response_store = ResponseStore()
