"""The MCP hub talks to a real stdio server: Docufy's own."""

from __future__ import annotations

import sys

import pytest

from spark_agent import mcp_hub, studio
from spark_agent.agent_tools import ToolBox, ToolContext


@pytest.fixture
def hub():
    found = mcp_hub.Hub()
    previous = mcp_hub._hub
    mcp_hub._hub = found
    yield found
    found.shutdown()
    mcp_hub._hub = previous


def test_servers_are_validated():
    with pytest.raises(mcp_hub.MCPConfigError):
        mcp_hub.normalize({"name": "Bad Name!", "command": "x"})
    with pytest.raises(mcp_hub.MCPConfigError):
        mcp_hub.normalize({"name": "web", "transport": "http", "url": "ftp://x"})
    ok = mcp_hub.normalize({"name": "Files", "command": "npx", "args": "-y server"})
    assert ok["name"] == "files" and ok["transport"] == "stdio" and ok["args"] == ["-y", "server"]


def test_the_agent_sees_and_calls_tools_on_a_stdio_server(hub):
    studio.studio_dir()
    mcp_hub.save_servers([{"name": "docufy-self", "command": sys.executable, "args": ["-m", "spark_agent", "mcp"]}])
    specs = hub.tool_specs(timeout=60)
    names = {spec["name"] for spec in specs}
    assert "mcp__docufy_self__check_diagram" in names
    assert all(spec["parameters"]["type"] == "object" for spec in specs)
    ctx = ToolContext("abcdefabcdef", None, None, {}, lambda: None, mcp_specs=specs)
    box = ToolBox(ctx)
    assert "mcp__docufy_self__check_diagram" in {spec["name"] for spec in box.specs()}
    out = box.run("mcp__docufy_self__check_diagram", {"mermaid": "flowchart LR\n  A[One] --> B[Two]"})
    assert out.startswith("OK"), out
    label, _detail = box.label("mcp__docufy_self__check_diagram", {})
    assert label.startswith("docufy self")
    status = hub.status()
    assert status[0]["status"] == "ready" and status[0]["tools"]


def test_a_broken_server_is_reported_not_raised(hub):
    studio.studio_dir()
    mcp_hub.save_servers([{"name": "broken", "command": "this-command-does-not-exist-docufy"}])
    assert hub.tool_specs(timeout=20) == []
    row = hub.status()[0]
    assert row["status"] == "failed" and row["error"]
