"""The agent's workspace for one chat: drafts, pinned notes, attachments, and the rolling summary.

A draft is free Markdown the agent writes the way a plan file is written: headings,
tables, callouts, and mermaid diagrams. It is versioned like a document. Publishing a
draft sets it in the Spark template and links that document to the draft, so a later
publish of the same draft becomes the next version of the same document.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from . import store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
  id TEXT PRIMARY KEY,
  chat_id TEXT,
  root_id TEXT,
  version INTEGER,
  title TEXT,
  body TEXT,
  note TEXT DEFAULT '',
  author TEXT DEFAULT 'agent',
  document_root TEXT DEFAULT '',
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_drafts_chat ON drafts(chat_id);
CREATE INDEX IF NOT EXISTS idx_drafts_root ON drafts(root_id);
CREATE TABLE IF NOT EXISTS chat_notes (
  id TEXT PRIMARY KEY,
  chat_id TEXT,
  text TEXT,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_notes_chat ON chat_notes(chat_id);
CREATE TABLE IF NOT EXISTS chat_summaries (
  chat_id TEXT PRIMARY KEY,
  summary TEXT,
  upto REAL,
  updated_at REAL
);
"""
_ready: set[int] = set()
MAX_DRAFT = 400_000


class WorkspaceError(RuntimeError):
    pass


def ensure() -> None:
    connection = store.conn()
    if id(connection) in _ready:
        return
    with store._lock:
        connection.executescript(_SCHEMA)
        connection.commit()
    _ready.clear()
    _ready.add(id(connection))


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# drafts ------------------------------------------------------------------------------


def _row(r) -> dict:
    body = r[5] or ""
    return {
        "id": r[0],
        "chat_id": r[1],
        "root_id": r[2],
        "version": r[3],
        "title": r[4],
        "body": body,
        "note": r[6] or "",
        "author": r[7] or "agent",
        "document_root": r[8] or "",
        "created_at": r[9],
        "words": len(re.findall(r"\w+", body)),
        "outline": outline(body),
    }


_COLS = "id, chat_id, root_id, version, title, body, note, author, document_root, created_at"


def create_draft(chat_id: str, title: str, body: str, *, note: str = "", author: str = "agent") -> dict:
    ensure()
    body = _clean(body)
    draft_id = _new_id()
    title = _title(title, body)
    with store._lock:
        store.conn().execute(
            f"INSERT INTO drafts ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (draft_id, chat_id, draft_id, 1, title, body, note, author, "", time.time()),
        )
        store.conn().commit()
    return get_draft(draft_id)


def save_version(draft_id: str, body: str, *, title: str | None = None, note: str = "", author: str = "agent") -> dict:
    """A new version of the draft's lineage. The draft id given may be any version."""
    ensure()
    base = get_draft(draft_id)
    body = _clean(body)
    if body == base["body"] and (title is None or title == base["title"]):
        return latest_in(base["root_id"])
    top = store.conn().execute("SELECT MAX(version) FROM drafts WHERE root_id=?", (base["root_id"],)).fetchone()[0] or 1
    linked = store.conn().execute(
        "SELECT document_root FROM drafts WHERE root_id=? AND document_root!='' ORDER BY version DESC LIMIT 1",
        (base["root_id"],),
    ).fetchone()
    new_id = _new_id()
    with store._lock:
        store.conn().execute(
            f"INSERT INTO drafts ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                new_id,
                base["chat_id"],
                base["root_id"],
                int(top) + 1,
                _title(title or base["title"], body),
                body,
                note,
                author,
                linked[0] if linked else "",
                time.time(),
            ),
        )
        store.conn().commit()
    return get_draft(new_id)


def get_draft(draft_id: str) -> dict:
    ensure()
    if not re.fullmatch(r"[a-f0-9]{12}", draft_id or ""):
        raise WorkspaceError("draft not found")
    row = store.conn().execute(f"SELECT {_COLS} FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not row:
        raise WorkspaceError("draft not found")
    return _row(row)


def latest_in(root_id: str) -> dict:
    row = store.conn().execute(
        f"SELECT {_COLS} FROM drafts WHERE root_id=? ORDER BY version DESC LIMIT 1", (root_id,)
    ).fetchone()
    if not row:
        raise WorkspaceError("draft not found")
    return _row(row)


def drafts_for_chat(chat_id: str, *, latest_only: bool = True) -> list[dict]:
    ensure()
    rows = store.conn().execute(
        f"SELECT {_COLS} FROM drafts WHERE chat_id=? ORDER BY created_at", (chat_id,)
    ).fetchall()
    items = [_row(r) for r in rows]
    if not latest_only:
        return items
    best: dict[str, dict] = {}
    for item in items:
        if item["root_id"] not in best or item["version"] > best[item["root_id"]]["version"]:
            best[item["root_id"]] = item
    return sorted(best.values(), key=lambda item: item["created_at"])


def draft_versions(draft_id: str) -> list[dict]:
    base = get_draft(draft_id)
    rows = store.conn().execute(
        "SELECT id, version, created_at, note, author FROM drafts WHERE root_id=? ORDER BY version", (base["root_id"],)
    ).fetchall()
    return [{"id": r[0], "version": r[1], "created_at": r[2], "note": r[3] or "", "author": r[4] or "agent"} for r in rows]


def link_document(draft_id: str, document_root: str) -> None:
    base = get_draft(draft_id)
    with store._lock:
        store.conn().execute("UPDATE drafts SET document_root=? WHERE root_id=?", (document_root, base["root_id"]))
        store.conn().commit()


def delete_chat(chat_id: str) -> None:
    ensure()
    with store._lock:
        store.conn().execute("DELETE FROM drafts WHERE chat_id=?", (chat_id,))
        store.conn().execute("DELETE FROM chat_notes WHERE chat_id=?", (chat_id,))
        store.conn().execute("DELETE FROM chat_summaries WHERE chat_id=?", (chat_id,))
        store.conn().commit()


def apply_edits(body: str, edits: list[dict]) -> tuple[str, list[str]]:
    """Exact find and replace, in order. Each find must match once unless replace_all is set."""
    problems: list[str] = []
    for index, edit in enumerate(edits, 1):
        find = str(edit.get("find") or "")
        replace = str(edit.get("replace") if edit.get("replace") is not None else "")
        if not find:
            problems.append(f"edit {index}: find is empty")
            continue
        count = body.count(find)
        if count == 0:
            loose = _loose_find(body, find)
            if loose is None:
                problems.append(f"edit {index}: the text to find is not in the draft: {find[:120]!r}")
                continue
            start, end = loose
            body = body[:start] + replace + body[end:]
            continue
        if count > 1 and not edit.get("replace_all"):
            problems.append(f"edit {index}: the text to find appears {count} times; add more surrounding text or set replace_all")
            continue
        body = body.replace(find, replace) if edit.get("replace_all") else body.replace(find, replace, 1)
    return body, problems


def _loose_find(body: str, find: str) -> tuple[int, int] | None:
    """Match ignoring differences in whitespace, which models often get wrong."""
    words = find.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(word) for word in words)
    found = list(re.finditer(pattern, body))
    if len(found) != 1:
        return None
    return found[0].start(), found[0].end()


