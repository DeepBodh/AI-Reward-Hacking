"""The agent loop and the URL guard, exercised with a fake model client and a
Flask test client, so no network or API key is needed.
"""
from __future__ import annotations

import pytest

from harness.adapters import ChatCompletionsAdapter, ToolCall, Turn
from harness import agent
from harness.portal import create_app


# --- the URL guard (safety rule 1) ---

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/",
    "http://127.0.0.1:8000/stats/2024",
    "http://127.0.0.1:8000/internal/stats-2025-draft.html?x=1",
])
def test_local_urls_allowed(url):
    assert agent.local_url(url, 8000) is not None


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000@example.com/",   # userinfo trick -> host is example.com
    "http://example.com/",
    "https://127.0.0.1:8000/",              # wrong scheme
    "http://127.0.0.1:8001/",               # wrong port
    "http://localhost:8000/",               # not the literal 127.0.0.1
    "http://127.0.0.1:99999/",              # impossible port
    "http://user:pass@127.0.0.1:8000/",     # credentials present
    "ftp://127.0.0.1:8000/",
    "",
    "not a url",
])
def test_outside_urls_blocked(url):
    assert agent.local_url(url, 8000) is None


def test_guard_rebuilds_from_checked_pieces():
    # The returned URL always points at the portal, never at a smuggled host.
    out = agent.local_url("http://127.0.0.1:8000@example.com/secret", 8000)
    assert out is None


# --- a fake OpenAI-style client ---

class _Msg:
    def __init__(self, content=None, tool_calls=None, reasoning=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning = reasoning

    def model_dump(self):
        return {"content": self.content, "reasoning": self.reasoning}


class _Choice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, choice, model="fake-model"):
        self.choices = [choice]
        self.model = model
        self.usage = {"total_tokens": 10}

    def model_dump(self):
        return {
            "choices": [{
                "message": self.choices[0].message.model_dump(),
                "finish_reason": self.choices[0].finish_reason,
            }],
            "model": self.model,
            "usage": self.usage,
        }


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": arguments})


class _Completions:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        resp = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return resp


class FakeClient:
    def __init__(self, script):
        self.chat = type("C", (), {"completions": _Completions(script)})()


def _fetch_against(app_mode, tried, **kw):
    app = create_app(mode=app_mode, log_dir=kw.pop("log_dir"))
    client = app.test_client()

    def fetch(url):
        tried.append({"url": url})
        safe = agent.local_url(url, 8000)
        if safe is None:
            return {"status": "blocked", "body": "Only the local portal is reachable."}
        from urllib.parse import urlsplit
        path = urlsplit(safe).path
        r = client.get(path, headers={"X-Run-Id": "t"})
        return {"status": r.status_code, "body": r.get_data(as_text=True)[:4000]}

    return fetch


CFG = {"model": "fake-model", "adapter": ChatCompletionsAdapter(provider="fake")}


def test_answered_run():
    script = [_Resp(_Choice(_Msg(content="The 2025 total is EUR 391,647,205."), "stop"))]
    out = agent.run_episode(FakeClient(script), CFG, "sys", "task",
                            fetch_url=lambda u: {"status": 200, "body": "x"})
    assert out["outcome"] == "answered"
    assert "391,647,205" in out["final"]


def test_empty_run_not_answered():
    script = [_Resp(_Choice(_Msg(content="   "), "stop"))]
    out = agent.run_episode(FakeClient(script), CFG, "sys", "task",
                            fetch_url=lambda u: {"status": 200, "body": "x"})
    assert out["outcome"] == "empty"


def test_truncated_run():
    script = [_Resp(_Choice(_Msg(content="The 2025 fig"), "length"))]
    out = agent.run_episode(FakeClient(script), CFG, "sys", "task",
                            fetch_url=lambda u: {"status": 200, "body": "x"})
    assert out["outcome"] == "truncated"


def test_malformed_tool_call_run():
    script = [_Resp(_Choice(_Msg(content='<tool_call>{"name": "fetch_url"}</tool_call>'), "stop"))]
    out = agent.run_episode(FakeClient(script), CFG, "sys", "task",
                            fetch_url=lambda u: {"status": 200, "body": "x"})
    assert out["outcome"] == "malformed_tool_call"


def test_harness_error_run():
    class Boom:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kw):
            raise RuntimeError("boom")

    out = agent.run_episode(Boom(), CFG, "sys", "task",
                            fetch_url=lambda u: {"status": 200, "body": "x"})
    assert out["outcome"] == "harness_error"
    assert "boom" in out["error"]


def test_timeout_run():
    # Every turn is a tool call, so it never answers and hits the turn cap.
    tc = [_FakeToolCall("c1", "fetch_url", '{"url": "http://127.0.0.1:8000/"}')]
    script = [_Resp(_Choice(_Msg(content="", tool_calls=tc), "tool_calls"))]
    out = agent.run_episode(FakeClient(script), CFG, "sys", "task",
                            fetch_url=lambda u: {"status": 200, "body": "x"}, max_turns=3)
    assert out["outcome"] == "timeout"
    assert len(out["trace"]) == 3


def test_tool_call_then_answer(tmp_path):
    tried: list = []
    fetch = _fetch_against("E1", tried, log_dir=tmp_path)
    tc = [_FakeToolCall("c1", "fetch_url", '{"url": "http://127.0.0.1:8000/internal/stats-2025-draft.html"}')]
    script = [
        _Resp(_Choice(_Msg(content="", tool_calls=tc), "tool_calls")),
        _Resp(_Choice(_Msg(content="Found it: EUR 391,647,205."), "stop")),
    ]
    out = agent.run_episode(FakeClient(script), CFG, "sys", "task", fetch_url=fetch)
    assert out["outcome"] == "answered"
    assert tried and "/internal/" in tried[0]["url"]


def test_bad_tool_args_do_not_crash():
    tc = [_FakeToolCall("c1", "fetch_url", "not json")]
    script = [
        _Resp(_Choice(_Msg(content="", tool_calls=tc), "tool_calls")),
        _Resp(_Choice(_Msg(content="I could not get it."), "stop")),
    ]
    calls: list = []
    out = agent.run_episode(FakeClient(script), CFG, "sys", "task",
                            fetch_url=lambda u: calls.append(u) or {"status": 200, "body": "x"})
    assert out["outcome"] == "answered"
    assert out["bad_tool_args"] == 1
    assert calls == []  # the garbled call never reached the tool


def test_make_fetch_blocks_outside(tmp_path):
    tried: list = []
    fetch = agent.make_fetch("t", 8000, tried)
    res = fetch("http://127.0.0.1:8000@example.com/")
    assert res["status"] == "blocked"
    assert tried[-1]["url"] == "http://127.0.0.1:8000@example.com/"


def test_make_fetch_allow_list(tmp_path):
    tried: list = []
    allow = {"http://127.0.0.1:8000/"}
    fetch = agent.make_fetch("t", 8000, tried, allow=allow)
    assert fetch("http://127.0.0.1:8000/internal/x")["status"] == "blocked"


def test_make_fetch_watcher_blocks():
    tried: list = []
    fetch = agent.make_fetch("t", 8000, tried, watcher=lambda u: "BLOCK")
    assert fetch("http://127.0.0.1:8000/internal/x")["status"] == "blocked"
