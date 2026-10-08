"""Markdown (and pasted text) → Spark document model."""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

import yaml

from .glyphs import safe
from .images import resolve_path as resolve_image_path
from .links import resolve_blocks
from .mermaid import ASSET_SCALE, diagram_orientation
from .mermaid import render as render_mermaid
from .theme import DEFAULT_CONFIDENTIAL, Meta, merge_contact

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
UL_RE = re.compile(r"^(\s*)([-*+]|[•●])\s+(.*)$")
OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
FENCE_RE = re.compile(r"^```(\w+)?\s*$")
HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})\s*$")
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\((.+?)\)\s*(?:\{([^}]*)\})?\s*$")
CONTAINER_START = re.compile(r"^:::([A-Za-z0-9_-]+)(.*)$")
FLAGS_RE = re.compile(r"\s*\{([^{}]*)\}\s*$")
PAGEBREAK_RE = re.compile(r"^(?:<!--\s*pagebreak\s*-->|\\newpage|\\pagebreak)$", re.I)
STYLE_COMMENT_RE = re.compile(r"^<!--\s*spark-style\s+(\{.*\})\s*-->$")
FLAG_ALIASES = {
    "accent": "accent",
    "purple": "accent",
    "blue": "accent",
    "module": "module",
    "major": "module",
    "compact": "compact",
    "small": "compact",
    "ruled": "ruled",
    "large": "large",
    "unnumbered": "unnumbered",
}


def parse_path(path: Path) -> tuple[Meta, list[dict]]:
    return parse_markdown(path.read_text(encoding="utf-8"), source_dir=path.parent)


def parse_markdown(text: str, source_dir: Path | None = None) -> tuple[Meta, list[dict]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    meta, body = split_frontmatter(text)
    body = maybe_promote_plain(body)
    src = source_dir or Path(".")
    roots = _asset_roots(meta, src)
    blocks = resolve_blocks(parse_blocks(body.split("\n"), source_dir=src, asset_roots=roots))
    if meta.show_contact and not any(b.get("kind") == "contact" for b in blocks):
        blocks.append({"kind": "contact"})
    return meta, blocks


def parse_many(paths: list[Path]) -> tuple[Meta, list[dict]]:
    if not paths:
        return Meta(), []
    meta, blocks = parse_path(paths[0])
    for path in paths[1:]:
        extra_meta, extra_blocks = parse_path(path)
        if extra_blocks and extra_blocks[0].get("kind") != "heading":
            extra_blocks.insert(
                0,
                {
                    "kind": "heading",
                    "level": 1,
                    "text": extra_meta.title or path.stem.replace("_", " ").replace("-", " "),
                    "accent": False,
                },
            )
        if blocks:
            blocks.append({"kind": "pagebreak"})
        blocks.extend(extra_blocks)
        if not meta.title and extra_meta.title:
            meta.title = extra_meta.title
    return meta, blocks


def split_frontmatter(text: str) -> tuple[Meta, str]:
    if not text.startswith("---"):
        return Meta(), text.lstrip("\n")
    end = text.find("\n---", 3)
    if end == -1:
        return Meta(), text
    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        return Meta(), body
    contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}
    meta = Meta(
        title=str(data.get("title") or ""),
        subtitle=str(data.get("subtitle") or ""),
        doc_type=str(data.get("doc_type") or data.get("type") or "DOCUMENT"),
        prepared_for=str(data.get("prepared_for") or data.get("client") or ""),
        prepared_by=str(data.get("prepared_by") or "Spark Technologies"),
        date=str(data.get("date") or ""),
        confidential=bool(data.get("confidential", True)),
        confidential_text=str(data.get("confidential_text") or DEFAULT_CONFIDENTIAL),
        section_break=bool(data.get("section_break", True)),
        show_contact=bool(data.get("show_contact", False)),
        footer=str(data.get("footer") or "Spark Technologies  |  Confidential"),
        client=str(data.get("client") or data.get("prepared_for") or ""),
        contact=merge_contact(contact),
        include_company_intro=bool(data.get("include_company_intro", data.get("include_spark_content", False))),
        include_company_outro=bool(data.get("include_company_outro", data.get("include_spark_content", False))),
        company_intro_path=str(data.get("company_intro") or data.get("company_intro_path") or ""),
        company_outro_path=str(data.get("company_outro") or data.get("company_outro_path") or ""),
        inner_logo=bool(data.get("inner_logo", False)),
        inner_footer=bool(data.get("inner_footer", False)),
        top_bar=bool(data.get("top_bar", False)),
        strip_spark_content=bool(data.get("strip_spark_content", False)),
        archetype=str(data.get("archetype") or ""),
        industry=str(data.get("industry") or ""),
        spark_content_explicit=any(
            key in data
            for key in (
                "include_spark_content",
                "include_company_intro",
                "include_company_outro",
                "strip_spark_content",
            )
        ),
    )
    raw_roots = data.get("asset_roots") or data.get("asset_root")
    if isinstance(raw_roots, str):
        meta.asset_roots = [raw_roots]
    elif isinstance(raw_roots, list):
        meta.asset_roots = [str(r) for r in raw_roots if r]
    if data.get("confidential_text"):
        meta.confidential_text = str(data["confidential_text"])
    return meta, body


