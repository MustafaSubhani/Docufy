"""Optional company intro/outro blocks (ADICC proposal pattern)."""

from __future__ import annotations

import re
from pathlib import Path

from .parser import parse_blocks

TEMPLATES = Path(__file__).resolve().parent / "templates"


def apply_company_sections(meta, blocks: list[dict]) -> list[dict]:
    intro = _load_template(meta.company_intro_path or TEMPLATES / "company_intro.md")
    outro = _load_template(meta.company_outro_path or TEMPLATES / "company_outro.md")

    if not meta.include_company_intro and not meta.include_company_outro:
        return blocks

    sections = _split_h1_sections(blocks)
    if not sections:
        merged = list(blocks)
    else:
        merged: list[dict] = []
        for i, section in enumerate(sections):
            merged.extend(section)
            if i == 0 and meta.include_company_intro and intro:
                merged.extend(intro)
        if meta.include_company_outro and outro:
            contact_idx = next(
                (i for i, b in enumerate(merged) if b.get("kind") == "contact"),
                len(merged),
            )
            merged[contact_idx:contact_idx] = outro

    return renumber_h1_sections(merged)


def _load_template(path: Path | None) -> list[dict]:
    if path is None or not Path(path).is_file():
        return []
    return parse_blocks(Path(path).read_text(encoding="utf-8").split("\n"), source_dir=Path(path).parent)


def _split_h1_sections(blocks: list[dict]) -> list[list[dict]]:
    sections: list[list[dict]] = []
    current: list[dict] = []
    for block in blocks:
        if block.get("kind") == "heading" and block.get("level") == 1:
            if current:
                sections.append(current)
            current = [block]
        else:
            current.append(block)
    if current:
        sections.append(current)
    return sections


def renumber_h1_sections(blocks: list[dict]) -> list[dict]:
    num = 0
    appendix = 0
    for block in blocks:
        if block.get("kind") != "heading" or block.get("level") != 1:
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
