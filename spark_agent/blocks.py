"""Split source markdown into raw top-level blocks with stable IDs (the unit the planner references)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from spark_docs.parser import table_gap_continues

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
FENCE = re.compile(r"^(```|~~~)\s*([\w-]*)")
IMAGE = re.compile(r"^!\[[^\]]*\]\([^)]+\)")
HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})\s*$")


@dataclass
class SrcBlock:
    id: str
    kind: str  # heading | para | list | table | code | mermaid | image | container | quote | hr | comment
    text: str
    level: int = 0

    @property
    def heading_text(self) -> str:
        m = HEADING.match(self.text.strip())
        return m.group(2).strip() if m else ""

    def preview(self, n: int = 160) -> str:
        t = re.sub(r"\s+", " ", self.text).strip()
        return t if len(t) <= n else t[: n - 1] + "…"


def split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            data = yaml.safe_load(text[3:end]) or {}
            return (data if isinstance(data, dict) else {}), text[end + 4 :].lstrip("\n")
    return {}, text


def segment(body: str) -> list[SrcBlock]:
    lines = body.replace("\r\n", "\n").split("\n")
    out: list[SrcBlock] = []
    i, n = 0, len(lines)

    def add(kind: str, chunk: list[str], level: int = 0):
        text = "\n".join(chunk).rstrip()
        if text.strip():
            out.append(SrcBlock(f"b{len(out) + 1}", kind, text, level))

    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        m = FENCE.match(s)
        if m:
            chunk = [line]
            i += 1
            while i < n and not lines[i].strip().startswith(m.group(1)):
                chunk.append(lines[i])
                i += 1
            if i < n:
                chunk.append(lines[i])
                i += 1
            add("mermaid" if m.group(2).lower() in {"mermaid", "mmd", "diagram"} else "code", chunk)
            continue
        if s.startswith(":::") and len(s) > 3:
            chunk, depth = [line], 1
            i += 1
            while i < n and depth:
                t = lines[i].strip()
                if t.startswith(":::") and len(t) > 3:
                    depth += 1
                elif t == ":::":
                    depth -= 1
                chunk.append(lines[i])
                i += 1
            add("container", chunk)
            continue
        if s.startswith("<!--"):
            chunk = [line]
            while "-->" not in lines[i] and i + 1 < n:
                i += 1
                chunk.append(lines[i])
            i += 1
            add("comment", chunk)
            continue
        hm = HEADING.match(s)
        if hm:
            add("heading", [s], len(hm.group(1)))
            i += 1
            continue
        if HR.match(s):
            add("hr", [s])
            i += 1
            continue
        if IMAGE.match(s):
            add("image", [s])
            i += 1
            continue
        if s.startswith("|"):
            chunk = []
            while i < n:
                cur = lines[i].strip()
                if cur.startswith("|"):
                    chunk.append(lines[i].rstrip("\r"))
                    i += 1
                    continue
                if cur == "" and table_gap_continues(lines, i):
                    i += 1
                    continue
                break
            add("table", chunk)
            continue
        if s.startswith(">"):
            chunk = []
            while i < n and lines[i].strip().startswith(">"):
                chunk.append(lines[i])
                i += 1
            add("quote", chunk)
            continue
        if LIST_ITEM.match(line):
            chunk = []
            while i < n:
                t = lines[i]
                if not t.strip():
                    j = i + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and (LIST_ITEM.match(lines[j]) or lines[j].startswith(("  ", "\t"))):
                        chunk.append("")
                        i = j
                        continue
                    break
                if LIST_ITEM.match(t) or t.startswith(("  ", "\t")) or (chunk and not HEADING.match(t.strip()) and not t.strip().startswith(("|", "```", ":::", "!["))):
                    chunk.append(t)
                    i += 1
                    continue
                break
            add("list", chunk)
            continue
        chunk = []
        while i < n and lines[i].strip() and not (
            HEADING.match(lines[i].strip()) or FENCE.match(lines[i].strip()) or lines[i].strip().startswith(("|", ":::", "<!--", ">"))
            or IMAGE.match(lines[i].strip()) or HR.match(lines[i].strip()) or (chunk and LIST_ITEM.match(lines[i]))
        ):
            chunk.append(lines[i])
            i += 1
        if not chunk:  # safety: consume one line
            chunk = [lines[i]]
            i += 1
        add("para", chunk)
    return out