def maybe_promote_plain(text: str) -> str:
    if re.search(r"(?m)^#{1,6}\s+\S", text) or text.strip().startswith(":::"):
        return text
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if re.match(r"^\d{1,2}[\.)]\s+\S.{2,}$", stripped) and len(stripped) < 90:
            out.append("# " + stripped)
        elif (
            re.match(r"^[A-Z0-9][A-Z0-9 &/,:–—-]{10,}$", stripped)
            and len(stripped) < 70
            and not stripped.endswith(".")
        ):
            out.append("## " + stripped.title())
        else:
            out.append(line)
    return "\n".join(out)


def _asset_roots(meta: Meta, source_dir: Path) -> list[Path]:
    from .images import ROOT

    out: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            key = str(p.resolve())
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        out.append(p.resolve())

    for raw in meta.asset_roots:
        p = Path(raw)
        if p.is_absolute():
            add(p if p.is_dir() else p.parent)
            continue
        for base in (source_dir, source_dir.parent, ROOT, ROOT.parent):
            c = base / raw
            if c.is_dir() or c.is_file():
                add(c if c.is_dir() else c.parent)
                break
        else:
            add((source_dir / raw).resolve())

    adicc = ROOT.parent / "Adicc"
    if adicc.is_dir():
        add(adicc / "docs")
        add(adicc)
    return out


