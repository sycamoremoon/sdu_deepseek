import os
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import sduwrap
from sduwrap import ChatConfig


@pytest.mark.skipif(os.environ.get("RUN_LIVE_SDU_TESTS") != "1", reason="Live SDU tests are opt-in.")
def test_live_sdu_text_smoke():
    cookies_path = Path("cookies.json")
    if cookies_path.exists() and not sduwrap.cookies:
        sduwrap.cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
    chunks = list(sduwrap.chat("请只回答 OK", [], ChatConfig()))
    combined = "".join(chunk.get("content", "") for chunk in chunks)
    assert combined


@pytest.mark.skipif(os.environ.get("RUN_LIVE_SDU_TESTS") != "1", reason="Live SDU tests are opt-in.")
@pytest.mark.parametrize("model_name", ["deepseek-ai/DeepSeek-V3.2-think", "deepseek-ai/DeepSeek-V4"])
def test_live_responses_think_models_smoke(model_name):
    cookies_path = Path("cookies.json")
    if cookies_path.exists() and not sduwrap.cookies:
        sduwrap.cookies = json.loads(cookies_path.read_text(encoding="utf-8"))

    client = TestClient(main.app)
    response = client.post(
        "/v1/responses",
        json={
            "model": model_name,
            "input": "请只回答 OK",
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == model_name
    assert isinstance(body["output_text"], str)
    assert body["output_text"].strip()
