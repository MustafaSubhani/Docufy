"""Personal memory. Lessons come from the user or from a verified fix, never from a source file.

Active memories are retrieved before planning, drafting, verifying, and editing.
A narrow scope wins over a wide one. A lesson that keeps helping becomes a skill
file and, when the fix is a replacement, a lint rule.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextvars import ContextVar
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "workspace" / "memory" / "memory.db"
DECAY_DAYS = 180
PROMOTE_HITS = 3
RETIRE_MISSES = 3
CHECKLIST_LIMIT = 6
CHECKLIST_CHARS = 800
SCOPES = {"project": 4, "client": 3, "user": 2, "global": 1}
TRUSTED = {"user", "verify", "manual"}

_db_path: ContextVar[Path | None] = ContextVar("spark_memory_db", default=None)
_context: ContextVar[dict | None] = ContextVar("spark_memory_context", default=None)
_conns: dict[str, sqlite3.Connection] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  kind TEXT,
  status TEXT,
  scope TEXT,
  scope_key TEXT,
  archetype TEXT,
  section_type TEXT,
  component TEXT,
  model TEXT,
  mistake TEXT,
  fix TEXT,
  evidence TEXT,
  confidence REAL,
  hits INTEGER,
  misses INTEGER,
  created_at REAL,
  used_at REAL,
  source TEXT,
  skill TEXT
);
CREATE TABLE IF NOT EXISTS issue_stats (
  code TEXT PRIMARY KEY,
  seen INTEGER,
  repeated INTEGER
);
"""


class MemoryError(RuntimeError):
    pass


def use(path: Path):
    """Point this thread at a database. Tests use a temporary file."""
    return _db_path.set(Path(path))


def reset(token) -> None:
    _db_path.reset(token)
    _context.set(None)


def set_context(**kwargs) -> None:
    current = dict(_context.get() or {})
    current.update({key: value for key, value in kwargs.items() if value is not None})
    _context.set(current)