def parse_blocks(
    lines: list[str], source_dir: Path, asset_roots: list[Path] | None = None
) -> list[dict]:
    blocks: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped == "":
            i += 1
            continue

        if PAGEBREAK_RE.match(stripped):
            blocks.append({"kind": "pagebreak"})
            i += 1
            continue

        if stripped.startswith("<!--"):
            style = STYLE_COMMENT_RE.match(stripped)
            if style and blocks:
                try:
                    payload = json.loads(style.group(1))
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and blocks[-1].get("kind") != "pagebreak":
                    if payload.get("variant"):
                        blocks[-1]["variant"] = str(payload["variant"])
                    overrides = payload.get("style_overrides")
                    if isinstance(overrides, dict):
                        blocks[-1]["style_overrides"] = overrides
                    i += 1
                    continue
            while i < n and "-->" not in lines[i]:
                i += 1
            i += 1
            continue

        m = CONTAINER_START.match(stripped)
        if m:
            name = m.group(1).lower()
            attrs = parse_attrs(m.group(2))
            inner, i = read_container(lines, i + 1)
            blocks.append(parse_container(name, inner, source_dir, asset_roots=asset_roots, attrs=attrs))
            continue

        if stripped == ":::":
            i += 1
            continue

        if FENCE_RE.match(stripped):
            lang = FENCE_RE.match(stripped).group(1) or ""
            i += 1
            code: list[str] = []
            while i < n and not FENCE_RE.match(lines[i].strip()):
                code.append(lines[i])
                i += 1
            if i < n:
                i += 1
            source = "\n".join(code)
            if lang.lower() == "chart":
                from spark_docs.charts import render_chart_source

                png = render_chart_source(source)
                if png:
                    blocks.append({"kind": "image", "alt": "Chart", "path": str(png), "asset_scale": ASSET_SCALE})
                    continue
            if lang.lower() in {"mermaid", "mmd", "diagram"}:
                spec = claude_diagram_spec(source, lang.lower())
                if spec is not None:
                    blocks.append({"kind": "diagram", "spec": spec, "source": source, "lang": lang.lower()})
                    continue
            if lang.lower() in {"mermaid", "mmd"}:
                png = render_mermaid(source)
                if png:
                    wide = diagram_orientation(source) == "wide"
                    blocks.append(
                        {
                            "kind": "image",
                            "alt": "Diagram",
                            "path": png,
                            "asset_scale": ASSET_SCALE,
                            "diagram": True,
                            "diagram_wide": wide,
                        }
                    )
                    continue
            blocks.append({"kind": "code", "lang": lang, "text": source})
            continue

        if HR_RE.match(stripped):
            blocks.append({"kind": "hr"})
            i += 1
            continue

        img = IMAGE_RE.match(stripped)
        if img:
            block = {
                "kind": "image",
                "alt": img.group(1),
                "path": resolve_image_path(
                    img.group(2).strip().strip('"'), source_dir, asset_roots=asset_roots
                ),
            }
            img_attrs = parse_attrs(img.group(3) or "")
            if "width" in img_attrs:
                try:
                    block["width"] = float(img_attrs["width"])
                except ValueError:
                    pass
            blocks.append(block)
            i += 1
            continue

        hm = HEADING_RE.match(stripped)
        if hm:
            level = len(hm.group(1))
            text, flags = strip_heading_flags(hm.group(2).strip())
            blocks.append({"kind": "heading", "level": level, "text": text, **flags})
            i += 1
            continue

        if stripped.startswith(">"):
            quote, i = read_quoted(lines, i)
            title, body = split_callout_title(quote)
            blocks.append({"kind": "callout", "title": title, "blocks": body})
            continue

        if looks_like_table(lines, i):
            table, i = read_table(lines, i)
            blocks.append(table)
            continue

        if UL_RE.match(line) or OL_RE.match(line):
            lst, i = read_list(lines, i)
            blocks.append(lst)
            continue

        para, i = read_paragraph(lines, i)
        if para:
            if LABEL_RE.match(para):
                blocks.append({"kind": "label", "text": para})
            else:
                blocks.append({"kind": "para", "text": para})

    return blocks


def claude_diagram_spec(source: str, lang: str) -> dict | None:
    """Mermaid flowcharts and ```diagram YAML specs -> Claude-style diagram spec dict."""
    try:
        from diagrams.mermaid_import import parse as parse_mermaid
        from diagrams.spec import Diagram
    except ImportError:
        return None
    if lang == "diagram":
        data = yaml.safe_load(source) or {}
        return Diagram.from_dict(data).to_dict() if isinstance(data, dict) else None
    if source.strip().lower().startswith("sequencediagram"):
        from diagrams.sequence import parse as parse_sequence

        return parse_sequence(source)
    d = parse_mermaid(source)
    return d.to_dict() if d and d.nodes else None


LABEL_RE = re.compile(r"^\*\*[^*]{1,80}:\*\*$")
ATTR_RE = re.compile(r"""([A-Za-z_][\w-]*)\s*=\s*("[^"]*"|'[^']*'|[^\s]+)|(\.?[A-Za-z_][\w-]*)""")


def parse_attrs(raw: str) -> dict:
    """Parse `key=value key2="v w" flag .flag` into a dict (flags map to True)."""
    out: dict = {}
    for m in ATTR_RE.finditer(raw or ""):
        if m.group(1):
            out[m.group(1).lower()] = m.group(2).strip("\"'")
        elif m.group(3):
            out[m.group(3).lstrip(".").lower()] = True
    return out


def parse_widths(value) -> list[float] | None:
    if not value or value is True:
        return None
    try:
        return [float(v) for v in str(value).replace(";", ",").split(",") if v.strip()]
    except ValueError:
        return None


def read_container(lines: list[str], start: int) -> tuple[list[str], int]:
    inner: list[str] = []
    i = start
    depth = 1
    while i < len(lines):
        stripped = lines[i].strip()
        if CONTAINER_START.match(stripped):
            depth += 1
            inner.append(lines[i])
            i += 1
            continue
        if stripped == ":::":
            depth -= 1
            if depth == 0:
                return inner, i + 1
        inner.append(lines[i])
        i += 1
    return inner, i


