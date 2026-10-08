"""Scoped edits. The model may rewrite the target. Code rejects anything else.

A block outside the scope must keep its hash. Text-range edits leave the rest of
the paragraph byte-identical. Locks and hand edits are skipped unless the user
named that block. Ripple checks come back as suggestions, not extra edits.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from spark_docs.ir import Block, Doc, Section, load_markdown, to_spark_markdown

from .llm import Router

Intent = Literal[
    "rewrite",
    "shorten",
    "expand",
    "fit_page",
    "tone",
    "fix_facts",
    "refresh_research",
    "recompute",
    "convert_component",
    "regenerate_diagram",
    "tweak_diagram",
    "replace_image",
    "generate_image",
    "insert_after",
    "delete",
    "translate",
    "restyle",
]
ScopeKind = Literal["section", "block", "text_range", "items", "table_cells", "diagram", "diagram_nodes", "region"]
_LATER = {"refresh_research", "convert_component", "replace_image", "generate_image"}
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_WEEK = re.compile(r"\b(?:Module\s+\d+|Week\s+\d+(?:\s*[-–]\s*\d+)?)\b", re.I)
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
_KICKER_NO = re.compile(r"^(\d+)(\b.*)$")


class EditError(RuntimeError):
    pass


class ScopeError(EditError):
    pass


class EditScope(BaseModel):
    kind: ScopeKind
    ids: list[str]
    start: int | None = None
    end: int | None = None
    page: int | None = None
    bbox: list[float] | None = None


class ImageRef(BaseModel):
    path: str = ""
    role: Literal["reference", "preview", "photo"] = "reference"
    page: int | None = None
    page_image: str = ""


class EditRequest(BaseModel):
    scope: EditScope
    instruction: str
    intent: Intent = "rewrite"
    attachments: list[ImageRef] = []
    apply_to: Literal["block", "section", "all_of_type", "save_as_variant"] = "block"
    variants: int = 1
    brand_lock: bool = True


@dataclass
class Suggestion:
    block_id: str
    message: str


@dataclass
class EditResult:
    status: str
    doc: Doc | None = None
    variants: list[Doc] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    renumbered: list[str] = field(default_factory=list)
    diff: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class ResolveResult:
    scope: EditScope | None
    instruction: str
    choices: list[str] = field(default_factory=list)


def apply_edit(doc: Doc, request: EditRequest, router: Router | None = None) -> EditResult:
    """Return variant documents. The caller commits the one the user accepts."""
    if request.intent == "restyle":
        from .style import restyle

        return restyle(doc, request, router)
    if request.apply_to == "save_as_variant" or request.intent in _LATER:
        raise EditError(f"{request.intent if request.intent in _LATER else request.apply_to} is not available yet")
    request = request.model_copy(deep=True)
    request.variants = min(3, max(1, request.variants))
    if request.intent in {"tweak_diagram", "regenerate_diagram"} or request.scope.kind in {"diagram", "diagram_nodes"}:
        return _diagram_edit(doc, request)
    if request.intent == "delete":
        return _delete(doc, request)
    if request.intent == "insert_after":
        return _insert(doc, request, router)
    targets, skipped = _targets(doc, request)
    if not targets:
        if skipped:
            return EditResult("locked", doc, skipped=skipped)
        raise EditError("nothing to edit; the target is missing or locked")
    if router is None:
        raise EditError("this edit needs a model")
    allowed = {item.block_id for item in targets}
    variants: list[Doc] = []
    for index in range(1, request.variants + 1):
        trial = doc.model_copy(deep=True)
        for item in targets:
            _rewrite(trial, doc, item, request, router, index)
        violations = scope_violations(doc, trial, allowed)
        if violations:
            raise ScopeError("edit changed blocks outside its scope: " + ", ".join(violations))
        variants.append(trial)
    chosen = variants[0]
    return EditResult(
        "ok",
        chosen,
        variants,
        ripple(doc, chosen, allowed),
        skipped=skipped,
        diff=_diff(doc, chosen, allowed),
    )


def accept(result: EditResult, index: int = 1) -> Doc:
    if not result.variants:
        raise EditError("there is no edit to accept")
    if index < 1 or index > len(result.variants):
        raise EditError(f"variant {index} is not one of {len(result.variants)}")
    return result.variants[index - 1]


def scope_violations(before: Doc, after: Doc, allowed: set[str], renumbered: set[str] | None = None) -> list[str]:
    """Ids whose content changed and were not in the scope. Leading section numbers may change after a delete."""
    renumbered = renumbered or set()
    problems = []
    after_sections = {section.id: section for section in _sections(after)}
    for section in _sections(before):
        other = after_sections.get(section.id)
        if other is None:
            if section.id not in allowed:
                problems.append(section.id)
            continue
        if section.id not in allowed and not _same_heading(section, other, section.id in renumbered):
            problems.append(section.id)
        after_blocks = {block.id: block for block in other.blocks}
        for block in section.blocks:
            if block.id in allowed:
                continue
            gone = after_blocks.get(block.id)
            if gone is None or _hash(block) != _hash(gone):
                problems.append(block.id)
    return problems


def ripple(before: Doc, after: Doc, allowed: set[str]) -> list[Suggestion]:
    """Places outside the scope that still quote a number or name the edit changed."""
    old = _scope_text(before, allowed)
    new = _scope_text(after, allowed)
    stale = (_mentions(old) - _mentions(new))
    found: list[Suggestion] = []
    for block in _blocks(after):
        if block.id in allowed or not stale:
            continue
        text = _plain(block)
        hits = [item for item in stale if item.lower() in text.lower()]
        if hits:
            found.append(Suggestion(block.id, f"still mentions {', '.join(hits)}. Update it too?"))
    return found


def resolve_phrase(doc: Doc, phrase: str) -> ResolveResult:
    """Map a sentence to one scope. Several matches come back as choices and do not edit."""
    text = phrase.strip().rstrip(".")
    named = re.search(r"\bblock\s+(b\d+)\b", text, re.I)
    if named:
        return ResolveResult(EditScope(kind="block", ids=[named.group(1)]), text)
    sentences = re.search(r"\bsentences?\s+(\d+)\s+to\s+(\d+)\s+of\s+(.+)$", text, re.I)
    if sentences:
        return _sentence_scope(doc, text, int(sentences.group(1)), int(sentences.group(2)), sentences.group(3).strip())
    paragraph = re.search(
        r"\b(first|second|third|fourth|\d+(?:st|nd|rd|th)?)\s+paragraph\s+of\s+(.+)$",
        text,
        re.I,
    )
    if paragraph:
        return _paragraph_scope(doc, text, _ordinal(paragraph.group(1)), paragraph.group(2).strip())
    section = re.search(r"\bsection\s+(.+)$", text, re.I)
    if section:
        return _section_scope(doc, text, section.group(1).strip())
    return ResolveResult(None, text)


def locate_quote(doc: Doc, quote: str) -> ResolveResult:
    hits: list[tuple[str, int, int]] = []
    for block in _blocks(doc):
        found = _find_quote(_plain(block), quote)
        if found:
            hits.append((block.id, found[0], found[1]))
    if not hits:
        return ResolveResult(None, quote)
    if len(hits) > 1:
        return ResolveResult(None, quote, [f"{bid} [{start}:{end}]" for bid, start, end in hits])
    bid, start, end = hits[0]
    return ResolveResult(EditScope(kind="text_range", ids=[bid], start=start, end=end), quote)


def region_scope(layout: dict, page: int, bbox: list[float]) -> EditScope:
    ids = []
    for block in layout.get("blocks") or []:
        if int(block.get("page") or 0) != page:
            continue
        box = [float(block.get("x") or 0), float(block.get("y") or 0), float(block.get("w") or 0), float(block.get("h") or 0)]
        if _overlap(box, bbox):
            ids.append(str(block["id"]))
    return EditScope(kind="region", ids=ids, page=page, bbox=list(bbox))


def lock_block(doc: Doc, block_id: str, locked: bool = True) -> None:
    _must_block(doc, block_id).locked = locked


def manual_edit(doc: Doc, block_id: str, text: str) -> None:
    block = _must_block(doc, block_id)
    if block.kind == "para":
        block.data["text"] = text
    elif block.kind == "list":
        block.data["items"] = [{"text": line, "indent": 0} for line in text.splitlines() if line.strip()]
    else:
        block.data["text"] = text
    block.edited_by = "user"
    block.rev += 1


def tweak_spec(spec: dict, node_ids: list[str], updates: dict[str, dict] | None = None, direction: str | None = None) -> dict:
    """Pin every node the edit does not change so ELK keeps those positions."""
    spec = json.loads(json.dumps(spec))
    updates = updates or {}
    chosen = set(node_ids)
    for index, node in enumerate(spec.get("nodes") or []):
        if chosen and node.get("id") in chosen:
            node.pop("pin_x", None)
            node.pop("pin_y", None)
            node.update(updates.get(node["id"]) or {})
            continue
        node["pin_x"] = float(node.get("pin_x") if node.get("pin_x") is not None else index * 160)
        node["pin_y"] = float(node.get("pin_y") if node.get("pin_y") is not None else 40)
    if direction:
        spec["direction"] = direction
    spec["layout"] = "tweak"
    return spec


def remake_spec(spec: dict, node_ids: list[str], updates: dict[str, dict] | None = None, direction: str | None = None) -> dict:
    """Drop pins and lay the diagram out again."""
    spec = json.loads(json.dumps(spec))
    updates = updates or {}
    chosen = set(node_ids)
    for node in spec.get("nodes") or []:
        node.pop("pin_x", None)
        node.pop("pin_y", None)
        if not chosen or node.get("id") in chosen:
            node.update(updates.get(node["id"]) or {})
    if direction:
        spec["direction"] = direction
    spec["layout"] = "auto"
    return spec


class History:
    """Append-only patch log. Undo and redo append a cursor move; they do not delete entries."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.events: list[dict] = []
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.events.append(json.loads(line))

    def current(self) -> tuple[Doc | None, str]:
        cursor, stack = self._cursor()
        if cursor is None:
            return None, ""
        version = stack[cursor]
        for event in self.events:
            if event.get("event") == "commit" and event.get("version") == version:
                return Doc.model_validate(event["doc"]), str(event.get("written") or "")
        return None, ""

    def commit(self, doc: Doc, written: str, note: str) -> None:
        cursor, stack = self._cursor()
        if cursor is None:
            self._write({"event": "commit", "version": 0, "index": 0, "stack": [0], "doc": doc.model_dump(), "written": written, "note": note})
            return
        version = max(event.get("version", 0) for event in self.events if event.get("event") == "commit") + 1
        kept = stack[: cursor + 1] + [version]
        self._write(
            {
                "event": "commit",
                "version": version,
                "index": len(kept) - 1,
                "stack": kept,
                "doc": doc.model_dump(),
                "written": written,
                "note": note,
            }
        )

    def undo(self) -> Doc:
        cursor, stack = self._cursor()
        if cursor is None or cursor == 0:
            raise EditError("nothing to undo")
        self._write({"event": "undo", "index": cursor - 1, "stack": stack})
        doc, _ = self.current()
        assert doc is not None
        return doc

    def redo(self) -> Doc:
        cursor, stack = self._cursor()
        if cursor is None or cursor >= len(stack) - 1:
            raise EditError("nothing to redo")
        self._write({"event": "redo", "index": cursor + 1, "stack": stack})
        doc, _ = self.current()
        assert doc is not None
        return doc

    def _cursor(self) -> tuple[int | None, list[int]]:
        if not self.events:
            return None, []
        last = self.events[-1]
        return int(last["index"]), [int(item) for item in last["stack"]]

    def _write(self, event: dict) -> None:
        self.events.append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")


