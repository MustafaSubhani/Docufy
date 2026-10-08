"""Typed document model and Markdown round trip.

The PDF renderer still consumes parser dicts. ``to_blocks`` turns a document
back into that shape. Word export is component-accurate, not a pixel copy.
Inline marks (bold, links, ``[^S12]``, ``{{calc:id}}``) stay in the text.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .parser import parse_markdown
from .theme import DEFAULT_CONFIDENTIAL, DEFAULT_CONTACT, Meta

FLAG_NAMES = ("accent", "module", "compact", "ruled", "large", "unnumbered")
_SKIP = {"id", "kind", "rev", "origin", "edited_by", "locked", "variant", "style_overrides"}
_MODULE_RE = re.compile(r"^Module\s+\d+\b", re.I)
_WHY = ("why choose", "why spark")


class Block(BaseModel):
    id: str
    kind: str
    rev: int = 1
    origin: str = "import"
    edited_by: str = ""
    locked: bool = False
    variant: str = ""
    style_overrides: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


class Section(BaseModel):
    id: str
    kicker: str = ""
    title: str = ""
    kicker_flags: dict[str, bool] = Field(default_factory=dict)
    title_flags: dict[str, bool] = Field(default_factory=dict)
    title_id: str = ""
    blocks: list[Block] = Field(default_factory=list)
    page_budget: int | None = None
    source_refs: list[str] = Field(default_factory=list)
    origin: str = "import"


class Part(BaseModel):
    kind: str = "main"
    cover: dict[str, str] | None = None
    cover_id: str = ""
    sections: list[Section] = Field(default_factory=list)


class DocMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    subtitle: str = ""
    doc_type: str = "DOCUMENT"
    prepared_for: str = ""
    prepared_by: str = "Spark Technologies"
    date: str = ""
    confidential: bool = True
    confidential_text: str = DEFAULT_CONFIDENTIAL
    section_break: bool = True
    show_contact: bool = False
    footer: str = "Spark Technologies  |  Confidential"
    client: str = ""
    contact: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_CONTACT))
    include_company_intro: bool = False
    include_company_outro: bool = False
    strip_spark_content: bool = False
    company_intro_path: str = ""
    company_outro_path: str = ""
    inner_logo: bool = False
    inner_footer: bool = False
    top_bar: bool = False
    asset_roots: list[str] = Field(default_factory=list)
    archetype: str = ""
    industry: str = ""


class Doc(BaseModel):
    meta: DocMeta
    parts: list[Part] = Field(default_factory=list)
    closing: Section | None = None
    calcs: dict[str, Any] = Field(default_factory=dict)
    citations: dict[str, Any] = Field(default_factory=dict)
    assets: dict[str, Any] = Field(default_factory=dict)


def load_markdown(text: str, source_dir: Path | None = None) -> Doc:
    meta, blocks = parse_markdown(text, source_dir=source_dir)
    return document_from_parse(meta, blocks)


def document_from_parse(meta: Meta, blocks: list[dict]) -> Doc:
    """Group parser blocks into parts and sections. Ids are ``b1``, ``b2``, ... in order."""
    seq = 0

    def nid() -> str:
        nonlocal seq
        seq += 1
        return f"b{seq}"

    parts: list[Part] = []
    current: Section | None = None
    current_part: Part | None = None

    def content_part() -> Part:
        if not parts:
            parts.append(Part(kind="main"))
        return parts[-1]

    def open_section(part: Part) -> Section:
        nonlocal current, current_part
        if current is None or current_part is not part:
            current = Section(id=nid())
            part.sections.append(current)
            current_part = part
        return current

    for raw in blocks:
        kind = raw.get("kind")
        if kind == "addendum":
            fields = {str(k): str(v) for k, v in (raw.get("fields") or {}).items()}
            parts.append(Part(kind="addendum", cover=fields, cover_id=nid()))
            current = None
            current_part = None
            continue
        part = content_part()
        level = int(raw.get("level") or 0)
        if kind == "heading" and level == 1:
            current = Section(
                id=nid(),
                kicker=str(raw.get("text") or ""),
                kicker_flags=_flags(raw),
                origin=str(raw.get("origin") or "import"),
            )
            part.sections.append(current)
            current_part = part
            continue
        if (
            kind == "heading"
            and level == 2
            and current is not None
            and current_part is part
            and not current.title
            and not current.blocks
        ):
            current.title = str(raw.get("text") or "")
            current.title_flags = _flags(raw)
            current.title_id = nid()
            continue
        open_section(part).blocks.append(_block_from(raw, nid()))
    return _attach_closing(Doc(meta=DocMeta.model_validate(asdict(meta)), parts=parts))


def to_blocks(doc: Doc) -> list[dict]:
    """Parser-shaped blocks, including stable ids, for the existing renderer."""
    out: list[dict] = []
    for part in doc.parts:
        if part.kind == "addendum":
            out.append(_bookkeeping("addendum", part.cover_id, fields=dict(part.cover or {})))
        for section in part.sections:
            if section.kicker:
                out.append(
                    _heading_block(1, section.kicker, section.kicker_flags, section.id, section.origin)
                )
            if section.title:
                out.append(_heading_block(2, section.title, section.title_flags, section.title_id, "import"))
            out.extend(_block_dict(block) for block in section.blocks)
    return out


def to_spark_markdown(doc: Doc) -> str:
    lines: list[str] = []
    front = _frontmatter(doc.meta)
    if front:
        lines.extend(front.split("\n"))
    for part in doc.parts:
        lines.extend(_emit_part(part))
    return "\n".join(lines).rstrip() + "\n"


def to_gfm(doc: Doc, asset_dir: Path | None = None) -> str:
    """Plain Markdown. Diagrams become image links when ``asset_dir`` is set.

    Image links are ``{asset_dir.name}/file.png``, so the Markdown file should
    sit next to that folder.
    """
    lines: list[str] = []
    if doc.meta.title:
        lines.append(f"# {doc.meta.title}")
        if doc.meta.subtitle:
            lines.append("")
            lines.append(doc.meta.subtitle)
        lines.append("")
    for part in doc.parts:
        if part.kind == "addendum":
            cover = part.cover or {}
            lines.append(f"## {cover.get('title') or 'Addendum'}")
            lines.append("")
        for section in part.sections:
            if section.kicker:
                lines.append(f"# {section.kicker}")
                lines.append("")
            if section.title:
                lines.append(f"## {section.title}")
                lines.append("")
            lines.extend(_gfm_blocks(section.blocks, doc.meta, asset_dir))
    return "\n".join(lines).rstrip() + "\n"


def _flags(raw: dict) -> dict[str, bool]:
    return {name: bool(raw.get(name)) for name in FLAG_NAMES}


def _block_from(raw: dict, block_id: str) -> Block:
    data = {key: value for key, value in raw.items() if key not in _SKIP}
    return Block(
        id=str(raw.get("id") or block_id),
        kind=str(raw.get("kind") or "para"),
        rev=int(raw.get("rev") or 1),
        origin=str(raw.get("origin") or "import"),
        edited_by=str(raw.get("edited_by") or ""),
        locked=bool(raw.get("locked") or False),
        variant=str(raw.get("variant") or ""),
        style_overrides=dict(raw.get("style_overrides") or {}),
        data=data,
    )


def _bookkeeping(kind: str, block_id: str, **extra: Any) -> dict:
    raw = {
        "kind": kind,
        "id": block_id,
        "rev": 1,
        "origin": "import",
        "edited_by": "",
        "locked": False,
        "variant": "",
        "style_overrides": {},
    }
    raw.update(extra)
    return raw


def _heading_block(level: int, text: str, flags: dict, block_id: str, origin: str) -> dict:
    raw = _bookkeeping("heading", block_id, level=level, text=text)
    raw["origin"] = origin
    raw.update(_flags(flags))
    return raw


def _block_dict(block: Block) -> dict:
    raw = _bookkeeping(block.kind, block.id)
    raw["rev"] = block.rev
    raw["origin"] = block.origin
    raw["edited_by"] = block.edited_by
    raw["locked"] = block.locked
    raw["variant"] = block.variant
    raw["style_overrides"] = dict(block.style_overrides)
    raw.update(block.data)
    return raw


def _attach_closing(doc: Doc) -> Doc:
    for part in reversed(doc.parts):
        if not part.sections:
            continue
        last = part.sections[-1]
        blob = f"{last.kicker} {last.title}".lower()
        if any(key in blob for key in _WHY):
            doc.closing = last
        break
    return doc


def _frontmatter(meta: DocMeta) -> str:
    current = meta.model_dump()
    default = DocMeta().model_dump()
    payload = {key: value for key, value in current.items() if value != default[key]}
    if not payload:
        return ""
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=10000).strip()
    return f"---\n{body}\n---\n"


def _emit_part(part: Part) -> list[str]:
    lines: list[str] = []
    if part.kind == "addendum":
        lines.append(":::addendum")
        for key, value in (part.cover or {}).items():
            lines.append(f"{key}: {value}")
        lines.append(":::")
        lines.append("")
    for section in part.sections:
        if section.kicker:
            lines.append(_heading_md(1, section.kicker, section.kicker_flags))
            lines.append("")
        if section.title:
            lines.append(_heading_md(2, section.title, section.title_flags))
            lines.append("")
        lines.extend(_emit_many(section.blocks))
    return lines


def _heading_md(level: int, text: str, flags: dict | None) -> str:
    marks: list[str] = []
    flags = flags or {}
    for name in ("ruled", "large", "compact", "accent", "unnumbered", "module"):
        if not flags.get(name):
            continue
        if name == "module" and _MODULE_RE.match(text):
            continue
        marks.append(f".{name}")
    suffix = " {" + " ".join(marks) + "}" if marks else ""
    return f"{'#' * level} {text}{suffix}"


def _style_comment(block: Block | dict) -> str:
    if isinstance(block, Block):
        variant, overrides = block.variant, block.style_overrides
    else:
        variant, overrides = str(block.get("variant") or ""), block.get("style_overrides") or {}
    if not variant and not overrides:
        return ""
    payload = {"style_overrides": overrides, "variant": variant}
    return f"<!-- spark-style {json.dumps(payload, sort_keys=True, default=str)} -->"


def _as_dict(block: Block | dict) -> dict:
    if isinstance(block, Block):
        raw = dict(block.data)
        raw["kind"] = block.kind
        return raw
    return block


def _emit_many(blocks: list) -> list[str]:
    lines: list[str] = []
    prev = ""
    for block in blocks:
        raw = _as_dict(block)
        kind = str(raw.get("kind") or "")
        if kind == "list" and prev == "list":
            lines.append("<!-- -->")
            lines.append("")
        lines.extend(_emit_one(raw))
        comment = _style_comment(block)
        if comment:
            lines.append(comment)
        lines.append("")
        prev = kind
    return lines


def _emit_one(raw: dict) -> list[str]:
    kind = str(raw.get("kind") or "")
    if kind in {"para", "label"}:
        return [str(raw.get("text") or "")]
    if kind == "heading":
        return [_heading_md(int(raw.get("level") or 1), str(raw.get("text") or ""), raw)]
    if kind == "list":
        lines = []
        ordered = bool(raw.get("ordered"))
        for item in raw.get("items") or []:
            text = item.get("text") if isinstance(item, dict) else str(item)
            indent = "  " if isinstance(item, dict) and item.get("indent") else ""
            bullet = "1." if ordered else "-"
            lines.append(f"{indent}{bullet} {text}")
        return lines
    if kind in {"table", "roadmap", "commercial"}:
        return _emit_table(kind, raw)
    if kind == "callout":
        lines = [":::callout"]
        if raw.get("title"):
            lines.append(f"### {raw['title']}")
            lines.append("")
        lines.extend(_emit_many(raw.get("blocks") or []))
        lines.append(":::")
        return lines
    if kind in {"columns", "cards"}:
        lines = [f":::{kind}"]
        for group in raw.get(kind) or []:
            flags = {"accent": bool(group.get("accent"))}
            lines.append(_heading_md(3, str(group.get("title") or ""), flags))
            lines.append("")
            lines.extend(_emit_many(group.get("blocks") or []))
        lines.append(":::")
        return lines
    if kind == "steps":
        lines = [":::steps"]
        for step in raw.get("steps") or []:
            lines.append(f"### {step.get('title') or ''}")
            lines.append("")
            if step.get("body"):
                lines.append(str(step["body"]))
                lines.append("")
        lines.append(":::")
        return lines
    if kind == "kpis":
        lines = [":::kpis"]
        for group in raw.get("groups") or []:
            lines.append(f"### {group.get('title') or ''}")
            lines.append("")
            for item in group.get("items") or []:
                lines.append(f"- {item}")
            lines.append("")
        lines.append(":::")
        return lines
    if kind in {"tight", "large"}:
        lines = [f":::{kind}"]
        lines.extend(_emit_many(raw.get("blocks") or []))
        lines.append(":::")
        return lines
    if kind == "stageflow":
        return _emit_stageflow(raw)
    if kind == "image":
        alt = raw.get("alt") or ""
        path = raw.get("path") or ""
        line = f"![{alt}]({path})"
        if raw.get("width"):
            line += "{width=" + _num(raw["width"]) + "}"
        return [line]
    if kind in {"diagram", "code"}:
        source = str(raw.get("source") or raw.get("text") or "")
        lang = str(raw.get("lang") or ("diagram" if kind == "diagram" else ""))
        if kind == "diagram" and not source and isinstance(raw.get("spec"), dict):
            source = yaml.safe_dump(raw["spec"], sort_keys=False, allow_unicode=True).strip()
            lang = "diagram"
        return _fence(lang, source)
    if kind == "hr":
        return ["---"]
    if kind == "pagebreak":
        return ["<!-- pagebreak -->"]
    if kind == "contact":
        return [":::contact", ":::"]
    if kind == "addendum":
        lines = [":::addendum"]
        for key, value in (raw.get("fields") or {}).items():
            lines.append(f"{key}: {value}")
        lines.append(":::")
        return lines
    raise ValueError(f"unsupported block kind: {kind}")


def _emit_table(kind: str, raw: dict) -> list[str]:
    headers = list(raw.get("headers") or [])
    rows = [list(row) for row in (raw.get("rows") or [])]
    if kind == "commercial" and raw.get("total"):
        rows.append(list(raw["total"]))
    table = _markdown_table(headers, rows)
    container = kind in {"roadmap", "commercial"} or "widths" in raw or "bold_first" in raw
    if not container:
        return table
    attrs: list[str] = []
    if raw.get("widths"):
        attrs.append("widths=" + ",".join(_num(v) for v in raw["widths"]))
    if raw.get("bold_first"):
        attrs.append("bold_first")
    head = f":::{kind}" + (f" {' '.join(attrs)}" if attrs else "")
    return [head, *table, ":::"]


def _markdown_table(headers: list, rows: list) -> list[str]:
    width = max([len(headers), *[len(row) for row in rows], 1])

    def pad(row: list) -> str:
        cells = ["" if cell is None else str(cell) for cell in row]
        cells += [""] * (width - len(cells))
        return "| " + " | ".join(cells) + " |"

    lines = [pad(headers), "| " + " | ".join(["---"] * width) + " |"]
    lines.extend(pad(row) for row in rows)
    return lines


def _emit_stageflow(raw: dict) -> list[str]:
    lines = [":::stageflow"]
    for step in raw.get("steps") or []:
        layer = step.get("layer") or "process"
        title = str(step.get("title") or "")
        suffix = f" {{layer={layer}}}" if layer and layer != "process" else ""
        lines.append(f"### {title}{suffix}")
        lines.append("")
        if step.get("detail"):
            lines.append(str(step["detail"]))
            lines.append("")
        if step.get("cap_title") or step.get("cap_detail"):
            lines.append(":::callout")
            if step.get("cap_title"):
                lines.append(f"### {step['cap_title']}")
                lines.append("")
            if step.get("cap_detail"):
                lines.append(str(step["cap_detail"]))
                lines.append("")
            lines.append(":::")
            lines.append("")
    lines.append(":::")
    return lines


def _fence(lang: str, source: str) -> list[str]:
    return [f"```{lang}", *source.split("\n"), "```"]


def _num(value: Any) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


def _gfm_blocks(blocks: list[Block], meta: DocMeta, asset_dir: Path | None) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        raw = _as_dict(block)
        lines.extend(_gfm_one(raw, block.id, meta, asset_dir))
        lines.append("")
    return lines


def _gfm_one(raw: dict, block_id: str, meta: DocMeta, asset_dir: Path | None) -> list[str]:
    kind = str(raw.get("kind") or "")
    if kind == "diagram":
        return _gfm_diagram(raw, block_id, asset_dir)
    if kind == "callout":
        return _gfm_quote(raw)
    if kind in {"columns", "cards"}:
        return _gfm_groups(raw.get(kind) or [])
    if kind == "steps":
        lines = []
        for index, step in enumerate(raw.get("steps") or [], start=1):
            body = step.get("body") or ""
            line = f"{index}. **{step.get('title') or ''}**"
            if body:
                line += f" — {body}"
            lines.append(line)
        return lines
    if kind == "kpis":
        return _gfm_kpis(raw)
    if kind == "stageflow":
        return _gfm_stageflow(raw)
    if kind in {"tight", "large"}:
        return _emit_many(raw.get("blocks") or [])
    if kind == "contact":
        bits = [meta.contact.get("email"), meta.contact.get("phone"), meta.contact.get("website")]
        return [" · ".join(str(bit) for bit in bits if bit)]
    return _emit_one(raw)


def _gfm_quote(raw: dict) -> list[str]:
    body: list[str] = []
    if raw.get("title"):
        body.append(f"**{raw['title']}**")
        body.append("")
    body.extend(_emit_many(raw.get("blocks") or []))
    quoted = []
    for line in "\n".join(body).splitlines() or [""]:
        quoted.append(f"> {line}" if line else ">")
    return quoted


def _gfm_groups(groups: list) -> list[str]:
    lines: list[str] = []
    for group in groups:
        title = group.get("title") or ""
        if title:
            lines.append(f"### {title}")
            lines.append("")
        lines.extend(_emit_many(group.get("blocks") or []))
    return lines


def _gfm_kpis(raw: dict) -> list[str]:
    lines: list[str] = []
    for group in raw.get("groups") or []:
        if group.get("title"):
            lines.append(f"### {group['title']}")
            lines.append("")
        for item in group.get("items") or []:
            lines.append(f"- {item}")
        lines.append("")
    return lines


def _gfm_stageflow(raw: dict) -> list[str]:
    lines: list[str] = []
    for step in raw.get("steps") or []:
        detail = step.get("detail") or ""
        line = f"- **{step.get('title') or ''}**"
        if detail:
            line += f" — {detail}"
        lines.append(line)
        if step.get("cap_title"):
            extra = step.get("cap_detail") or ""
            cap = f"  - {step['cap_title']}"
            if extra:
                cap += f": {extra}"
            lines.append(cap)
    return lines


def _gfm_diagram(raw: dict, block_id: str, asset_dir: Path | None) -> list[str]:
    source = str(raw.get("source") or "")
    lang = str(raw.get("lang") or "diagram")
    spec = raw.get("spec")
    if asset_dir is not None and isinstance(spec, dict) and spec.get("type") != "sequence":
        try:
            import shutil

            from diagrams.render import render_png_spec

            png = render_png_spec(spec)
            asset_dir.mkdir(parents=True, exist_ok=True)
            dest = asset_dir / f"{block_id}.png"
            shutil.copyfile(png, dest)
            title = spec.get("title") or "Diagram"
            return [f"![{title}]({asset_dir.name}/{dest.name})"]
        except Exception:
            pass
    if not source and isinstance(spec, dict):
        source = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True).strip()
        lang = "diagram"
    return _fence(lang, source)
