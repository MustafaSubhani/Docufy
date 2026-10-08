"""Spark company content (intro, Why Choose Us, contact banners) and section numbering.

The "Include Spark content" switch:
  - include_company_intro  -> About Spark Technologies (Who We Are + Solution Areas) before the first section
  - include_company_outro  -> Why Spark Technologies page (Why Choose Us + contact) at the very end
  - strip_spark_content    -> remove company sections and contact banners already in the source
Blocks inserted from templates are tagged origin=spark_content.
"""

from __future__ import annotations

import re
from pathlib import Path

from .brand import apply_archetype_defaults, boilerplate_blocks, industry_of
from .parser import parse_blocks

SPARK_SECTION_RE = re.compile(r"(about spark|why spark|who we are|why choose us)", re.I)


def apply_company_sections(meta, blocks: list[dict]) -> list[dict]:
    apply_archetype_defaults(meta)
    out = list(blocks)
    if getattr(meta, "strip_spark_content", False):
        out = strip_spark_content(out)
    has_intro = any(is_heading(b, "who we are") for b in out)
    has_outro = any(is_heading(b, "why choose us") for b in out)
    changed = False

    if meta.include_company_intro and not has_intro:
        intro = tag(fill_placeholders(_intro_blocks(meta), meta))
        out = intro + out
        changed = True

    if meta.include_company_outro and not has_outro:
        outro = tag(fill_placeholders(_outro_blocks(meta), meta))
        # A contact that closes How We Move Forward stays. Only a dangling end banner
        # is dropped, because the Why Choose Us page brings its own.
        if (
            meta.show_contact
            and out
            and out[-1].get("kind") == "contact"
            and any(b.get("kind") == "contact" for b in outro)
            and not any(b.get("kind") == "steps" for b in out[-6:])
        ):
            out = out[:-1]
        out = out + outro
        changed = True

    return renumber_h1_sections(out) if changed or getattr(meta, "strip_spark_content", False) else out


def is_heading(block: dict, text: str) -> bool:
    return block.get("kind") == "heading" and text in (block.get("text") or "").lower()


def tag(blocks: list[dict]) -> list[dict]:
    for b in blocks:
        b["origin"] = "spark_content"
    return blocks


def client_name(meta) -> str:
    return (meta.client or meta.prepared_for or "").strip()


def fill_placeholders(blocks: list[dict], meta) -> list[dict]:
    name = client_name(meta)
    values = {
        "client": name or "The client",
        "client_possessive": f"{name}'s" if name else "the client's",
    }

    def sub(text: str) -> str:
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: values.get(m.group(1), m.group(0)), text)

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return sub(node)
        return node

    return [walk(b) for b in blocks]


def strip_spark_content(blocks: list[dict]) -> list[dict]:
    """Drop company sections (About/Why Spark, Who We Are, Why Choose Us) and contact banners."""
    out: list[dict] = []
    skipping = False
    for b in blocks:
        if b.get("kind") == "heading" and b.get("level") == 1:
            skipping = bool(SPARK_SECTION_RE.search(b.get("text") or "")) or b.get("origin") == "spark_content"
        if skipping or b.get("kind") == "contact" or b.get("origin") == "spark_content":
            continue
        out.append(b)
    return out


def _intro_blocks(meta) -> list[dict]:
    if meta.company_intro_path:
        return _load_template(meta.company_intro_path)
    return boilerplate_blocks("company_intro")


def _outro_blocks(meta) -> list[dict]:
    if meta.company_outro_path:
        return _load_template(meta.company_outro_path)
    return boilerplate_blocks(f"why_choose_us/{industry_of(meta)}")


def _load_template(path: Path | str | None) -> list[dict]:
    if path is None or not Path(path).is_file():
        return []
    p = Path(path)
    return parse_blocks(p.read_text(encoding="utf-8").split("\n"), source_dir=p.parent)


def renumber_h1_sections(blocks: list[dict]) -> list[dict]:
    num = 0
    appendix = 0
    for block in blocks:
        if block.get("kind") == "addendum":
            appendix = 0
        if block.get("kind") != "heading" or block.get("level") != 1 or block.get("unnumbered"):
            continue
        text = (block.get("text") or "").strip()
        app = re.match(r"^A(\d+)[\.\):\-–—]?\s+(.*)$", text, re.I)
        numbered = re.match(r"^0?(\d+)[\.\):\-–—]?\s+(.*)$", text)
        if app:
            appendix += 1
            block["text"] = f"A{appendix}  {app.group(2).strip()}"
        elif numbered:
            num += 1
            block["text"] = f"{num:02d}  {numbered.group(2).strip()}"
        else:
            num += 1
            block["text"] = f"{num:02d}  {text}"
    return blocks