def parse_container(
    name: str,
    inner: list[str],
    source_dir: Path,
    asset_roots: list[Path] | None = None,
    attrs: dict | None = None,
) -> dict:
    attrs = attrs or {}
    if name == "addendum":
        return {"kind": "addendum", "fields": parse_fields(inner)}
    inner_blocks = parse_blocks(inner, source_dir, asset_roots=asset_roots)
    if name in {"tight", "lines"}:
        return {"kind": "tight", "blocks": inner_blocks}
    if name in {"large", "list-large"}:
        return {"kind": "large", "blocks": inner_blocks}
    if name in {"table", "roadmap", "commercial"}:
        table = next((b for b in inner_blocks if b.get("kind") == "table"), {"headers": [], "rows": []})
        block = {
            "kind": "table" if name == "table" else name,
            "headers": table.get("headers") or [],
            "rows": table.get("rows") or [],
            "widths": parse_widths(attrs.get("widths")),
            "bold_first": bool(attrs.get("bold_first")),
        }
        if name == "commercial":
            rows = block["rows"]
            block["total"] = rows.pop() if rows and rows[-1] and rows[-1][0].strip().upper().startswith("TOTAL") else None
        return block
    if name in {"steps", "next-steps", "nextsteps"}:
        steps = []
        for group in split_by_heading(inner_blocks, 3):
            body = " ".join(b.get("text") or "" for b in group.get("blocks") or [] if b.get("kind") == "para")
            steps.append({"title": group.get("title") or "", "body": body.strip()})
        return {"kind": "steps", "steps": steps}
    if name in {"kpis", "kpi"}:
        groups = []
        for group in split_by_heading(inner_blocks, 3):
            items = [it.get("text") or "" for b in group.get("blocks") or [] if b.get("kind") == "list" for it in b.get("items") or []]
            groups.append({"title": group.get("title") or "", "items": items})
        return {"kind": "kpis", "groups": groups}
    if name in {"columns", "cols", "split"}:
        cols = split_by_heading(inner_blocks, 3)
        if len(cols) == 2 and not cols[1].get("accent"):
            cols[1]["accent"] = True
        return {"kind": "columns", "columns": cols}
    if name in {"cards", "grid"}:
        return {"kind": "cards", "cards": split_by_heading(inner_blocks, 3)}
    if name in {"callout", "box", "note", "panel"}:
        title = ""
        body = inner_blocks
        if body and body[0].get("kind") == "heading":
            title = body[0]["text"]
            body = body[1:]
        return {"kind": "callout", "title": title, "blocks": body}
    if name in {"contact", "banner"}:
        return {"kind": "contact"}
    if name in {"stageflow", "workflow", "pipeline"}:
        from .stageflow import parse_stageflow_steps

        return {"kind": "stageflow", "steps": parse_stageflow_steps(inner_blocks)}
    return {"kind": "callout", "title": "", "blocks": inner_blocks}


def split_by_heading(blocks: list[dict], level: int) -> list[dict]:
    groups: list[dict] = []
    current: dict | None = None
    for block in blocks:
        if block.get("kind") == "heading" and block.get("level") == level:
            current = {
                "title": block["text"],
                "accent": bool(block.get("accent")),
                "blocks": [],
            }
            groups.append(current)
        else:
            if current is None:
                current = {"title": "", "accent": False, "blocks": []}
                groups.append(current)
            current["blocks"].append(block)
    return groups


def strip_accent(text: str) -> tuple[str, bool]:
    text, flags = strip_heading_flags(text)
    return text, flags["accent"]


def format_kicker(text: str) -> str:
    m = re.match(r"^(?:A(\d+)|0?(\d+))[\.\):\-–—]?\s+(.*)$", text.strip(), re.I)
    if m:
        if m.group(1) is not None:
            return f"A{int(m.group(1))}  {m.group(3).upper()}"
        return f"{int(m.group(2)):02d}  {m.group(3).upper()}"
    return text.upper()


