"""Change a finished document from the chat. Each change becomes a new version.

Word swaps, the title, the client name, the company pages, and removing a
section work without a model. Rewriting text needs a model and goes through the
scoped edit engine, so blocks outside the named section keep their exact text.
The original frontmatter is kept, because the document model does not carry
every studio key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from spark_docs.ir import load_markdown, to_spark_markdown
from spark_docs.parser import claude_diagram_spec

from .blocks import split_frontmatter
from .diagramfix import repair_mermaid
from .edit import (
    EditError,
    EditRequest,
    EditScope,
    _kicker_body,
    _label,
    _sections,
    _sections_named,
    apply_edit,
    resolve_phrase,
)
from .llm import LLMError, Router

_Q = "[\"'\u2018\u2019\u201c\u201d]"
_TITLE = re.compile(
    r"^(?:please\s+)?(?:"
    r"(?:set|change|make)\s+(?:the\s+)?(?:document\s+)?title\s+(?:to\s+|as\s+)?"
    r"|rename\s+(?:the\s+)?(?:document|it)\s+(?:to\s+|as\s+)?"
    r"|(?:call|name)\s+(?:it|the\s+document)\s+"
    rf"){_Q}?(?P<title>.+?){_Q}?[.!]?$",
    re.I,
)
_CLIENT = re.compile(
    rf"^(?:please\s+)?(?:set\s+|change\s+)?(?:the\s+)?(?:client(?:\s+name)?|prepared\s+for)\s+(?:is\s+|to\s+|as\s+)?{_Q}?(?P<name>.+?){_Q}?[.!]?$",
    re.I,
)
_REPLACE = re.compile(
    rf"^(?:please\s+)?(?:replace|change|swap|rename)\s+{_Q}?(?P<old>.+?){_Q}?\s+(?:with|to|into|for)\s+{_Q}?(?P<new>.+?){_Q}?[.!]?$",
    re.I | re.S,
)
_REMOVE = re.compile(
    r"^(?:please\s+)?(?:remove|delete|drop|cut)\s+(?:the\s+)?(?:section\s+(?P<a>.+?)|(?P<b>.+?)\s+section)[.!]?$",
    re.I,
)
_INTERNAL = re.compile(
    r"\b(?:make\s+it\s+internal|internal\s+only|(?:remove|drop|hide)\s+(?:the\s+)?(?:spark|company)\s+pages|without\s+(?:the\s+)?(?:spark|company)\s+pages|turn\s+off\s+(?:the\s+)?client\s+pages)\b",
    re.I,
)
_CLIENT_PAGES = re.compile(
    r"\b(?:make\s+it\s+client\s+facing|(?:add|include|show)\s+(?:the\s+)?(?:spark|company)\s+pages|with\s+(?:the\s+)?(?:spark|company)\s+pages|turn\s+on\s+(?:the\s+)?client\s+pages)\b",
    re.I,
)
_SPARK_PAGES = ("who we are", "why choose", "solution areas", "move forward", "contact")
_WITHOUT_MODEL = (
    "Without a model I can replace words, set the title or the client, remove a section, "
    "or switch the company pages. Choose Local, Claude, or OpenAI in the model menu to rewrite text."
)


@dataclass
class Revision:
    markdown: str | None
    summary: str
    choices: list[str] = field(default_factory=list)


def revise(
    markdown: str,
    instruction: str,
    router: Router | None = None,
    *,
    section_ids: list[str] | None = None,
) -> Revision:
    text = " ".join(instruction.split()).strip()
    if not text:
        return Revision(None, "Write what should change.")
    meta, body = split_frontmatter(markdown)
    meta = dict(meta or {})

    if _INTERNAL.search(text):
        meta["include_spark_content"] = False
        return Revision(_join(meta, body), "The company pages are off.")
    if _CLIENT_PAGES.search(text):
        meta["include_spark_content"] = True
        return Revision(_join(meta, body), "The company pages are on.")
    found = _TITLE.match(text)
    if found:
        title = found.group("title").strip()
        meta["title"] = title
        return Revision(_join(meta, body), f"The title is now {title}.")
    found = _CLIENT.match(text)
    if found:
        name = found.group("name").strip()
        meta["prepared_for"] = name
        meta["client"] = name
        return Revision(_join(meta, body), f"The document is now prepared for {name}.")
    found = _REMOVE.match(text)
    if found:
        return _remove(markdown, meta, body, (found.group("a") or found.group("b") or "").strip())
    diagram = _edit_diagram(markdown, meta, text, router)
    if diagram is not None:
        return diagram
    found = _REPLACE.match(text)
    if found:
        old, new = found.group("old").strip(), found.group("new").strip()
        swapped = _replace(meta, body, old, new)
        if swapped is not None:
            return swapped
    if router is None:
        return Revision(None, _WITHOUT_MODEL)
    forced = [item.strip() for item in (section_ids or []) if item and item.strip()]
    return _rewrite(markdown, meta, text, router, section_ids=forced or None)


def section_names(markdown: str) -> list[str]:
    return [_label(section) for section in _sections(load_markdown(markdown))]


def _replace(meta: dict, body: str, old: str, new: str) -> Revision | None:
    count = body.count(old)
    for key in ("title", "subtitle", "prepared_for", "client"):
        value = meta.get(key)
        if isinstance(value, str) and old in value:
            count += value.count(old)
            meta[key] = value.replace(old, new)
    if not count:
        return None
    noun = "place" if count == 1 else "places"
    return Revision(_join(meta, body.replace(old, new)), f"Replaced {old} with {new} in {count} {noun}.")


def _remove(markdown: str, meta: dict, body: str, name: str) -> Revision:
    if any(key in name.casefold() for key in _SPARK_PAGES):
        return Revision(None, f"{name} is one of the company pages. Say make it internal to leave the company pages out.")
    doc = load_markdown(markdown)
    matches = _sections_named(doc, name)
    if not matches:
        return Revision(None, f"There is no section called {name}. {_listing(doc)}")
    if len(matches) > 1:
        labels = [_label(section) for section in matches]
        return Revision(None, f"{name} matches more than one section: {', '.join(labels)}.", labels)
    target = matches[0]
    result = apply_edit(doc, EditRequest(scope=EditScope(kind="section", ids=[target.id]), instruction="remove", intent="delete"))
    return Revision(_join(meta, _body(result.doc)), f"Removed {_label(target)}.")


_DIAGRAM = re.compile(r"\b(?:diagram|flowchart|flow chart)\b", re.I)
_DIAGRAM_VERB = re.compile(r"\b(?:rename|redraw|change|edit|update|make|move|add|remove|simplify|fix)\b", re.I)


def _edit_diagram(markdown: str, meta: dict, text: str, router: Router | None) -> Revision | None:
    """Change one diagram. A label or a direction is edited in place. A redraw is rewritten, then repaired."""
    if not (_DIAGRAM.search(text) and _DIAGRAM_VERB.search(text)):
        return None
    doc = load_markdown(markdown)
    found = [(section, block) for section in _sections(doc) for block in section.blocks if block.kind == "diagram"]
    if not found:
        return Revision(None, "There is no diagram in this document yet.")
    named = _mentioned(doc, text)
    if len(found) > 1:
        chosen = [(section, block) for section, block in found if section in named]
        if len(chosen) != 1:
            labels = [_label(section) for section, _block in found]
            return Revision(None, f"Name the section whose diagram should change: {', '.join(labels)}.", labels)
        found = chosen
    section, block = found[0]
    simple = bool(re.search(r"\b(left to right|top to bottom|rename)\b", text, re.I))
    if simple:
        result = apply_edit(
            doc,
            EditRequest(scope=EditScope(kind="diagram", ids=[block.id]), instruction=text, intent="tweak_diagram"),
        )
        if result.doc is None:
            return Revision(None, "The diagram was left as it is.")
        return Revision(_join(meta, _body(result.doc)), f"Updated the diagram in {_label(section)}.")
    if router is None:
        return Revision(None, "Say which label to rename, or ask for left to right. Choose a model to redraw the diagram.")
    trial = doc.model_copy(deep=True)
    target = next(item for sec in _sections(trial) for item in sec.blocks if item.id == block.id)
    source = str(target.data.get("source") or "")
    try:
        reply = router.complete(
            "writer",
            "Edit the mermaid flowchart so it follows the instruction. Reply with only the mermaid source, starting with flowchart. Use at most 6 nodes. Keep labels the instruction does not change.",
            f"Instruction:\n{text}\n\nDiagram:\n{source}",
            max_tokens=400,
            temperature=0.2,
        ).text
    except LLMError:
        reply = source
    fenced = re.search(r"```(?:mermaid|mmd)?\s*(.*?)```", reply or "", re.S)
    fixed = repair_mermaid(fenced.group(1) if fenced else (reply or source))
    spec = claude_diagram_spec(fixed, "mermaid") or {}
    target.data["source"] = fixed
    target.data["spec"] = spec
    target.data["lang"] = "mermaid"
    target.rev += 1
    target.edited_by = "agent"
    return Revision(_join(meta, _body(trial)), f"Updated the diagram in {_label(section)}.")


_PAGE_PREFIX = re.compile(r"^On page \d+ of the open document:\s*", re.I)


def _rewrite(
    markdown: str,
    meta: dict,
    text: str,
    router: Router,
    *,
    section_ids: list[str] | None = None,
) -> Revision:
    doc = load_markdown(markdown)
    cleaned = _PAGE_PREFIX.sub("", text).strip() or text
    if section_ids:
        scope = EditScope(kind="section", ids=section_ids)
        instruction = cleaned
    else:
        instruction = cleaned
        try:
            resolved = resolve_phrase(doc, instruction)
        except EditError as exc:
            return Revision(None, str(exc))
        scope = resolved.scope
        if scope is None and resolved.choices:
            return Revision(None, f"That matches more than one section: {', '.join(resolved.choices)}.", resolved.choices)
        if scope is None:
            named = _mentioned(doc, instruction)
            if len(named) > 1:
                labels = [_label(section) for section in named]
                return Revision(None, f"Choose one section: {', '.join(labels)}.", labels)
            if not named:
                return Revision(None, f"Name the section to change. {_listing(doc)}")
            scope = EditScope(kind="section", ids=[named[0].id])
    label = _scope_label(doc, scope)
    try:
        result = apply_edit(doc, EditRequest(scope=scope, instruction=instruction, intent=_intent(instruction)), router)
    except (EditError, LLMError) as exc:
        return Revision(None, f"That change was not made. {exc}")
    if result.status == "locked" or result.doc is None:
        return Revision(None, f"{label} is locked or was edited by hand, so it was left as it is.")
    verb = {"shorten": "Shortened", "expand": "Expanded", "tone": "Changed the tone of", "translate": "Translated"}.get(
        _intent(instruction), "Rewrote"
    )
    return Revision(_join(meta, _body(result.doc)), f"{verb} {label}.")


def _intent(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(shorten|shorter|trim|tighten|condense|concise|brief)\b", lowered):
        return "shorten"
    if re.search(r"\b(expand|longer|elaborate|more detail|add detail)\b", lowered):
        return "expand"
    if re.search(r"\b(tone|formal|friendly|confident|warmer|plainer|simpler)\b", lowered):
        return "tone"
    if re.search(r"\btranslate\b", lowered):
        return "translate"
    return "rewrite"


def _mentioned(doc, text: str) -> list:
    lowered = text.casefold()
    found = []
    for section in _sections(doc):
        names = {section.title.casefold(), _kicker_body(section.kicker).strip().casefold()}
        if any(name and len(name) > 2 and name in lowered for name in names):
            found.append(section)
    return found


def _scope_label(doc, scope: EditScope) -> str:
    if scope.kind == "section":
        wanted = set(scope.ids)
        labels = [_label(section) for section in _sections(doc) if section.id in wanted]
        if len(labels) == 1:
            return labels[0]
        if labels:
            return ", ".join(labels)
    return "that part"


def _listing(doc) -> str:
    names = [_label(section) for section in _sections(doc)]
    return f"This document has {', '.join(names)}." if names else "This document has no sections yet."


def _body(doc) -> str:
    _meta, body = split_frontmatter(to_spark_markdown(doc))
    return body


def _join(meta: dict, body: str) -> str:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10000).strip()
    return f"---\n{front}\n---\n\n{body.lstrip()}"