def read_doc(path: Path) -> tuple[Doc, History]:
    path = Path(path)
    history = History(_log_path(path))
    disk = path.read_text(encoding="utf-8") if path.is_file() else ""
    snap, written = history.current()
    if snap is not None and written == disk:
        return snap, history
    doc = load_markdown(disk) if disk else Doc(meta={})
    if snap is not None:
        _copy_locks(snap, doc)
    return doc, history


def write_doc(path: Path, doc: Doc, history: History, note: str, before: Doc | None = None, before_text: str = "") -> str:
    """Write markdown and append the edit. The first save also stores the previous version so undo can restore it."""
    text = to_spark_markdown(doc)
    if not history.events and before is not None:
        history.commit(before, before_text, "baseline")
    path = Path(path)
    path.write_text(text, encoding="utf-8")
    history.commit(doc, text, note)
    return text


def _log_path(path: Path) -> Path:
    return path.with_name(path.name + ".edits.jsonl")


# --- internals -----------------------------------------------------------------


@dataclass
class _Target:
    block_id: str
    start: int | None = None
    end: int | None = None
    item: int | None = None
    cell: tuple[int, int] | None = None


def _targets(doc: Doc, request: EditRequest) -> tuple[list[_Target], list[str]]:
    scope = request.scope
    explicit = set(scope.ids) if scope.kind in {"block", "text_range", "items", "table_cells"} else set()
    if request.apply_to == "all_of_type" and scope.ids:
        kind = _must_block(doc, scope.ids[0]).kind
        scope = scope.model_copy(update={"ids": [block.id for block in _blocks(doc) if block.kind == kind], "kind": "block"})
        explicit = set()
    skipped: list[str] = []
    targets: list[_Target] = []
    if scope.kind == "section":
        for section in _match_sections(doc, scope.ids):
            for block in section.blocks:
                if _hands_off(block, explicit):
                    skipped.append(block.id)
                    continue
                targets.append(_Target(block.id))
    elif scope.kind == "region":
        for bid in scope.ids:
            block = _must_block(doc, bid)
            if _hands_off(block, set()):
                skipped.append(bid)
                continue
            targets.append(_Target(bid))
    elif scope.kind == "text_range":
        targets.append(_Target(scope.ids[0], scope.start, scope.end))
    elif scope.kind == "items":
        block_id, index = _item_ref(scope.ids[0])
        targets.append(_Target(block_id, item=index))
    elif scope.kind == "table_cells":
        block_id, row, col = _cell_ref(scope.ids[0])
        targets.append(_Target(block_id, cell=(row, col)))
    else:
        for bid in scope.ids:
            targets.append(_Target(bid))
    return targets, skipped