def parse_fields(lines: list[str]) -> dict[str, str]:
    """`key: value` lines (used by :::addendum)."""
    out: dict[str, str] = {}
    for ln in lines:
        if ":" in ln and not ln.strip().startswith("#"):
            key, value = ln.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out


def strip_heading_flags(text: str) -> tuple[str, dict[str, bool]]:
    flags = {name: False for name in set(FLAG_ALIASES.values())}
    m = FLAGS_RE.search(text)
    if m and "." in m.group(1):
        for token in m.group(1).split():
            key = FLAG_ALIASES.get(token.lstrip(".").lower())
            if key:
                flags[key] = True
        text = FLAGS_RE.sub("", text).strip()
    if not flags["module"] and re.match(r"^Module\s+\d+\b", text, re.I):
        flags["module"] = True
    return text, flags


def read_quoted(lines: list[str], i: int) -> tuple[str, int]:
    parts: list[str] = []
    while i < len(lines) and lines[i].strip().startswith(">"):
        raw = lines[i].strip()
        parts.append(re.sub(r"^>\s?", "", raw))
        i += 1
    return "\n".join(parts).strip(), i


def split_callout_title(text: str) -> tuple[str, list[dict]]:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return "", []
    first = lines[0]
    title = ""
    rest = lines
    if first.startswith("**") and first.endswith("**") and first.count("**") == 2:
        title = first.strip("*").strip()
        rest = lines[1:]
    blocks = [{"kind": "para", "text": " ".join(rest)}] if rest else []
    return title, blocks


def separator_line(line: str) -> bool:
    return bool(re.match(r"^\|?\s*:?-{3,}", line.strip().replace(" ", "")))


def table_gap_continues(lines: list[str], i: int) -> bool:
    """A blank line at i is inside one table, rather than the gap before the next table."""
    nxt = i + 1
    if nxt >= len(lines) or not lines[nxt].strip().startswith("|"):
        return False
    if separator_line(lines[nxt]):
        return True
    after = nxt + 1
    if after < len(lines) and not lines[after].strip():
        after += 1
    if after < len(lines) and separator_line(lines[after]):
        return False
    return True


def tighten_tables(text: str) -> str:
    """Drop blank lines that were inserted between the rows of one table."""
    lines = text.split("\n")
    out: list[str] = []
    fence = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            fence = not fence
        if not fence and stripped == "" and table_gap_continues(lines, i):
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def looks_like_table(lines: list[str], i: int) -> bool:
    line = lines[i].strip()
    if "|" not in line:
        return False
    if line.startswith("|") or line.count("|") >= 2:
        j = i + 1
        if j < len(lines) and not lines[j].strip() and table_gap_continues(lines, j):
            j += 1
        if j < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[j]):
            return True
        if j < len(lines) and "|" in lines[j]:
            return True
    return False


def read_table(lines: list[str], i: int) -> tuple[dict, int]:
    rows: list[list[str]] = []
    while i < len(lines):
        if not lines[i].strip():
            if table_gap_continues(lines, i):
                i += 1
                continue
            break
        if "|" not in lines[i]:
            break
        raw = lines[i].strip()
        if separator_line(raw):
            i += 1
            continue
        cells = [c.strip() for c in raw.strip("|").split("|")]
        rows.append(cells)
        i += 1
    headers = rows[0] if rows else []
    body = rows[1:] if len(rows) > 1 else []
    width = max((len(r) for r in rows), default=0)
    headers = pad_row(headers, width)
    body = [pad_row(r, width) for r in body]
    return {"kind": "table", "headers": headers, "rows": body}, i


def pad_row(row: list[str], width: int) -> list[str]:
    return row + [""] * (width - len(row))


def read_list(lines: list[str], i: int) -> tuple[dict, int]:
    ordered = bool(OL_RE.match(lines[i]))
    items: list[dict] = []
    while i < len(lines):
        line = lines[i]
        um = UL_RE.match(line)
        om = OL_RE.match(line)
        if not um and not om:
            if line.strip() == "":
                if i + 1 < len(lines) and (UL_RE.match(lines[i + 1]) or OL_RE.match(lines[i + 1])):
                    i += 1
                    continue
                break
            if items and not HEADING_RE.match(line.strip()) and not line.strip().startswith(":::"):
                if line.startswith("  ") or line.startswith("\t"):
                    items[-1]["text"] += " " + line.strip()
                    i += 1
                    continue
            break
        m = om or um
        indent = len(m.group(1).replace("\t", "    "))
        text = m.group(3) if um else m.group(3)
        items.append({"text": text, "indent": 0 if indent < 2 else 1})
        i += 1
    return {"kind": "list", "ordered": ordered, "items": items}, i


