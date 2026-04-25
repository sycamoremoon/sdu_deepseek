import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SDU_DEEPSEEK_SKIP_LOGIN", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from responses_store import response_store  # noqa: E402


@pytest.fixture(autouse=True)
def clear_response_store():
    response_store.clear()
    yield
    response_store.clear()


@pytest.fixture()
def client(monkeypatch):
    def fake_chat(content, history, config):
        yield {"content": f"Echo: {content}", "reasoning_content": ""}

    monkeypatch.setattr(main.sduwrap, "chat", fake_chat)
    return TestClient(main.app)


@pytest.fixture()
def mock_sdu(monkeypatch):
    def apply(chunks):
        def fake_chat(content, history, config):
            for chunk in chunks:
                yield chunk

        monkeypatch.setattr(main.sduwrap, "chat", fake_chat)

    return apply