def _rewrite(trial: Doc, before: Doc, item: _Target, request: EditRequest, router: Router, variant: int) -> None:
    block = _must_block(trial, item.block_id)
    original = _must_block(before, item.block_id)
    if item.item is not None:
        old = str(original.data["items"][item.item].get("text") or "")
        new = _writer(router, old, request.instruction, variant, "list item")
        items = [dict(entry) for entry in block.data.get("items") or []]
        items[item.item]["text"] = new.strip()
        if items != [dict(entry) for entry in original.data.get("items") or []]:
            others = [i for i in range(len(items)) if i != item.item and items[i] != original.data["items"][i]]
            if others:
                raise ScopeError("list edit changed another item")
        block.data["items"] = items
    elif item.cell is not None:
        row, col = item.cell
        old = str(original.data["rows"][row][col])
        new = _writer(router, old, request.instruction, variant, "table cell")
        rows = [list(entry) for entry in block.data.get("rows") or []]
        rows[row][col] = new.strip()
        block.data["rows"] = rows
    elif item.start is not None and item.end is not None:
        old = str(original.data.get("text") or "")
        start, end = item.start, item.end
        replacement = _writer(router, old[start:end], request.instruction, variant, "text range")
        block.data["text"] = _splice(old, start, end, replacement)
        if not (block.data["text"].startswith(old[:start]) and block.data["text"].endswith(old[end:])):
            raise ScopeError("text-range edit changed the rest of the paragraph")
    else:
        old = _plain(original)
        new = _writer(router, old, request.instruction, variant, original.kind)
        if original.kind == "list":
            block.data["items"] = [{"text": line, "indent": 0} for line in new.splitlines() if line.strip()]
        else:
            block.data["text"] = new.strip()
    block.rev += 1
    block.edited_by = "agent"