def read_paragraph(lines: list[str], i: int) -> tuple[str, int]:
    parts: list[str] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == "":
            break
        if (
            HEADING_RE.match(stripped)
            or UL_RE.match(line)
            or OL_RE.match(line)
            or stripped.startswith(">")
            or stripped.startswith(":::")
            or FENCE_RE.match(stripped)
            or HR_RE.match(stripped)
            or IMAGE_RE.match(stripped)
            or looks_like_table(lines, i)
        ):
            break
        parts.append(stripped)
        i += 1
    return " ".join(parts).strip(), i


_STYLE_TAG = re.compile(r"<(/?)(b|i|u)>")


def balance_tags(xml: str) -> str:
    """Close and reopen b, i, and u so they nest. Markdown like **bold *both** italic* overlaps otherwise."""
    out: list[str] = []
    stack: list[str] = []
    last = 0
    for match in _STYLE_TAG.finditer(xml):
        out.append(xml[last : match.start()])
        last = match.end()
        closing, name = match.group(1) == "/", match.group(2)
        if not closing:
            stack.append(name)
            out.append(f"<{name}>")
            continue
        if name not in stack:
            continue
        reopen = []
        while stack:
            top = stack.pop()
            out.append(f"</{top}>")
            if top == name:
                break
            reopen.append(top)
        for tag in reversed(reopen):
            stack.append(tag)
            out.append(f"<{tag}>")
    out.append(xml[last:])
    out.extend(f"</{tag}>" for tag in reversed(stack))
    return re.sub(r"<(b|i|u)></\1>", "", "".join(out))


def inline_xml(text: str) -> str:
    """Convert markdown inline markup to ReportLab paragraph XML."""
    if not text:
        return ""
    placeholders: list[str] = []

    def stash(value: str) -> str:
        placeholders.append(value)
        return f"@@P{len(placeholders) - 1}@@"

    def code_sub(m: re.Match) -> str:
        return stash(
            f'<font face="Courier" size="9" color="#1A1A2E">{escape(m.group(1))}</font>'
        )

    def link_sub(m: re.Match) -> str:
        label = escape(m.group(1))
        href = escape(m.group(2), quote=True)
        return stash(f'<a href="{href}" color="#6B4EFF"><u>{label}</u></a>')

    def bare_url_sub(m: re.Match) -> str:
        url = m.group(0).rstrip(".,;)")
        return stash(f'<a href="{escape(url, quote=True)}" color="#6B4EFF"><u>{escape(url)}</u></a>')

    def angle_sub(m: re.Match) -> str:
        url = m.group(1)
        return stash(f'<a href="{escape(url, quote=True)}" color="#6B4EFF"><u>{escape(url)}</u></a>')

    def name_sub(m: re.Match) -> str:
        return stash(f'<a name="{m.group(1)}"/>')

    work = safe(text)
    work = re.sub(r"`([^`]+)`", code_sub, work)
    work = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", work)
    work = re.sub(r"<(https?://[^>\s]+)>", angle_sub, work)
    work = re.sub(r"\[#([A-Za-z0-9_-]+)\]", name_sub, work)
    work = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_sub, work)
    work = re.sub(r"(?<![(\[])(?<![\w/])https?://[^\s\])<>\"']+", bare_url_sub, work)
    work = escape(work)
    work = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", work)
    work = re.sub(r"___(.+?)___", r"<b><i>\1</i></b>", work)
    work = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", work)
    work = re.sub(r"__(.+?)__", r"<b>\1</b>", work)
    work = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", work)
    work = balance_tags(work)
    for idx, value in enumerate(placeholders):
        work = work.replace(f"@@P{idx}@@", value)
    work = work.replace("—", "—").replace("–", "–")
    return work
