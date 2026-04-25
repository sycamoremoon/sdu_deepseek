import sduwrap


def test_chat_stream_parses_plain_think_tags():
    stream = sduwrap.ChatStream()
    content, reasoning = stream.process("<think>reason</think>answer")
    tail_content, tail_reasoning = stream.finalize()
    assert content + tail_content == "answer"
    assert reasoning + tail_reasoning == "reason"


def test_chat_stream_handles_split_think_tags():
    stream = sduwrap.ChatStream()
    chunks = ["<thi", "nk>rea", "son</th", "ink><exec_command>{\"cmd\":\"pwd\"}</exec_command>"]
    visible = []
    hidden = []
    for chunk in chunks:
        content, reasoning = stream.process(chunk)
        visible.append(content)
        hidden.append(reasoning)
    tail_content, tail_reasoning = stream.finalize()
    visible.append(tail_content)
    hidden.append(tail_reasoning)
    assert "".join(hidden) == "reason"
    assert "".join(visible) == '<exec_command>{"cmd":"pwd"}</exec_command>'