def _writer(router: Router, target: str, instruction: str, variant: int, label: str) -> str:
    system = (
        "You edit one target inside a Spark Technologies document. "
        "Return only the replacement for that target. Do not include frontmatter, other sections, or commentary."
    )
    user = f"VARIANT {variant}\nKIND: {label}\nINSTRUCTION: {instruction}\n\nTARGET:\n{target}"
    from .memory import watchout

    user += watchout(component=label, stage="edit")
    text = _limited(router, system, user)
    if _spilled(text):
        text = _limited(router, system, user + "\n\nThe previous reply was a whole document and was rejected. Return only the replacement.")
    if _spilled(text):
        raise ScopeError("the model returned a whole document instead of the target")
    return text


def _limited(router: Router, system: str, user: str) -> str:
    return router.complete("writer", system, user, temperature=0.2).text.strip()


def _spilled(text: str) -> bool:
    return text.startswith("---") or "\n# " in text


def _splice(old: str, start: int, end: int, replacement: str) -> str:
    prefix, suffix = old[:start], old[end:]
    repl = replacement.strip("\n")
    if repl.startswith(prefix) and repl.endswith(suffix) and len(repl) >= len(prefix) + len(suffix):
        return repl
    return prefix + repl + suffix


def _delete(doc: Doc, request: EditRequest) -> EditResult:
    trial = doc.model_copy(deep=True)
    remove = set(request.scope.ids)
    for part in trial.parts:
        kept = []
        for section in part.sections:
            if section.id in remove:
                continue
            section.blocks = [block for block in section.blocks if block.id not in remove]
            kept.append(section)
        part.sections = kept
    renumbered = _renumber(trial)
    allowed = set(remove) | set(renumbered)
    violations = scope_violations(doc, trial, allowed, set(renumbered))
    if violations:
        raise ScopeError("delete changed blocks outside its scope: " + ", ".join(violations))
    return EditResult("ok", trial, [trial], renumbered=renumbered, diff=_diff(doc, trial, remove))


