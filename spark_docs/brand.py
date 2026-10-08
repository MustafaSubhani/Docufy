"""Spark brand pack: facts, boilerplate, archetypes, and the UK lexicon.

Content lives in ``brand/spark``. The theme pack stays responsible for the look.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from .parser import parse_blocks
from .theme import DEFAULT_CONFIDENTIAL

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "brand" / "spark"
BOILERPLATE = BRAND / "boilerplate"

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


@lru_cache(maxsize=1)
def load_facts() -> dict:
    return yaml.safe_load((BRAND / "facts.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_lexicon() -> dict:
    return yaml.safe_load((BRAND / "lexicon.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_archetypes() -> dict:
    """Archetypes keyed by id, with aliases pointing at the same record."""
    found: dict = {}
    for path in sorted((BRAND / "archetypes").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        name = str(data.get("id") or path.stem)
        data["id"] = name
        found[name] = data
        for alias in data.get("aliases") or []:
            found[str(alias)] = data
    return found


def facts_contact() -> dict:
    company = load_facts()["company"]
    return {key: company[key] for key in ("email", "phone", "website", "address")}


def internal_confidential() -> str:
    return str(load_facts()["confidential"]["internal"]).strip()


def boilerplate_text(name: str) -> str:
    if name == "company_intro":
        who = (BOILERPLATE / "who_we_are.md").read_text(encoding="utf-8").rstrip()
        areas = (BOILERPLATE / "solution_areas.md").read_text(encoding="utf-8").rstrip()
        return f"{who}\n\n{areas}\n"
    path = BOILERPLATE.joinpath(*name.split("/"))
    if path.suffix != ".md":
        path = path.with_suffix(".md")
    return path.read_text(encoding="utf-8")


def boilerplate_blocks(name: str) -> list[dict]:
    text = boilerplate_text(name)
    return parse_blocks(text.split("\n"), source_dir=BOILERPLATE)


def industry_of(meta) -> str:
    chosen = getattr(meta, "industry", "") or ""
    if chosen:
        return chosen
    arch = load_archetypes().get(getattr(meta, "archetype", "") or "")
    if arch and arch.get("industry"):
        return str(arch["industry"])
    return "construction"


def archetype_prompt() -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for key, arch in load_archetypes().items():
        ident = str(arch.get("id") or key)
        if ident in seen:
            continue
        seen.add(ident)
        slots = [
            slot.get("kicker")
            for slot in arch.get("slots") or []
            if slot.get("kicker") and not slot.get("spark_content")
        ]
        slot_text = f" Slots: {', '.join(slots)}." if slots else ""
        lines.append(f"- {ident} (doc_type {arch.get('doc_type')}): {arch.get('description', '')}{slot_text}")
    return "\n".join(lines)


def apply_archetype_defaults(meta) -> None:
    """Fill spark-content and confidentiality from the archetype when the file did not set them."""
    arch = load_archetypes().get(getattr(meta, "archetype", "") or "")
    if not arch or getattr(meta, "spark_content_explicit", False):
        return
    if meta.doc_type == "DOCUMENT" and arch.get("doc_type"):
        meta.doc_type = str(arch["doc_type"])
    on = bool(arch.get("spark_content"))
    meta.include_company_intro = on
    meta.include_company_outro = on
    if not getattr(meta, "industry", "") and arch.get("industry"):
        meta.industry = str(arch["industry"])
    if on:
        return
    meta.show_contact = False
    meta.strip_spark_content = True
    if meta.confidential_text == DEFAULT_CONFIDENTIAL:
        meta.confidential_text = internal_confidential()


def contact_issues(contact: dict | None) -> list[str]:
    expected = facts_contact()
    issues = []
    for key, value in expected.items():
        got = (contact or {}).get(key)
        if got and got != value:
            issues.append(f"contact {key} is {got!r}; brand pack says {value!r}")
    return issues


def lexicon_issues(text: str) -> list[str]:
    avoid = {}
    for item in load_lexicon().get("prefer") or []:
        avoid[str(item["avoid"]).lower()] = str(item["use"])
    found: list[str] = []
    seen: set[str] = set()
    for match in _WORD.finditer(text):
        word = match.group(0)
        preferred = avoid.get(word.lower())
        if preferred and word.lower() not in seen:
            seen.add(word.lower())
            found.append(f"use {preferred!r} instead of {word!r}")
    return found
