"""Connectors: one per AI family, each returning the same normalised Turn.

The agent loop (harness/agent.py) never talks to a provider directly. It calls
one of these adapters, which does the provider-specific call and hands back a
Turn with the same fields no matter who answered:

    text           the model's normal output text (its answer), or None
    thinking        the model's hidden reasoning, as a string, or None
    tool_calls      list of ToolCall(id, name, arguments-as-JSON-string)
    finish_reason   normalised: "stop" | "length" | "tool_calls" | other
    usage           token counts as a plain dict
    served_model    the model id the provider says actually answered
    raw             the full raw response as a dict (silent-failure rule 6)

Adapters also keep the thinking the right way between steps (history_entry)
and format a tool result (tool_result).

Three adapters:
    ChatCompletionsAdapter  OpenAI-style /chat/completions. Covers OpenRouter,
                            local llama.cpp/MLX, and OpenAI chat models.
    ResponsesAdapter        OpenAI Responses API, for reasoning models (e.g.
                            GPT-6 Luna) whose thinking Chat Completions drops.
                            Pass the first step's reasoning items back each step.
    AnthropicAdapter        Anthropic Messages API, for the optional Claude model.

The adapters depend only on a client object being passed in (the openai or
anthropic SDK client), so they are easy to exercise with a fake client in tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as the model produced it


@dataclass
class Turn:
    text: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    served_model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# Patterns that mean a model wrote a tool call as plain text instead of making a
# real tool call. A known problem with some open models' chat formats.
_TEXT_TOOLCALL_MARKERS = (
    "<tool_call>",
    "<|tool_call|>",
    "functions.fetch_url",
    '"name": "fetch_url"',
    '"name":"fetch_url"',
    "fetch_url(",
)


def _as_dict(obj: Any) -> dict:
    """Best-effort conversion of an SDK response object to a plain dict."""
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    if isinstance(obj, dict):
        return obj
    try:
        return json.loads(json.dumps(obj, default=lambda o: getattr(o, "__dict__", str(o))))
    except Exception:
        return {"repr": repr(obj)}


class BaseAdapter:
    #: provider label stored on each run (e.g. "openrouter", "openai", "anthropic")
    provider: str = "base"

    def looks_like_text_tool_call(self, text: str) -> bool:
        low = text.lower()
        return any(m.lower() in low for m in _TEXT_TOOLCALL_MARKERS)

    # Subclasses implement:
    def call(self, client, cfg, messages, tools) -> Turn:  # pragma: no cover
        raise NotImplementedError

    def history_entry(self, turn: Turn) -> dict:  # pragma: no cover
        raise NotImplementedError

    def tool_result(self, tool_call: ToolCall, content: str) -> dict:  # pragma: no cover
        raise NotImplementedError


class ChatCompletionsAdapter(BaseAdapter):
    """OpenAI-style chat completions: OpenRouter, local servers, OpenAI chat."""

    def __init__(self, provider: str = "openrouter"):
        self.provider = provider

    def call(self, client, cfg, messages, tools) -> Turn:
        kwargs: dict[str, Any] = dict(model=cfg["model"], messages=messages, tools=tools)
        kwargs.update(cfg.get("extra", {}))
        # OpenRouter provider pin + no fallbacks travels in extra_body.
        if cfg.get("provider_pin"):
            kwargs.setdefault("extra_body", {})["provider"] = cfg["provider_pin"]
        resp = client.chat.completions.create(**kwargs)
        raw = _as_dict(resp)
        choice = resp.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name,
                                       arguments=tc.function.arguments or "{}"))

        # Reasoning text appears under different keys depending on the provider;
        # inspect the raw JSON rather than trusting any single field.
        thinking = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)
        if thinking is None:
            mraw = raw.get("choices", [{}])[0].get("message", {})
            thinking = mraw.get("reasoning") or mraw.get("reasoning_content")

        return Turn(
            text=getattr(msg, "content", None),
            thinking=thinking,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=raw.get("usage", {}) or {},
            served_model=raw.get("model") or cfg["model"],
            raw=raw,
        )

    def history_entry(self, turn: Turn) -> dict:
        entry: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
        if turn.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in turn.tool_calls
            ]
        return entry

    def tool_result(self, tool_call: ToolCall, content: str) -> dict:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": content}


class ResponsesAdapter(BaseAdapter):
    """OpenAI Responses API, for reasoning models that need their thinking kept.

    Use this only if the check in plan section 6 shows Chat Completions drops
    Luna's thinking. The reasoning items returned by one step are passed back
    on the next, which Chat Completions cannot do.
    """

    provider = "openai"

    def call(self, client, cfg, messages, tools) -> Turn:
        # Responses tools are flat {type:function, name, description, parameters}.
        flat_tools = [
            {"type": "function", **t["function"]} if t.get("type") == "function" else t
            for t in tools
        ]
        kwargs: dict[str, Any] = dict(model=cfg["model"], input=messages, tools=flat_tools)
        kwargs.update(cfg.get("extra", {}))
        resp = client.responses.create(**kwargs)
        raw = _as_dict(resp)

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        reasoning_items: list[dict] = []

        for item in raw.get("output", []):
            itype = item.get("type")
            if itype == "reasoning":
                reasoning_items.append(item)
                for s in item.get("summary", []) or []:
                    if isinstance(s, dict) and s.get("text"):
                        thinking_parts.append(s["text"])
            elif itype == "message":
                for part in item.get("content", []) or []:
                    if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                        text_parts.append(part.get("text", ""))
            elif itype in ("function_call", "tool_call"):
                tool_calls.append(ToolCall(
                    id=item.get("call_id") or item.get("id") or "",
                    name=item.get("name", ""),
                    arguments=item.get("arguments") or "{}",
                ))

        finish = "tool_calls" if tool_calls else raw.get("status") or "stop"
        if raw.get("status") == "incomplete":
            reason = (raw.get("incomplete_details") or {}).get("reason")
            if reason == "max_output_tokens":
                finish = "length"

        turn = Turn(
            text="".join(text_parts) or None,
            thinking="\n".join(thinking_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=raw.get("usage", {}) or {},
            served_model=raw.get("model") or cfg["model"],
            raw=raw,
        )
        # Stash the reasoning items so history_entry can pass them back.
        turn.raw["_reasoning_items"] = reasoning_items
        turn.raw["_output_items"] = raw.get("output", [])
        return turn

    def history_entry(self, turn: Turn) -> dict:
        # The loop appends this; for Responses we hand back the whole output list
        # (reasoning items included) wrapped so agent.py can extend the input.
        return {"_responses_output": turn.raw.get("_output_items", [])}

    def tool_result(self, tool_call: ToolCall, content: str) -> dict:
        return {"type": "function_call_output", "call_id": tool_call.id, "output": content}


class AnthropicAdapter(BaseAdapter):
    """Anthropic Messages API, for the optional Claude model."""

    provider = "anthropic"

    def call(self, client, cfg, messages, tools) -> Turn:
        system = ""
        conv: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                conv.append(m)
        a_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]
        kwargs: dict[str, Any] = dict(
            model=cfg["model"],
            system=system,
            messages=conv,
            tools=a_tools,
            max_tokens=cfg.get("max_tokens", 4096),
        )
        kwargs.update(cfg.get("extra", {}))
        resp = client.messages.create(**kwargs)
        raw = _as_dict(resp)

        text_parts, thinking_parts, tool_calls = [], [], []
        for block in raw.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "thinking":
                thinking_parts.append(block.get("thinking", ""))
            elif btype == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=json.dumps(block.get("input", {})),
                ))

        stop = raw.get("stop_reason")
        finish = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls"}.get(stop, stop)

        turn = Turn(
            text="".join(text_parts) or None,
            thinking="\n".join(thinking_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=raw.get("usage", {}) or {},
            served_model=raw.get("model") or cfg["model"],
            raw=raw,
        )
        turn.raw["_content_blocks"] = raw.get("content", [])
        return turn

    def history_entry(self, turn: Turn) -> dict:
        return {"role": "assistant", "content": turn.raw.get("_content_blocks", [])}

    def tool_result(self, tool_call: ToolCall, content: str) -> dict:
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_call.id, "content": content}],
        }


ADAPTERS: dict[str, Callable[..., BaseAdapter]] = {
    "chat": ChatCompletionsAdapter,
    "responses": ResponsesAdapter,
    "anthropic": AnthropicAdapter,
}


def make_adapter(kind: str, **kwargs) -> BaseAdapter:
    return ADAPTERS[kind](**kwargs)
