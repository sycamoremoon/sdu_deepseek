import os
import json
from pathlib import Path

import pytest

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
