"""Model providers for the agent harness.

OpenAI runs on the Responses API with hosted web search, streamed text, streamed
reasoning summaries, and function tools. Local models run on Ollama's
OpenAI-compatible Chat Completions endpoint with the same function tools.

Both adapters own their own transcript, because the two wire formats differ:
the harness only sees turns (text, tool calls, citations, usage) and hands back
tool results.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}
_LOCK = threading.Lock()
_SKIP = re.compile(r"audio|realtime|tts|transcribe|image|dall|embedding|moderation|whisper|search|instruct|codex|oss|vision|preview|chat-latest|davinci|babbage", re.I)


class ProviderError(RuntimeError):
    pass


# keys and clients -----------------------------------------------------------------


def openai_key() -> str:
    found = os.environ.get("OPENAI_API_KEY", "").strip()
    if found:
        return found
    try:
        from . import secrets

        return (secrets.value("openai") or "").strip()
    except Exception:
        return ""


def openai_client(timeout: float = 300.0, base_url: str | None = None, api_key: str | None = None):
    from openai import OpenAI

    key = api_key if api_key is not None else openai_key()
    if not key:
        raise ProviderError("add an OpenAI key in Settings")
    kwargs: dict[str, Any] = {"api_key": key, "timeout": timeout, "max_retries": 2}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def list_openai_models(refresh: bool = False) -> list[str]:
    """Chat-capable model ids this key can use, newest first. Cached for an hour."""
    key = openai_key()
    if not key:
        return []
    with _LOCK:
        hit = _MODELS_CACHE.get(key[-8:])
        # A list is kept for an hour; a failed lookup for five minutes, so the menu does not wait on every poll.
        if hit and not refresh and time.time() - hit[0] < (3600 if hit[1] else 300):
            return list(hit[1])
    try:
        client = openai_client(timeout=8)
        ids = [item.id for item in client.models.list()]
    except Exception:
        with _LOCK:
            _MODELS_CACHE[key[-8:]] = (time.time(), [])
        return []
    usable = [name for name in ids if re.match(r"^(gpt-\d|o\d)", name) and not _SKIP.search(name)]
    usable.sort(key=_rank, reverse=True)
    with _LOCK:
        _MODELS_CACHE[key[-8:]] = (time.time(), usable)
    return usable


def _rank(name: str) -> tuple:
    numbers = tuple(int(part) for part in re.findall(r"\d+", name.split("-20")[0])[:3])
    dated = 1 if re.search(r"-\d{4}-\d{2}-\d{2}$", name) else 0
    return (numbers, -dated, -len(name))


def is_small(name: str) -> bool:
    return bool(re.search(r"mini|nano|luna|small|lite|flash", name, re.I))


def resolve_openai_model(wanted: str, *, fast: bool = False) -> str:
    """Keep the configured model when the key has it. Otherwise take the newest one that fits the role."""
    wanted = (wanted or "").split("/", 1)[-1].strip()
    available = list_openai_models()
    if not available:
        return wanted or ("gpt-5-mini" if fast else "gpt-5")
    if wanted and wanted in available:
        return wanted
    pool = [name for name in available if is_small(name) == fast and "nano" not in name] or available
    return pool[0]


def is_reasoning(model: str) -> bool:
    name = model.split("/", 1)[-1]
    return bool(re.match(r"^(o\d|gpt-5|gpt-6|gpt-\d{2})", name))


# turn types -------------------------------------------------------------------------


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw: str = ""


@dataclass
class Citation:
    url: str
    title: str = ""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    searches: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.searches += other.searches


@dataclass
class Turn:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish: str = "stop"
    model: str = ""


@dataclass
class Events:
    """Callbacks the harness gives an adapter. Every one is optional."""

    text: Callable[[str], None] = lambda _delta: None
    thinking: Callable[[str], None] = lambda _delta: None
    search: Callable[[str, str, str], None] = lambda _id, _status, _query: None
    tool_started: Callable[[str, str], None] = lambda _id, _name: None


# OpenAI Responses -------------------------------------------------------------------


class ResponsesAdapter:
    """OpenAI Responses API. Hosted web search plus function tools, all streamed."""

    hosted_search = True

    def __init__(
        self,
        model: str,
        *,
        effort: str = "medium",
        search_context: str = "medium",
        web: bool = True,
        max_output: int = 16000,
        client=None,
        cache_key: str = "",
    ) -> None:
        self.model = model
        self.cache_key = cache_key
        self.effort = effort
        self.search_context = search_context
        self.web = web
        self.max_output = max_output
        self.client = client
        self.items: list[Any] = []
        self.system = ""
        self._reasoning_ok = is_reasoning(model)
        self._summary_ok = True
        self._sources_ok = True
        self._cache_ok = True

    def start(self, system: str, history: list[dict], user: list[dict] | str) -> None:
        self.system = system
        self.items = [{"role": item["role"], "content": item["content"]} for item in history]
        self.items.append({"role": "user", "content": user if isinstance(user, str) else user})

    def add_results(self, results: list[tuple[ToolCall, str]]) -> None:
        for call, output in results:
            self.items.append({"type": "function_call_output", "call_id": call.id, "output": output})

    def nudge(self, text: str) -> None:
        self.items.append({"role": "user", "content": text})

    def _tools(self, functions: list[dict]) -> list[dict]:
        tools: list[dict] = []
        if self.web:
            tools.append({"type": "web_search", "search_context_size": self.search_context})
        for spec in functions:
            tools.append(
                {
                    "type": "function",
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                    "strict": False,
                }
            )
        return tools

    def step(self, functions: list[dict], events: Events, check: Callable[[], None]) -> Turn:
        client = self.client or openai_client()
        self.client = client
        for _attempt in range(4):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "instructions": self.system,
                "input": self.items,
                "tools": self._tools(functions),
                "max_output_tokens": self.max_output,
                "parallel_tool_calls": True,
                "stream": True,
            }
            if self._reasoning_ok:
                kwargs["reasoning"] = {"effort": self.effort}
                if self._summary_ok:
                    kwargs["reasoning"]["summary"] = "auto"
            if self.web and self._sources_ok:
                kwargs["include"] = ["web_search_call.action.sources"]
            if self.cache_key and self._cache_ok:
                kwargs["prompt_cache_key"] = self.cache_key  # turns of one chat share a cached prefix
            try:
                return self._consume(client.responses.create(**kwargs), events, check)
            except Exception as exc:  # the API names the parameter it refused
                text = str(exc)
                if self._reasoning_ok and self.effort not in {"low", "medium", "high"} and ("minimal" in text or "effort" in text):
                    # Minimal effort cannot use web search, and newer models name the lowest level differently.
                    self.effort = "low"
                    continue
                if "reasoning.summary" in text or ("summary" in text and "organization" in text.lower()):
                    self._summary_ok = False
                    continue
                if "reasoning" in text and ("not supported" in text or "Unsupported" in text):
                    self._reasoning_ok = False
                    continue
                if "include" in text and "sources" in text:
                    self._sources_ok = False
                    continue
                if "prompt_cache_key" in text:
                    self._cache_ok = False
                    continue
                if "web_search" in text and ("not supported" in text or "Unsupported" in text or "not enabled" in text):
                    self.web = False
                    continue
                raise
        raise ProviderError("the model refused every request shape")

    def _consume(self, stream, events: Events, check: Callable[[], None]) -> Turn:
        turn = Turn(model=self.model)
        final = None
        pieces: list[str] = []
        for event in stream:
            check()
            kind = getattr(event, "type", "")
            if kind == "response.output_text.delta":
                pieces.append(event.delta)
                events.text(event.delta)
            elif kind == "response.reasoning_summary_text.delta":
                events.thinking(event.delta)
            elif kind == "response.reasoning_summary_part.done":
                events.thinking("\n\n")
            elif kind == "response.output_item.added":
                item = event.item
                if getattr(item, "type", "") == "web_search_call":
                    events.search(item.id, "searching", "")
                elif getattr(item, "type", "") == "function_call":
                    events.tool_started(item.call_id, item.name)
            elif kind == "response.output_item.done":
                item = event.item
                if getattr(item, "type", "") == "web_search_call":
                    action = getattr(item, "action", None)
                    query = str(getattr(action, "query", "") or "") if action is not None else ""
                    if not query and action is not None:
                        query = "; ".join(str(q) for q in (getattr(action, "queries", None) or [])[:3])
                    events.search(item.id, "done", query)
                    for source in (getattr(action, "sources", None) or []) if action is not None else []:
                        url = getattr(source, "url", "") or ""
                        if url:
                            turn.citations.append(Citation(url=url, title=str(getattr(source, "title", "") or "")))
            elif kind in {"response.completed", "response.incomplete"}:
                final = event.response
            elif kind in {"response.failed", "error"}:
                response = getattr(event, "response", None)
                error = getattr(response, "error", None) if response is not None else getattr(event, "message", "")
                raise ProviderError(str(getattr(error, "message", error) or "the model call failed"))
        if final is None:
            raise ProviderError("the stream ended before the reply finished")
        turn.finish = "length" if getattr(final, "status", "") == "incomplete" else "stop"
        for item in final.output or []:
            kind = getattr(item, "type", "")
            if kind == "function_call":
                turn.calls.append(ToolCall(item.call_id, item.name, _args(item.arguments), item.arguments or ""))
            elif kind == "web_search_call":
                turn.usage.searches += 1
            elif kind == "message":
                for part in getattr(item, "content", None) or []:
                    for note in getattr(part, "annotations", None) or []:
                        if getattr(note, "type", "") == "url_citation":
                            turn.citations.append(Citation(url=note.url, title=str(getattr(note, "title", "") or "")))
            dumped = item.model_dump(exclude_none=True, mode="json")
            if kind == "web_search_call" and isinstance(dumped.get("action"), dict):
                dumped["action"].pop("sources", None)
            self.items.append(dumped)
        turn.text = "".join(pieces) or str(getattr(final, "output_text", "") or "")
        usage = getattr(final, "usage", None)
        if usage is not None:
            turn.usage.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            turn.usage.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0)
            reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0)
            turn.usage.cached_tokens = int(cached or 0)
            turn.usage.reasoning_tokens = int(reasoning or 0)
        return turn


# Chat Completions (Ollama and other OpenAI-compatible servers) ----------------------


class ChatAdapter:
    """OpenAI-compatible Chat Completions with function tools. No hosted search."""

    hosted_search = False

    def __init__(self, model: str, *, base_url: str, api_key: str = "ollama", max_output: int = 4096, client=None) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.max_output = max_output
        self.client = client
        self.messages: list[dict] = []

    def start(self, system: str, history: list[dict], user: list[dict] | str) -> None:
        text = user if isinstance(user, str) else "\n\n".join(part.get("text", "") for part in user if part.get("type") == "input_text")
        self.messages = [{"role": "system", "content": system}]
        self.messages += [{"role": item["role"], "content": _flat(item["content"])} for item in history]
        self.messages.append({"role": "user", "content": text})

    def add_results(self, results: list[tuple[ToolCall, str]]) -> None:
        for call, output in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

    def nudge(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def step(self, functions: list[dict], events: Events, check: Callable[[], None]) -> Turn:
        client = self.client or openai_client(base_url=self.base_url, api_key=self.api_key)
        self.client = client
        tools = [{"type": "function", "function": spec} for spec in functions]
        stream = client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            tools=tools or None,
            max_tokens=self.max_output,
            temperature=0.3,
            stream=True,
            stream_options={"include_usage": True},
        )
        turn = Turn(model=self.model)
        text: list[str] = []
        calls: dict[int, dict] = {}
        for chunk in stream:
            check()
            if getattr(chunk, "usage", None):
                turn.usage.input_tokens = int(chunk.usage.prompt_tokens or 0)
                turn.usage.output_tokens = int(chunk.usage.completion_tokens or 0)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if getattr(delta, "content", None):
                text.append(delta.content)
                events.text(delta.content)
            for piece in getattr(delta, "tool_calls", None) or []:
                slot = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                if piece.id:
                    slot["id"] = piece.id
                if piece.function and piece.function.name:
                    slot["name"] = piece.function.name
                    events.tool_started(slot["id"] or str(piece.index), slot["name"])
                if piece.function and piece.function.arguments:
                    slot["arguments"] += piece.function.arguments
            if choice.finish_reason:
                turn.finish = "length" if choice.finish_reason == "length" else "stop"
        turn.text = "".join(text)
        message: dict[str, Any] = {"role": "assistant", "content": turn.text}
        if calls:
            message["tool_calls"] = []
            for index, slot in sorted(calls.items()):
                call_id = slot["id"] or f"call_{index}_{int(time.time() * 1000)}"
                turn.calls.append(ToolCall(call_id, slot["name"], _args(slot["arguments"]), slot["arguments"]))
                message["tool_calls"].append(
                    {"id": call_id, "type": "function", "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"}}
                )
        self.messages.append(message)
        return turn


def _flat(content) -> str:
    if isinstance(content, str):
        return content
    return "\n\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))


def _args(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            from .llm import parse_json

            data = parse_json(raw)
        except ValueError:
            return {"_invalid": raw[:2000]}
    return data if isinstance(data, dict) else {"value": data}