def remember(
    kind: str,
    text: str,
    *,
    fix: str = "",
    source: str = "user",
    scope: str = "user",
    scope_key: str = "",
    archetype: str = "",
    section_type: str = "",
    component: str = "",
    model: str = "",
    evidence: dict | None = None,
    verified: bool = False,
    created_at: float | None = None,
) -> str:
    """Store a preference, lesson, or client fact. Documents and web pages are refused."""
    if source not in TRUSTED:
        raise MemoryError("memory only learns from the user or from a verified fix")
    if scope not in SCOPES:
        raise MemoryError(f"unknown scope {scope}")
    if kind not in {"preference", "lesson", "fact"}:
        raise MemoryError(f"unknown memory kind {kind}")
    status = "active" if verified or kind in {"preference", "fact"} and source == "user" else "candidate"
    if kind == "lesson" and not verified:
        status = "candidate"
    confidence = 0.9 if verified else 0.6 if status == "active" else 0.4
    memory_id = uuid.uuid4().hex[:12]
    now = time.time() if created_at is None else created_at
    with _lock_conn() as db:
        db.execute(
            """INSERT INTO memories (
                id, kind, status, scope, scope_key, archetype, section_type, component, model,
                mistake, fix, evidence, confidence, hits, misses, created_at, used_at, source, skill
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                memory_id, kind, status, scope, scope_key, archetype, section_type, component, model,
                text.strip(), fix.strip(), json.dumps(evidence or {}), confidence, 0, 0, now, now, source, "",
            ),
        )
    return memory_id


def note_verified(issue_key: str, *, archetype: str = "", component: str = "", model: str = "") -> str:
    """A check failed and the fix made it pass. That lesson starts active."""
    code = issue_key.split("|", 1)[0]
    return remember(
        "lesson",
        code,
        fix=f"Keep the document free of {code}. The verifier cleared it once a fix was applied.",
        source="verify",
        scope="user",
        archetype=archetype,
        component=component,
        model=model,
        evidence={"issue": issue_key},
        verified=True,
    )


def note_correction(before: str, after: str, *, component: str = "", archetype: str = "") -> str:
    """A hand edit is a candidate until the user confirms it."""
    return remember(
        "lesson",
        before.strip()[:240],
        fix=after.strip()[:240],
        source="manual",
        scope="user",
        component=component,
        archetype=archetype,
        evidence={"before": before[:500], "after": after[:500]},
        verified=False,
    )


def confirm(memory_id: str) -> None:
    _update_status(memory_id, "active", confidence=0.9)


def disable(memory_id: str) -> None:
    _update_status(memory_id, "disabled")


def delete(memory_id: str) -> None:
    with _lock_conn() as db:
        db.execute("DELETE FROM memories WHERE id=?", (memory_id,))


def forget_key(scope_key: str) -> int:
    """Remove memories that belong to one chat, including a skill file they promoted."""
    if not scope_key:
        return 0
    with _lock_conn() as db:
        rows = db.execute("SELECT skill FROM memories WHERE scope_key=?", (scope_key,)).fetchall()
        db.execute("DELETE FROM memories WHERE scope_key=?", (scope_key,))
    removed = 0
    for row in rows:
        skill = row["skill"] or ""
        path = Path(skill)
        if skill and path.is_file() and "skills" in path.parts:
            path.unlink()
        removed += 1
    return removed


def edit_memory(memory_id: str, text: str) -> None:
    with _lock_conn() as db:
        db.execute("UPDATE memories SET mistake=? WHERE id=?", (text.strip(), memory_id))


def list_memories(status: str = "") -> list[dict]:
    sql = "SELECT * FROM memories"
    args: tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    sql += " ORDER BY created_at DESC"
    with _lock_conn() as db:
        rows = db.execute(sql, args).fetchall()
    return [dict(row) for row in rows]


def record_issue(code: str, *, again: bool) -> None:
    """Count whether an issue type came back after a lesson was supposed to prevent it."""
    with _lock_conn() as db:
        row = db.execute("SELECT seen, repeated FROM issue_stats WHERE code=?", (code,)).fetchone()
        if row is None:
            db.execute("INSERT INTO issue_stats (code, seen, repeated) VALUES (?,?,?)", (code, 1, 1 if again else 0))
        else:
            db.execute(
                "UPDATE issue_stats SET seen=?, repeated=? WHERE code=?",
                (row["seen"] + 1, row["repeated"] + (1 if again else 0), code),
            )
        if again:
            db.execute(
                "UPDATE memories SET misses=misses+1 WHERE status='active' AND mistake=?",
                (code,),
            )
            db.execute(
                """UPDATE memories SET status='retired'
                   WHERE status='active' AND mistake=? AND misses>=? AND misses>hits""",
                (code, RETIRE_MISSES),
            )


def repeat_rate(code: str) -> float | None:
    with _lock_conn() as db:
        row = db.execute("SELECT seen, repeated FROM issue_stats WHERE code=?", (code,)).fetchone()
    if row is None or not row["seen"]:
        return None
    return row["repeated"] / row["seen"]


def checklist(
    *,
    archetype: str = "",
    section_type: str = "",
    component: str = "",
    client: str = "",
    project: str = "",
    text: str = "",
    stage: str = "",
) -> str:
    """Active memories for this context, narrowest and newest first, inside a short budget."""
    ctx = _context.get() or {}
    archetype = archetype or str(ctx.get("archetype") or "")
    section_type = section_type or str(ctx.get("section_type") or "")
    component = component or str(ctx.get("component") or "")
    client = client or str(ctx.get("client") or "")
    project = project or str(ctx.get("project") or "")
    text = text or str(ctx.get("text") or "")
    _expire()
    query = _terms(" ".join([archetype, section_type, component, client, project, text, stage]))
    with _lock_conn() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM memories WHERE status='active'").fetchall()]
    ranked = []
    for row in rows:
        if not _matches(row, archetype, section_type, component, client, project):
            continue
        overlap = _overlap(query, _terms(f"{row['mistake']} {row['fix']}"))
        ranked.append((SCOPES[row["scope"]], overlap, row["created_at"], row))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    chosen: list[dict] = []
    seen: set[str] = set()
    for _, _, _, row in ranked:
        key = re.sub(r"\s+", " ", row["mistake"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(row)
        if len(chosen) >= CHECKLIST_LIMIT:
            break
    lines: list[str] = []
    used = 0
    kept: list[dict] = []
    for row in chosen:
        line = f"- ({row['scope']}) {row['mistake']}"
        if row["fix"]:
            line += f" Fix: {row['fix']}"
        if used + len(line) > CHECKLIST_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
        kept.append(row)
    if kept:
        _touch(kept)
    return "\n".join(lines)


def watchout(**kwargs) -> str:
    """Suffix for a model prompt. Empty when there is nothing to remember."""
    text = checklist(**kwargs)
    if not text:
        return ""
    return f"\n\nWatch out for:\n{text}"


def _matches(row: dict, archetype: str, section_type: str, component: str, client: str, project: str) -> bool:
    if row["archetype"] and archetype and row["archetype"].lower() != archetype.lower():
        return False
    if row["section_type"] and section_type and row["section_type"].lower() not in section_type.lower():
        return False
    if row["component"] and component and row["component"].lower() != component.lower():
        return False
    if row["scope"] == "client" and row["scope_key"] and client and row["scope_key"].lower() != client.lower():
        return False
    if row["scope"] == "project" and row["scope_key"] and project and row["scope_key"].lower() != project.lower():
        return False
    if row["scope"] == "client" and not client:
        return False
    if row["scope"] == "project" and not project:
        return False
    return True


def _touch(rows: list[dict]) -> None:
    now = time.time()
    with _lock_conn() as db:
        for row in rows:
            hits = int(row["hits"]) + 1
            db.execute("UPDATE memories SET hits=?, used_at=? WHERE id=?", (hits, now, row["id"]))
            if hits >= PROMOTE_HITS and row["kind"] == "lesson" and not row["skill"]:
                _promote(db, row, hits)


def _promote(db: sqlite3.Connection, row: dict, hits: int) -> None:
    folder = _database().parent / "skills"
    folder.mkdir(parents=True, exist_ok=True)
    version = 1
    name = re.sub(r"[^a-z0-9]+", "-", row["mistake"].lower()).strip("-")[:40] or "lesson"
    path = folder / f"{name}.md"
    body = (
        f"---\nversion: {version}\nlesson: {row['id']}\nkind: {row['kind']}\n---\n\n"
        f"# {row['mistake']}\n\n{row['fix'] or row['mistake']}\n"
    )
    path.write_text(body, encoding="utf-8")
    db.execute("UPDATE memories SET skill=? WHERE id=?", (str(path), row["id"]))
    if "->" in (row["fix"] or ""):
        find, repl = [part.strip() for part in row["fix"].split("->", 1)]
        rules_path = _database().parent / "rules.yaml"
        current = yaml.safe_load(rules_path.read_text(encoding="utf-8")) if rules_path.is_file() else []
        current = list(current or [])
        if not any(item.get("find") == find for item in current):
            current.append({"id": row["id"], "kind": "replace", "find": find, "replace": repl, "hits": hits})
            rules_path.write_text(yaml.safe_dump(current, sort_keys=False), encoding="utf-8")


def _expire() -> None:
    cutoff = time.time() - DECAY_DAYS * 86400
    with _lock_conn() as db:
        db.execute(
            """UPDATE memories SET status='expired'
               WHERE status='active' AND hits=0 AND created_at<?""",
            (cutoff,),
        )


def _update_status(memory_id: str, status: str, confidence: float | None = None) -> None:
    with _lock_conn() as db:
        if confidence is None:
            db.execute("UPDATE memories SET status=? WHERE id=?", (status, memory_id))
        else:
            db.execute("UPDATE memories SET status=?, confidence=? WHERE id=?", (status, confidence, memory_id))


def _terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2}


def _overlap(query: set[str], memory: set[str]) -> float:
    if not query or not memory:
        return 0.0
    return len(query & memory) / (len(memory) ** 0.5)


class _lock_conn:
    def __enter__(self) -> sqlite3.Connection:
        db = _connect()
        db.__enter__()
        return db

    def __exit__(self, exc_type, exc, tb) -> None:
        _connect().__exit__(exc_type, exc, tb)


def _connect() -> sqlite3.Connection:
    path = _database()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path)
    db = _conns.get(key)
    if db is None:
        db = sqlite3.connect(key, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        _conns[key] = db
    return db


def _database() -> Path:
    return _db_path.get() or DEFAULT_DB
