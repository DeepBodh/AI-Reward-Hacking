"""The Responses and Anthropic connectors normalise to the same Turn shape."""
from __future__ import annotations

from harness.adapters import AnthropicAdapter, ChatCompletionsAdapter, ResponsesAdapter


# --- chat completions: reasoning field is picked up ---

class _CMsg:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.__dict__.setdefault("content", None)
        self.__dict__.setdefault("tool_calls", None)

    def model_dump(self):
        return dict(self.__dict__)


class _CResp:
    def __init__(self, message, finish, model="m"):
        self.choices = [type("Ch", (), {"message": message, "finish_reason": finish})]
        self.model = model
        self.usage = {"total_tokens": 5}

    def model_dump(self):
        return {"choices": [{"message": self.choices[0].message.model_dump(),
                             "finish_reason": self.choices[0].finish_reason}],
                "model": self.model, "usage": self.usage}


class _ChatClient:
    def __init__(self, resp):
        self.chat = type("C", (), {"completions": type("X", (), {"create": lambda s, **k: resp})()})()


def test_chat_picks_up_reasoning_content():
    msg = _CMsg(content="answer", reasoning_content="step by step")
    adapter = ChatCompletionsAdapter()
    turn = adapter.call(_ChatClient(_CResp(msg, "stop")), {"model": "m"}, [], [])
    assert turn.text == "answer"
    assert turn.thinking == "step by step"
    assert turn.finish_reason == "stop"
    assert turn.served_model == "m"


def test_chat_provider_pin_passed_through():
    captured = {}

    class Cli:
        def __init__(self):
            self.chat = type("C", (), {"completions": type("X", (), {
                "create": lambda s, **k: (captured.update(k) or _CResp(_CMsg(content="a"), "stop"))
            })()})()

    adapter = ChatCompletionsAdapter()
    cfg = {"model": "m", "provider_pin": {"order": ["prov"], "allow_fallbacks": False}}
    adapter.call(Cli(), cfg, [], [])
    assert captured["extra_body"]["provider"]["allow_fallbacks"] is False


# --- responses API: reasoning + tool call + truncation ---

class _RespObj:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


class _RespClient:
    def __init__(self, data):
        self.responses = type("R", (), {"create": lambda s, **k: _RespObj(data)})()


def test_responses_reasoning_and_toolcall():
    data = {
        "model": "gpt-5.6-luna",
        "status": "completed",
        "usage": {"output_tokens": 3},
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking..."}]},
            {"type": "function_call", "call_id": "fc1", "name": "fetch_url",
             "arguments": '{"url": "http://127.0.0.1:8000/"}'},
        ],
    }
    adapter = ResponsesAdapter()
    turn = adapter.call(_RespClient(data), {"model": "gpt-5.6-luna"}, [], [])
    assert turn.thinking == "thinking..."
    assert turn.finish_reason == "tool_calls"
    assert turn.tool_calls[0].name == "fetch_url"
    # The reasoning items are stashed so the next step can pass them back.
    entry = adapter.history_entry(turn)
    assert "_responses_output" in entry
    assert entry["_responses_output"][0]["type"] == "reasoning"


def test_responses_truncation_detected():
    data = {
        "model": "gpt-5.6-luna",
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "cut"}]}],
    }
    turn = ResponsesAdapter().call(_RespClient(data), {"model": "gpt-5.6-luna"}, [], [])
    assert turn.finish_reason == "length"


def test_responses_tool_result_shape():
    from harness.adapters import ToolCall
    out = ResponsesAdapter().tool_result(ToolCall("fc1", "fetch_url", "{}"), '{"status":200}')
    assert out == {"type": "function_call_output", "call_id": "fc1", "output": '{"status":200}'}


# --- anthropic ---

class _AnthResp:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


class _AnthClient:
    def __init__(self, data):
        self.messages = type("M", (), {"create": lambda s, **k: _AnthResp(data)})()


def test_anthropic_normalises():
    data = {
        "model": "claude-haiku",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 4, "output_tokens": 2},
        "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "tu1", "name": "fetch_url",
             "input": {"url": "http://127.0.0.1:8000/"}},
        ],
    }
    adapter = AnthropicAdapter()
    turn = adapter.call(_AnthClient(data), {"model": "claude-haiku"}, [
        {"role": "system", "content": "s"}, {"role": "user", "content": "u"}], [])
    assert turn.thinking == "hmm"
    assert turn.text == "let me check"
    assert turn.finish_reason == "tool_calls"
    assert turn.tool_calls[0].id == "tu1"
    res = adapter.tool_result(turn.tool_calls[0], '{"status":200}')
    assert res["role"] == "user"
    assert res["content"][0]["type"] == "tool_result"


def test_looks_like_text_tool_call():
    a = ChatCompletionsAdapter()
    assert a.looks_like_text_tool_call('<tool_call>{"name":"fetch_url"}</tool_call>')
    assert a.looks_like_text_tool_call("I'll call fetch_url(url=...)")
    assert not a.looks_like_text_tool_call("The 2025 total is EUR 391,647,205.")
