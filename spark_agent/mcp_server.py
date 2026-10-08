"""Docufy as an MCP server, so other agents (Claude Desktop, Cursor, Claude Code) can use the template.

Run it over stdio:  python -m spark_agent mcp

It offers the template guide, a diagram check, the documents in the studio, and setting any Markdown
in the Spark template as PDF and Word. The template itself is never changed from here.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("docufy", instructions="Spark Technologies document template: write Markdown, then set it as branded PDF and Word.")


@server.tool()
def template_guide() -> str:
    """The Spark template's Markdown syntax: headings and kickers, :::blocks, tables, diagrams, the proposal pattern."""
    from .agent_tools import ToolBox, ToolContext

    return ToolBox(ToolContext("", None, None, {}, lambda: None)).t_template_guide()


@server.tool()
def check_diagram(mermaid: str) -> str:
    """Check that a mermaid flowchart renders in the template's diagram engine; returns a repaired version if not."""
    from .agent_tools import ToolBox, ToolContext

    return ToolBox(ToolContext("", None, None, {}, lambda: None)).t_check_diagram(mermaid)


@server.tool()
def list_documents(query: str = "") -> str:
    """Documents in the Docufy studio (latest versions), newest first. Optional search text."""
    from .studio import list_documents as listed

    rows = listed(query)
    if not rows:
        return "No documents."
    return "\n".join(f"{row['id']}  v{row['version']}  {row['pages']} pages  {row['title']}  (chat: {row['chat_title']})" for row in rows[:50])


@server.tool()
def read_document(document_id: str) -> str:
    """The Spark-template Markdown source of a studio document."""
    from .studio import StudioError, document_markdown, get_document

    try:
        return document_markdown(get_document(document_id))
    except StudioError as exc:
        return f"Error: {exc}"


@server.tool()
def render_markdown(markdown: str, out_dir: str, client_pages: bool = False, plan: bool = False) -> str:
    """Set Markdown in the Spark template and write PDF and Word files into out_dir.

    With plan=False the Markdown must already use template syntax (see template_guide). With plan=True free
    Markdown is mapped onto the template's components by the planner (no model: by headings).
    """
    import yaml

    from .blocks import split_frontmatter

    folder = Path(out_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    meta, body = split_frontmatter(markdown)
    meta = dict(meta or {})
    meta["include_spark_content"] = bool(client_pages)
    if "title" not in meta:
        found = re.search(r"^#\s+(.+)$", body, re.M)
        meta["title"] = found.group(1).strip() if found else "Document"
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip() + "\n---\n\n" + body.lstrip()
    stem = re.sub(r"[^\w .,()&-]+", " ", str(meta["title"])).strip()[:80] or f"document-{uuid.uuid4().hex[:6]}"
    if plan:
        from .convert import convert
        from .llm import Router

        source = folder / f"{stem}.md"
        source.write_text(text, encoding="utf-8")
        router = Router()
        router.fake = _no_model
        result = convert(source, router, spark_content=client_pages, render=True, docx=True, out_dir=folder, log=lambda *_: None)
        return f"PDF: {result.pdf}\nWord: {result.docx}\nChecks: {len(result.issues)} notes"
    from .verify import render_and_check

    md = folder / f"{stem}.spark.md"
    md.write_text(text, encoding="utf-8")
    pdf, docx, issues = render_and_check(md, folder, docx=True)
    lines = [f"PDF: {pdf}", f"Word: {docx}"]
    if issues:
        lines.append("Checks:\n- " + "\n- ".join(issues[:10]))
    return "\n".join(lines)


def _no_model(_model, _messages, _schema) -> str:
    from .llm import LLMError

    raise LLMError("no model over MCP")


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