def _insert(doc: Doc, request: EditRequest, router: Router | None) -> EditResult:
    if router is None:
        raise EditError("this edit needs a model")
    if not request.scope.ids:
        raise EditError("insert needs a block to follow")
    trial = doc.model_copy(deep=True)
    anchor = request.scope.ids[0]
    text = _writer(router, "", request.instruction, 1, "new paragraph").strip()
    fresh = Block(id=_next_id(trial), kind="para", origin="edit", edited_by="agent", data={"text": text})
    for section in _sections(trial):
        for index, block in enumerate(section.blocks):
            if block.id == anchor:
                section.blocks.insert(index + 1, fresh)
                violations = scope_violations(doc, trial, {fresh.id})
                if violations:
                    raise ScopeError("insert changed blocks outside its scope: " + ", ".join(violations))
                return EditResult("ok", trial, [trial], diff=_diff(doc, trial, {fresh.id}))
    raise EditError(f"no block {anchor}")


def _diagram_edit(doc: Doc, request: EditRequest) -> EditResult:
    block_id = request.scope.ids[0]
    node_ids = request.scope.ids[1:]
    trial = doc.model_copy(deep=True)
    block = _must_block(trial, block_id)
    spec = _spec_of(block)
    direction, updates = _diagram_instruction(request.instruction, node_ids, spec.get("nodes") or [])
    if updates:
        node_ids = list(dict.fromkeys([*node_ids, *updates.keys()]))
    if request.intent == "regenerate_diagram":
        spec = remake_spec(spec, node_ids, updates, direction)
    else:
        spec = tweak_spec(spec, node_ids, updates, direction)
    block.data["spec"] = spec
    import yaml

    block.data["source"] = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True).strip()
    block.data["lang"] = "diagram"
    block.rev += 1
    block.edited_by = "agent"
    violations = scope_violations(doc, trial, {block_id})
    if violations:
        raise ScopeError("diagram edit changed blocks outside its scope: " + ", ".join(violations))
    return EditResult("ok", trial, [trial], diff=_diff(doc, trial, {block_id}))


def _diagram_instruction(instruction: str, node_ids: list[str], nodes: list | None = None) -> tuple[str | None, dict[str, dict]]:
    text = instruction.lower()
    direction = "LR" if "left to right" in text else "TB" if "top to bottom" in text else None
    updates: dict[str, dict] = {}
    renamed = re.search(r"\brename\s+(.+?)\s+to\s+(.+)$", instruction.strip(), re.I)
    if renamed:
        name = renamed.group(1).strip()
        title = re.sub(r"\s+on the diagram$", "", renamed.group(2).strip(), flags=re.I).rstrip(".")
        node_id = name
        for node in nodes or []:
            label = str(node.get("title") or "")
            if name.casefold() in {str(node.get("id") or "").casefold(), label.casefold()}:
                node_id = str(node.get("id") or name)
                break
        updates[node_id] = {"title": title}
    elif node_ids and instruction.strip():
        updates[node_ids[0]] = {"title": instruction.strip()}
    return direction, updates


