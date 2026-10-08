"""MCP servers the agent can use as tools.

Servers are listed in workspace/studio/mcp.json and edited from Settings. Each enabled
server gets one long-lived session on a background event loop, so a stdio server is
started once, not once per call. Tools are exposed to the model as
``mcp__<server>__<tool>`` and every result comes back as text.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_RESULT = 24_000


class MCPConfigError(RuntimeError):
    pass


@dataclass
class _Server:
    config: dict
    status: str = "idle"  # idle, connecting, ready, failed
    error: str = ""
    tools: list[dict] = field(default_factory=list)
    session: object = None
    stop: asyncio.Event | None = None
    task: asyncio.Future | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    started: float = 0.0


def config_path() -> Path:
    from .studio import studio_dir

    return studio_dir() / "mcp.json"


def load_servers() -> list[dict]:
    path = config_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    servers = data.get("servers") if isinstance(data, dict) else data
    return [item for item in (servers or []) if isinstance(item, dict) and item.get("name")]


def save_servers(servers: list[dict]) -> list[dict]:
    clean = [normalize(item) for item in servers]
    names = [item["name"] for item in clean]
    if len(names) != len(set(names)):
        raise MCPConfigError("two servers share a name")
    config_path().write_text(json.dumps({"servers": clean}, indent=2), encoding="utf-8")
    hub().reload()
    return clean


def normalize(item: dict) -> dict:
    name = str(item.get("name") or "").strip().lower().replace(" ", "-")
    if not _NAME.match(name):
        raise MCPConfigError("a server name is lower case letters, digits, - or _, up to 32 characters")
    transport = str(item.get("transport") or ("stdio" if item.get("command") else "http")).lower()
    if transport not in {"stdio", "http", "sse"}:
        raise MCPConfigError("transport is stdio, http, or sse")
    out: dict = {"name": name, "transport": transport, "enabled": bool(item.get("enabled", True))}
    if transport == "stdio":
        command = str(item.get("command") or "").strip()
        if not command:
            raise MCPConfigError(f"{name}: a stdio server needs a command")
        args = item.get("args") or []
        if isinstance(args, str):
            args = shlex.split(args, posix=os.name != "nt")
        out["command"] = command
        out["args"] = [str(arg) for arg in args]
        env = item.get("env") or {}
        out["env"] = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
    else:
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise MCPConfigError(f"{name}: the server address must start with http:// or https://")
        out["url"] = url
        headers = item.get("headers") or {}
        out["headers"] = {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}
    allow = item.get("allow") or []
    out["allow"] = [str(tool) for tool in allow] if isinstance(allow, list) else []
    out["description"] = str(item.get("description") or "")[:300]
    return out


class Hub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.servers: dict[str, _Server] = {}
        self.lock = threading.Lock()

    # loop ---------------------------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self.lock:
            if self.loop is None or not self.thread or not self.thread.is_alive():
                if os.name == "nt":
                    loop = asyncio.ProactorEventLoop()  # subprocess support for stdio servers
                else:
                    loop = asyncio.new_event_loop()
                self.loop = loop
                self.thread = threading.Thread(target=loop.run_forever, name="docufy-mcp", daemon=True)
                self.thread.start()
            return self.loop

    def _run(self, coro, timeout: float):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError("the MCP server did not answer in time") from None

    # lifecycle ----------------------------------------------------------------------
    def reload(self) -> None:
        wanted = {item["name"]: item for item in load_servers() if item.get("enabled", True)}
        for name in list(self.servers):
            server = self.servers[name]
            if name not in wanted or wanted[name] != server.config:
                self._close(server)
                self.servers.pop(name, None)

    def _close(self, server: _Server) -> None:
        if server.stop is not None and self.loop is not None:
            self.loop.call_soon_threadsafe(server.stop.set)

    def connect(self, name: str, timeout: float = 30.0) -> _Server:
        configs = {item["name"]: item for item in load_servers()}
        config = configs.get(name)
        if config is None:
            raise MCPConfigError(f"there is no MCP server called {name}")
        with self.lock:
            server = self.servers.get(name)
            if server is not None and server.config != config:
                self._close(server)
                server = None
            if server is not None and server.status == "failed" and time.time() - server.started > 60:
                server = None
            if server is None:
                server = _Server(config=config, status="connecting", started=time.time())
                self.servers[name] = server
                start = True
            else:
                start = False
        if start:
            loop = self._ensure_loop()
            server.task = asyncio.run_coroutine_threadsafe(self._serve(server), loop)
        server.ready.wait(timeout)
        if server.status == "connecting":
            server.status, server.error = "failed", "the server did not start in time"
        return server

    async def _serve(self, server: _Server) -> None:
        from mcp import ClientSession

        server.stop = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                read, write = await self._open(stack, server.config)
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), 30)
                listed = await asyncio.wait_for(session.list_tools(), 30)
                allow = set(server.config.get("allow") or [])
                server.tools = [
                    {
                        "name": tool.name,
                        "description": (tool.description or "")[:1000],
                        "schema": getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}},
                    }
                    for tool in listed.tools
                    if not allow or tool.name in allow
                ]
                server.session = session
                server.status, server.error = "ready", ""
                server.ready.set()
                await server.stop.wait()
        except BaseException as exc:  # noqa: BLE001 - a server's failure is shown, never raised into the app
            server.status = "failed"
            server.error = _short(exc)
            server.session = None
        finally:
            if server.status == "ready":
                server.status = "idle"
            server.session = None
            server.ready.set()

    async def _open(self, stack: AsyncExitStack, config: dict):
        if config["transport"] == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            env = {**os.environ, **(config.get("env") or {})}
            params = StdioServerParameters(command=config["command"], args=config.get("args") or [], env=env)
            read, write = await stack.enter_async_context(stdio_client(params))
            return read, write
        if config["transport"] == "sse":
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(sse_client(config["url"], headers=config.get("headers") or None))
            return read, write
        from mcp.client.streamable_http import streamablehttp_client

        streams = await stack.enter_async_context(streamablehttp_client(config["url"], headers=config.get("headers") or None))
        return streams[0], streams[1]

    # use ----------------------------------------------------------------------------
    def tool_specs(self, timeout: float = 20.0) -> list[dict]:
        """Function specs for every tool on every enabled server that connects."""
        specs: list[dict] = []
        for config in load_servers():
            if not config.get("enabled", True):
                continue
            server = self.connect(config["name"], timeout=timeout)
            if server.status != "ready":
                continue
            for tool in server.tools:
                specs.append(
                    {
                        "name": tool_name(config["name"], tool["name"]),
                        "description": f"[MCP {config['name']}] {tool['description'] or tool['name']}"[:1024],
                        "parameters": _schema(tool["schema"]),
                    }
                )
        return specs

    def call(self, name: str, tool: str, arguments: dict, timeout: float = 120.0) -> str:
        server = self.connect(name)
        if server.status != "ready" or server.session is None:
            raise RuntimeError(f"MCP server {name} is not connected: {server.error or server.status}")
        result = self._run(server.session.call_tool(tool, arguments or {}), timeout)
        return render_result(result)

    def status(self) -> list[dict]:
        rows = []
        for config in load_servers():
            server = self.servers.get(config["name"])
            rows.append(
                {
                    **config,
                    "status": (server.status if server else "idle") if config.get("enabled", True) else "off",
                    "error": server.error if server else "",
                    "tools": [{"name": tool["name"], "description": tool["description"][:200]} for tool in (server.tools if server else [])],
                }
            )
        return rows

    def test(self, name: str) -> dict:
        with self.lock:
            existing = self.servers.pop(name, None)
        if existing is not None:
            self._close(existing)
        server = self.connect(name, timeout=40)
        return {"ok": server.status == "ready", "error": server.error, "tools": [tool["name"] for tool in server.tools]}

    def shutdown(self) -> None:
        for server in list(self.servers.values()):
            self._close(server)
        self.servers.clear()


def tool_name(server: str, tool: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", tool)
    return f"mcp__{server.replace('-', '_')}__{safe}"[:64]


def split_name(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    for config in load_servers():
        prefix = f"mcp__{config['name'].replace('-', '_')}__"
        if name.startswith(prefix):
            server = hub().servers.get(config["name"])
            for tool in server.tools if server else []:
                if tool_name(config["name"], tool["name"]) == name:
                    return config["name"], tool["name"]
            return config["name"], name[len(prefix) :]
    return None


def render_result(result) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(item.text)
        elif kind == "resource":
            resource = getattr(item, "resource", None)
            parts.append(str(getattr(resource, "text", "") or getattr(resource, "uri", "")))
        elif kind == "image":
            parts.append("[image returned by the tool]")
        else:
            parts.append(str(item)[:2000])
    structured = getattr(result, "structuredContent", None)
    if structured and not parts:
        parts.append(json.dumps(structured, ensure_ascii=False)[:MAX_RESULT])
    text = "\n".join(part for part in parts if part).strip() or "(the tool returned nothing)"
    if getattr(result, "isError", False):
        text = "Tool error: " + text
    return text[:MAX_RESULT]


def _schema(schema: dict) -> dict:
    data = dict(schema or {})
    data.setdefault("type", "object")
    data.setdefault("properties", {})
    data.pop("$schema", None)
    return data


def _short(exc: BaseException) -> str:
    inner = exc
    while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
        inner = inner.exceptions[0]
    text = " ".join(str(inner).split()) or inner.__class__.__name__
    return text[:300]


_hub: Hub | None = None


def hub() -> Hub:
    global _hub
    if _hub is None:
        _hub = Hub()
    return _hub