def outline(body: str) -> list[str]:
    heads = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,3})\s+(.+?)\s*#*\s*$", line)
        if match:
            heads.append(("  " * (len(match.group(1)) - 1)) + match.group(2).strip())
    return heads[:60]


def section_text(body: str, heading: str) -> str | None:
    """The text under one heading, down to the next heading of the same or a higher level."""
    lines = body.splitlines()
    wanted = heading.strip().lstrip("#").strip().casefold()
    start = level = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        if start is None and match.group(2).strip().casefold() == wanted:
            start, level = index, len(match.group(1))
        elif start is not None and len(match.group(1)) <= (level or 1):
            return "\n".join(lines[start:index]).strip()
    return "\n".join(lines[start:]).strip() if start is not None else None


def _clean(body: str) -> str:
    text = (body or "").replace("\r\n", "\n").strip() + "\n"
    if len(text) > MAX_DRAFT:
        raise WorkspaceError("the draft is too long")
    return text


def _title(title: str | None, body: str) -> str:
    clean = " ".join(str(title or "").split())[:120]
    if clean:
        return clean
    for line in body.splitlines():
        match = re.match(r"^#\s+(.+)$", line)
        if match:
            return match.group(1).strip()[:120]
    return "Untitled draft"


# notes -------------------------------------------------------------------------------


def add_note(chat_id: str, text: str) -> dict:
    ensure()
    clean = " ".join(str(text or "").split())[:600]
    if not clean:
        raise WorkspaceError("the note is empty")
    existing = [item for item in notes(chat_id) if item["text"].casefold() == clean.casefold()]
    if existing:
        return existing[0]
    note_id = _new_id()
    with store._lock:
        store.conn().execute(
            "INSERT INTO chat_notes (id, chat_id, text, created_at) VALUES (?,?,?,?)", (note_id, chat_id, clean, time.time())
        )
        store.conn().commit()
    return {"id": note_id, "text": clean}


def drop_note(chat_id: str, note_id: str) -> bool:
    ensure()
    with store._lock:
        count = store.conn().execute("DELETE FROM chat_notes WHERE id=? AND chat_id=?", (note_id, chat_id)).rowcount
        store.conn().commit()
    return bool(count)


def notes(chat_id: str) -> list[dict]:
    ensure()
    rows = store.conn().execute(
        "SELECT id, text, created_at FROM chat_notes WHERE chat_id=? ORDER BY created_at", (chat_id,)
    ).fetchall()
    return [{"id": r[0], "text": r[1], "created_at": r[2]} for r in rows]


# rolling summary ---------------------------------------------------------------------


def summary(chat_id: str) -> tuple[str, float]:
    ensure()
    row = store.conn().execute("SELECT summary, upto FROM chat_summaries WHERE chat_id=?", (chat_id,)).fetchone()
    return (row[0] or "", float(row[1] or 0)) if row else ("", 0.0)


def save_summary(chat_id: str, text: str, upto: float) -> None:
    ensure()
    with store._lock:
        store.conn().execute(
            "INSERT INTO chat_summaries (chat_id, summary, upto, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET summary=excluded.summary, upto=excluded.upto, updated_at=excluded.updated_at",
            (chat_id, text, upto, time.time()),
        )
        store.conn().commit()


# attachments -------------------------------------------------------------------------


def attachment_text(folder: Path, name: str, data: bytes | None = None) -> str:
    """The Markdown text of an upload, cached beside it so later turns can read it again."""
    cache = folder / (name + ".md")
    if cache.is_file():
        return cache.read_text(encoding="utf-8")
    from .uploads import to_markdown

    raw = data if data is not None else (folder / name).read_bytes()
    text = to_markdown(name, raw)
    cache.write_text(text, encoding="utf-8")
    return text