def _renumber(doc: Doc) -> list[str]:
    changed = []
    number = 0
    for section in _sections(doc):
        match = _KICKER_NO.match(section.kicker or "")
        if not match:
            continue
        number += 1
        updated = f"{number:02d}{match.group(2)}"
        if updated != section.kicker:
            section.kicker = updated
            changed.append(section.id)
    return changed


def _hands_off(block: Block, explicit: set[str]) -> bool:
    if block.id in explicit:
        return False
    return bool(block.locked or block.edited_by == "user")


def _same_heading(before: Section, after: Section, numbers_only: bool) -> bool:
    if before.title != after.title or before.kicker_flags != after.kicker_flags:
        return False
    if before.kicker == after.kicker:
        return True
    if not numbers_only:
        return False
    return _kicker_body(before.kicker) == _kicker_body(after.kicker)


def _kicker_body(kicker: str) -> str:
    match = _KICKER_NO.match(kicker or "")
    return match.group(2) if match else kicker


def _hash(block: Block) -> str:
    payload = {"kind": block.kind, "data": block.data, "variant": block.variant, "style_overrides": block.style_overrides}
    return json.dumps(payload, sort_keys=True, default=str)


def _sections(doc: Doc) -> list[Section]:
    return [section for part in doc.parts for section in part.sections]


def _blocks(doc: Doc) -> list[Block]:
    return [block for section in _sections(doc) for block in section.blocks]


def _must_block(doc: Doc, block_id: str) -> Block:
    for block in _blocks(doc):
        if block.id == block_id:
            return block
    raise EditError(f"no block {block_id}")


def _plain(block: Block) -> str:
    if block.kind == "list":
        return "\n".join(str(item.get("text") or "") for item in block.data.get("items") or [])
    if block.kind == "table":
        rows = block.data.get("rows") or []
        return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)
    return str(block.data.get("text") or "")


def _scope_text(doc: Doc, allowed: set[str]) -> str:
    return "\n".join(_plain(block) for block in _blocks(doc) if block.id in allowed)


def _mentions(text: str) -> set[str]:
    found = {match.group(0) for match in _WEEK.finditer(text)}
    found |= {match.group(0) for match in _NUM.finditer(text) if not re.fullmatch(r"0?\d", match.group(0))}
    return found


def _match_sections(doc: Doc, ids: list[str]) -> list[Section]:
    wanted = {item.casefold() for item in ids}
    found = []
    for section in _sections(doc):
        label = f"{section.kicker} {section.title}".casefold()
        if section.id in ids or any(item in label or item == section.kicker.casefold() or item == section.title.casefold() for item in wanted):
            found.append(section)
    if not found:
        raise EditError("no section matches " + ", ".join(ids))
    return found


def _paragraph_scope(doc: Doc, phrase: str, index: int, name: str) -> ResolveResult:
    sections = _sections_named(doc, name)
    if len(sections) != 1:
        return ResolveResult(None, phrase, [_label(section) for section in sections])
    paras = [block for block in sections[0].blocks if block.kind == "para"]
    if index < 1 or index > len(paras):
        raise EditError(f"{_label(sections[0])} has {len(paras)} paragraphs")
    return ResolveResult(EditScope(kind="block", ids=[paras[index - 1].id]), phrase)


def _sentence_scope(doc: Doc, phrase: str, start: int, end: int, name: str) -> ResolveResult:
    sections = _sections_named(doc, name)
    if len(sections) != 1:
        return ResolveResult(None, phrase, [_label(section) for section in sections])
    paras = [block for block in sections[0].blocks if block.kind == "para"]
    if len(paras) != 1:
        return ResolveResult(None, phrase, [f"{block.id}: {_plain(block)[:40]}" for block in paras])
    spans = _sentences(_plain(paras[0]))
    if start < 1 or end > len(spans) or start > end:
        raise EditError(f"that paragraph has {len(spans)} sentences")
    return ResolveResult(EditScope(kind="text_range", ids=[paras[0].id], start=spans[start - 1][0], end=spans[end - 1][1]), phrase)


def _section_scope(doc: Doc, phrase: str, name: str) -> ResolveResult:
    sections = _sections_named(doc, name)
    if len(sections) != 1:
        return ResolveResult(None, phrase, [_label(section) for section in sections])
    return ResolveResult(EditScope(kind="section", ids=[sections[0].id]), phrase)


def _sections_named(doc: Doc, name: str) -> list[Section]:
    key = name.casefold()
    exact = [section for section in _sections(doc) if key in {section.kicker.casefold(), section.title.casefold(), _label(section).casefold()}]
    if exact:
        return exact
    return [section for section in _sections(doc) if key in f"{section.kicker} {section.title}".casefold()]


def _label(section: Section) -> str:
    return section.title or section.kicker or section.id


def _ordinal(token: str) -> int:
    word = token.casefold()
    if word in _ORDINAL:
        return _ORDINAL[word]
    return int(re.match(r"\d+", word).group(0))


def _sentences(text: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for match in re.finditer(r"[.!?]\s+", text):
        spans.append((start, match.end()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _find_quote(text: str, quote: str) -> tuple[int, int] | None:
    folded, mapping = _fold(text)
    pos = folded.find(_norm(quote).casefold())
    if pos < 0:
        return None
    start = mapping[pos]
    end = mapping[pos + len(_norm(quote).casefold()) - 1] + 1
    return _snap(text, start, end)


def _fold(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    mapping: list[int] = []
    pending = False
    for index, char in enumerate(text):
        if char.isspace():
            pending = bool(chars)
            continue
        if pending:
            chars.append(" ")
            mapping.append(index)
            pending = False
        chars.append(char.casefold())
        mapping.append(index)
    return "".join(chars), mapping


def _snap(text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and text[start - 1].isalnum():
        start -= 1
    while end < len(text) and end > start and text[end : end + 1].isalnum():
        end += 1
    return start, end


def _overlap(a: list[float], b: list[float]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def _item_ref(token: str) -> tuple[str, int]:
    match = re.fullmatch(r"(b\d+)\.i(\d+)", token)
    if not match:
        raise EditError(f"item ids look like b3.i2, not {token}")
    return match.group(1), int(match.group(2)) - 1


def _cell_ref(token: str) -> tuple[str, int, int]:
    match = re.fullmatch(r"(b\d+)\.r(\d+)c(\d+)", token)
    if not match:
        raise EditError(f"cell ids look like b3.r1c2, not {token}")
    return match.group(1), int(match.group(2)) - 1, int(match.group(3)) - 1


def _spec_of(block: Block) -> dict:
    if isinstance(block.data.get("spec"), dict):
        return json.loads(json.dumps(block.data["spec"]))
    import yaml

    loaded = yaml.safe_load(str(block.data.get("source") or block.data.get("text") or "")) or {}
    if not isinstance(loaded, dict):
        raise EditError("diagram spec is not a mapping")
    return loaded


def _next_id(doc: Doc) -> str:
    best = 0
    for section in _sections(doc):
        for token in (section.id, section.title_id):
            best = max(best, _num(token))
        for block in section.blocks:
            best = max(best, _num(block.id))
    return f"b{best + 1}"


def _num(token: str) -> int:
    match = re.fullmatch(r"b(\d+)", token or "")
    return int(match.group(1)) if match else 0


def _diff(before: Doc, after: Doc, allowed: set[str]) -> str:
    lines = []
    after_blocks = {block.id: block for block in _blocks(after)}
    for block in _blocks(before):
        if block.id not in allowed:
            continue
        old, new = _plain(block), _plain(after_blocks.get(block.id) or block)
        if old != new:
            lines.append(f"- {block.id}: {old}")
            lines.append(f"+ {block.id}: {new}")
    for block in _blocks(after):
        if block.id in allowed and block.id not in {item.id for item in _blocks(before)}:
            lines.append(f"+ {block.id}: {_plain(block)}")
    return "\n".join(lines)


def _copy_locks(source: Doc, dest: Doc) -> None:
    by_text = {_plain(block): block for block in _blocks(source)}
    for block in _blocks(dest):
        previous = by_text.get(_plain(block))
        if previous is None:
            continue
        block.locked = previous.locked
        block.edited_by = previous.edited_by
        block.rev = previous.rev
