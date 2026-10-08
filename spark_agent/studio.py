"""Docufy studio: chats, generated documents, versions, and settings for the web app.

Document text is formatted by the existing renderer. When the model is off, the
planner falls back to the notes themselves, so a chat can still produce a PDF.
Jobs run one at a time on a background worker so a laptop is not asked to run
two local models at once.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from . import store
from .llm import LLMError, Router, load_config, pretty_model

ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD = 15 * 1024 * 1024
PROFILES = {"dev": "Local", "claude": "Claude", "openai": "OpenAI"}
CHOICES = ("off", "dev", "claude", "openai")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY,
  title TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  chat_id TEXT,
  role TEXT,
  body TEXT,
  status TEXT,
  document_id TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  chat_id TEXT,
  message_id TEXT,
  title TEXT,
  body TEXT,
  pdf_path TEXT,
  docx_path TEXT,
  pages INTEGER,
  spark_content INTEGER,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_documents_chat ON documents(chat_id);
"""
_COLUMNS = {
    "messages": {
        "model": "TEXT DEFAULT ''",
        "progress": "TEXT DEFAULT ''",
        "run_id": "TEXT DEFAULT ''",
        "attachment": "TEXT DEFAULT ''",
        "target_id": "TEXT DEFAULT ''",
        "activity": "TEXT DEFAULT ''",
        "web": "INTEGER DEFAULT 0",
    },
    "documents": {
        "version": "INTEGER DEFAULT 1",
        "parent_id": "TEXT DEFAULT ''",
        "root_id": "TEXT DEFAULT ''",
        "md_path": "TEXT DEFAULT ''",
    },
}
LATEST = (
    "COALESCE(d.version, 1) = (SELECT MAX(COALESCE(x.version, 1)) FROM documents x "
    "WHERE COALESCE(NULLIF(x.root_id, ''), x.id) = COALESCE(NULLIF(d.root_id, ''), d.id))"
)
_DOC_HINT = re.compile(
    r"(^|\n)\s*#{1,3}\s+\S|\b(proposal|scope of work|write a|create a|draft a|turn this into|make a document|report for)\b",
    re.I,
)
_HEADINGS = re.compile(r"(^|\n)\s*#{1,3}\s+\S")
_WRITE = re.compile(r"\b(write|draft|create|prepare|produce|generate|put together|make)\b", re.I)
_DOC_WORD = re.compile(
    r"\b(proposal|report|scope of work|statement of work|document|brief|plan|guide|letter|overview|summary|"
    r"case study|memo|one pager|playbook|handbook|policy|checklist|manual|specification|spec)\b",
    re.I,
)
_NEW_DOC = re.compile(r"\b(new|another|separate|fresh)\b[\w\s]{0,30}\b(document|proposal|report|draft|doc)\b", re.I)
_WEB = re.compile(
    r"\b(?:deep research|web search|search(?:ing)? (?:the )?(?:web|internet|online)|"
    r"(?:from|on|over) the internet|internet research|research online)\b",
    re.I,
)
_MARKER = re.compile(r"\[\[(?:task|history|target|brief|evidence)\]\]\s*")
NEEDS_MODEL = (
    "Writing a new document needs a model. Choose one below and I will write it from your brief. "
    "To set notes you already have, paste them with headings and Format only will lay them out."
)
_REPLY_SYSTEM = (
    "You are Docufy, a document studio that sets notes as Spark Technologies documents. "
    "Reply in plain UK English. Keep replies short and useful. Do not use emoji. Do not invent prices, "
    "names, or figures. If the person wants research on an industry or topic, say they should ask again "
    "with research in the message so Docufy can search the web. Do not answer industry research from memory. "
    "If the person wants a document, ask them to paste the notes or attach a file. "
    "Earlier turns are under [[history]] and the new message is under [[task]]. "
    "Answer only [[task]]; do not carry over clients or products from [[history]] unless the task names them. "
    "Do not repeat those markers."
)
_RESEARCH_TYPOS = ("resesearch", "reasearch", "reserach", "reseach", "resarch", "reearch", "reaseach")
_RESEARCH_INTENT = re.compile(
    r"\b(?:help me )?(?:research|look up|find out|tell me about|what are (?:the )?common)\b",
    re.I,
)
_CONTINUE_RESEARCH = re.compile(r"\b(?:continue|go deeper|more on (?:that|this)|same topic|follow up)\b", re.I)
_ready: dict[int, object] = {}
_probe_cache: dict[str, tuple[float, dict]] = {}


class StudioError(RuntimeError):
    pass


class Stopped(Exception):
    """The person pressed Stop. The message already says so."""


@dataclass
class _Job:
    kind: str
    chat_id: str
    assistant_id: str
    user_id: str
    body: str
    spark: bool
    settings: dict
    upload: tuple[str, bytes] | None = None
    target_id: str = ""
    web: bool = False
    section_ids: list[str] = field(default_factory=list)
    token: str = field(default_factory=lambda: uuid.uuid4().hex)


class _Jobs:
    """One worker. A job may finish only while its token is still the current one for its message."""

    def __init__(self) -> None:
        self.queue: queue.Queue[_Job] = queue.Queue()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.active = ""
        self.current: dict[str, str] = {}

    def busy(self) -> bool:
        return bool(self.active) or not self.queue.empty()

    def claim(self, job: _Job) -> None:
        self.current[job.assistant_id] = job.token

    def live(self, job: _Job) -> bool:
        return self.current.get(job.assistant_id) == job.token

    def cancel(self, message_id: str) -> None:
        self.current.pop(message_id, None)

    def release(self, job: _Job) -> None:
        if self.live(job):
            self.current.pop(job.assistant_id, None)

    def submit(self, job: _Job) -> None:
        with self.lock:
            self.queue.put(job)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name="docufy-jobs", daemon=True)
                self.thread.start()

    def _run(self) -> None:
        while True:
            try:
                job = self.queue.get(timeout=20)
            except queue.Empty:
                with self.lock:
                    if self.queue.empty():
                        self.thread = None
                        return
                continue
            self.active = job.assistant_id
            try:
                if self.live(job):
                    _work(job)
            finally:
                self.active = ""


_jobs = _Jobs()


class _Progress:
    """The steps a job takes, stored on its message so the chat can show them live and afterwards.

    A step is top level. An agent or item sits under a step. Starting a new step closes the one
    before it and anything still open under it.
    """

    def __init__(self, job: _Job) -> None:
        self.job = job
        self.run_id = ""
        self.shown = 0.0
        self.steps: list[dict] = []
        self.lock = threading.Lock()
        self.agents = bool(job.settings.get("use_model"))
        self.sections = 0

    def check(self) -> None:
        if not _jobs.live(self.job):
            raise Stopped()

    def start(self, key: str, label: str, kind: str = "step", parent: str = "", detail: str = "") -> None:
        self.check()
        now = time.time()
        with self.lock:
            if kind == "step":
                for step in self.steps:
                    if step["status"] == "running" and step["key"] != key:
                        step["status"], step["ended"] = "done", now
            found = next((step for step in self.steps if step["key"] == key), None)
            if found:
                found.update(label=label, status="running", detail=detail, started=now, ended=None)
            else:
                self.steps.append(
                    {"key": key, "label": label, "kind": kind, "parent": parent, "status": "running", "detail": detail, "started": now, "ended": None}
                )
            self._save(label)

    def finish(self, key: str, detail: str = "", status: str = "done") -> None:
        now = time.time()
        with self.lock:
            for step in self.steps:
                if step["key"] == key or (step["parent"] == key and step["status"] == "running"):
                    if step["key"] == key and detail:
                        step["detail"] = detail
                    step["status"], step["ended"] = status if step["key"] == key else "done", now
            self._save("")

    def close(self, status: str) -> None:
        now = time.time()
        with self.lock:
            for step in self.steps:
                if step["status"] == "running":
                    step["status"], step["ended"] = status, now
            self._save("", live=False)

    def partial(self, text: str) -> None:
        self.check()
        if time.time() - self.shown < 0.2:
            return
        self.shown = time.time()
        payload = json.dumps({"text": "", "streaming": True})
        with store._lock:
            store.conn().execute(
                "UPDATE messages SET body=?, progress=? WHERE id=?",
                (text, payload, self.job.assistant_id),
            )
            store.conn().commit()

    def log(self, *args, **_kwargs) -> None:
        line = " ".join(str(item) for item in args).strip()
        started = re.match(r"run ([a-f0-9]{12}): (\d+) source blocks", line)
        if started:
            self.run_id = started.group(1)
            with store._lock:
                store.conn().execute("UPDATE messages SET run_id=? WHERE id=?", (self.run_id, self.job.assistant_id))
                store.conn().commit()
            self.finish("read", f"{started.group(2)} blocks")
            self.start("plan", "Planning the layout" if self.agents else "Finding the sections")
            return
        outline = re.match(r"archetype (\S+), (\d+) sections", line)
        if outline:
            self.sections = int(outline.group(2))
            self.finish("plan", f"{outline.group(2)} sections, {outline.group(1).replace('_', ' ')}")
            self.start("layout", "Choosing components for each section" if self.agents else "Setting each section")
            return
        section = re.match(r"section (\d+)/(\d+): (.+)$", line)
        if section:
            index = int(section.group(1))
            if index > 1:
                self.finish(f"section-{index - 1}")
            self.start(f"section-{index}", section.group(3), kind="agent" if self.agents else "item", parent="layout")
            return
        agent = re.match(r"agent (start|done): (.+)$", line)
        if agent:
            key = "section-" + re.sub(r"\W+", "-", agent.group(2).lower()).strip("-")
            if agent.group(1) == "start":
                self.start(key, agent.group(2), kind="agent", parent="layout")
            else:
                self.finish(key)
            return
        if line.startswith("plan attempt"):
            self.start("plan", "Adjusting the outline")
            return
        if line.startswith("paused"):
            self.finish("layout", "memory was high, some sections kept as text")
            return
        if line.startswith("rendering pages"):
            self.finish("layout", f"{self.sections} sections" if self.sections else "")
            self.start("render", "Setting the pages and checking them")
            return
        self.check()

    def _save(self, text: str, live: bool = True) -> None:
        running = [step for step in self.steps if step["status"] == "running"]
        label = text or (running[-1]["label"] if running else "")
        payload = json.dumps({"text": label}) if live else ""
        activity = json.dumps(self.steps)
        with store._lock:
            store.conn().execute(
                "UPDATE messages SET progress=?, activity=? WHERE id=?",
                (payload, activity, self.job.assistant_id),
            )
            store.conn().commit()


def studio_dir() -> Path:
    raw = os.environ.get("SPARK_STUDIO_DIR")
    path = Path(raw) if raw else ROOT / "workspace" / "studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure() -> None:
    connection = store.conn()
    if id(connection) in _ready:
        return
    with store._lock:
        connection.executescript(_SCHEMA)
        for table, columns in _COLUMNS.items():
            names = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column, ddl in columns.items():
                if column not in names:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_documents_parent ON documents(parent_id)")
        connection.commit()
    # Holding the connection keeps its id from being reused by a later one.
    _ready.clear()
    _ready[id(connection)] = connection


# chats ---------------------------------------------------------------------------


def list_chats(query: str = "") -> list[dict]:
    ensure()
    sql = (
        "SELECT c.id, c.title, c.created_at, c.updated_at, "
        "EXISTS (SELECT 1 FROM messages m WHERE m.chat_id=c.id AND m.status='writing'), "
        "(SELECT COUNT(*) FROM documents d WHERE d.chat_id=c.id) FROM chats c"
    )
    args: list = []
    needle = query.strip()
    if needle:
        sql += " WHERE c.title LIKE ? OR EXISTS (SELECT 1 FROM messages m WHERE m.chat_id=c.id AND m.body LIKE ?)"
        args += [f"%{needle}%", f"%{needle}%"]
    sql += " ORDER BY c.updated_at DESC"
    rows = store.conn().execute(sql, args).fetchall()
    return [
        {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3], "busy": bool(r[4]), "documents": r[5]}
        for r in rows
    ]


def create_chat() -> dict:
    ensure()
    now = time.time()
    chat_id = uuid.uuid4().hex[:12]
    with store._lock:
        store.conn().execute(
            "INSERT INTO chats (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (chat_id, "New chat", now, now),
        )
        store.conn().commit()
    return {"id": chat_id, "title": "New chat", "created_at": now, "updated_at": now, "busy": False, "documents": 0}


def rename_chat(chat_id: str, title: str) -> dict:
    _check_id(chat_id)
    clean = " ".join(str(title or "").split())[:80]
    if not clean:
        raise StudioError("the chat needs a name")
    get_chat(chat_id)
    with store._lock:
        store.conn().execute("UPDATE chats SET title=? WHERE id=?", (clean, chat_id))
        store.conn().commit()
    return get_chat(chat_id)


def get_chat(chat_id: str) -> dict:
    _check_id(chat_id)
    ensure()
    row = store.conn().execute("SELECT id, title, created_at, updated_at FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not row:
        raise StudioError("chat not found")
    cfg = load_config()
    messages = [
        {
            "id": m[0],
            "role": m[1],
            "body": m[2],
            "status": m[3],
            "document_id": m[4],
            "created_at": m[5],
            "model": m[6] or "",
            "model_label": _choice_label(m[6] or "", cfg) if m[1] == "assistant" else "",
            "progress": _progress(m[7]),
            "attachment": m[8] or "",
            "target_id": m[9] or "",
            "activity": _activity(m[10]),
            "web": bool(m[11]),
        }
        for m in store.conn().execute(
            "SELECT id, role, body, status, document_id, created_at, model, progress, attachment, target_id, activity, web "
            "FROM messages WHERE chat_id=? ORDER BY created_at",
            (chat_id,),
        )
    ]
    return {
        "id": row[0],
        "title": row[1],
        "created_at": row[2],
        "updated_at": row[3],
        "busy": any(item["status"] == "writing" for item in messages),
        "messages": messages,
        "documents": _documents_for_chat(chat_id),
    }


def delete_chat(chat_id: str) -> None:
    ensure()
    store.ensure_panel()
    docs = _documents_for_chat(chat_id)
    message_ids = [r[0] for r in store.conn().execute("SELECT id FROM messages WHERE chat_id=?", (chat_id,))]
    for message_id in message_ids:
        _jobs.cancel(message_id)
    with store._lock:
        store.conn().execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        store.conn().execute("DELETE FROM documents WHERE chat_id=?", (chat_id,))
        store.conn().execute("DELETE FROM chats WHERE id=?", (chat_id,))
        store.conn().execute("DELETE FROM activity_log WHERE detail LIKE ?", (f"%{chat_id}%",))
        store.conn().commit()
    for doc in docs:
        folder = studio_dir() / "docs" / doc["id"]
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
    for message_id in message_ids:
        folder = studio_dir() / "uploads" / message_id
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
    from .memory import forget_key
    from .panel import log_activity

    forget_key(chat_id)
    log_activity("project", "deleted a chat")


# messages ------------------------------------------------------------------------


def send_message(
    chat_id: str,
    text: str,
    *,
    spark_content: bool | None = None,
    upload: tuple[str, bytes] | None = None,
    model: str | None = None,
    document_id: str | None = None,
    section_ids: list[str] | None = None,
    web: bool = False,
    wait: bool = False,
) -> dict:
    chat = get_chat(chat_id)
    settings = with_choice(load_settings(), model)
    spark = settings["spark_content"] if spark_content is None else bool(spark_content)
    body = (text or "").strip()
    filename = _safe_name(upload[0]) if upload else ""
    if upload and not body:
        body = filename
    if not body and not upload:
        raise StudioError("write a message first")
    if upload:
        from .ops import UploadError, sniff_upload

        try:
            sniff_upload(filename, upload[1])
        except UploadError as exc:
            raise StudioError(str(exc)) from exc
        if len(upload[1]) > MAX_UPLOAD:
            raise StudioError(f"that file is over {MAX_UPLOAD // (1024 * 1024)} MB")
    target = ""
    if document_id:
        _check_id(document_id)
        found = store.conn().execute("SELECT chat_id FROM documents WHERE id=?", (document_id,)).fetchone()
        if not found or found[0] != chat_id:
            raise StudioError("document not found")
        target = document_id
    kind = _kind(body, filename, target, web=web)
    now = time.time()
    user_id = uuid.uuid4().hex[:12]
    assistant_id = uuid.uuid4().hex[:12]
    title = chat["title"]
    if title == "New chat":
        title = _title_from(body or filename)
    if upload:
        folder = studio_dir() / "uploads" / user_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(upload[1])
    choice = _choice_of(settings)
    with store._lock:
        store.conn().execute(
            "INSERT INTO messages (id, chat_id, role, body, status, document_id, created_at, attachment, target_id, web) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, chat_id, "user", body, "ready", None, now, filename, target if kind == "revise" else "", 1 if web else 0),
        )
        store.conn().execute(
            "INSERT INTO messages (id, chat_id, role, body, status, document_id, created_at, model, progress) VALUES (?,?,?,?,?,?,?,?,?)",
            (assistant_id, chat_id, "assistant", _pending(kind, web), "writing", None, now + 0.001, choice, ""),
        )
        store.conn().execute("UPDATE chats SET title=?, updated_at=? WHERE id=?", (title, now, chat_id))
        store.conn().commit()
    scoped = [item.strip() for item in (section_ids or []) if item and str(item).strip()]
    job = _Job(
        kind,
        chat_id,
        assistant_id,
        user_id,
        body,
        spark,
        settings,
        upload,
        target if kind == "revise" else "",
        web,
        scoped,
    )
    _dispatch(job, wait=wait)
    return get_chat(chat_id)


def stop_message(chat_id: str, message_id: str) -> dict:
    _check_id(chat_id)
    _check_id(message_id)
    row = store.conn().execute("SELECT status, run_id FROM messages WHERE id=? AND chat_id=?", (message_id, chat_id)).fetchone()
    if not row:
        raise StudioError("message not found")
    if row[0] == "writing":
        _jobs.cancel(message_id)
        _finish_message(message_id, chat_id, "Stopped.", "stopped", None)
        if row[1]:
            store.finish_run(row[1], "stopped", "", {"stopped": True})
    return get_chat(chat_id)


def retry_message(chat_id: str, message_id: str, model: str | None = None) -> dict:
    _check_id(chat_id)
    _check_id(message_id)
    ensure()
    row = store.conn().execute(
        "SELECT status, created_at, role FROM messages WHERE id=? AND chat_id=?", (message_id, chat_id)
    ).fetchone()
    if not row or row[2] != "assistant":
        raise StudioError("message not found")
    if row[0] not in {"failed", "stopped", "blocked"}:
        raise StudioError("only a stopped or failed reply can be tried again")
    user = store.conn().execute(
        "SELECT id, body, attachment, target_id, web FROM messages WHERE chat_id=? AND role='user' AND created_at<? ORDER BY created_at DESC LIMIT 1",
        (chat_id, row[1]),
    ).fetchone()
    if not user:
        raise StudioError("there is nothing to try again")
    settings = with_choice(load_settings(), model)
    upload = None
    if user[2]:
        path = studio_dir() / "uploads" / user[0] / user[2]
        if not path.is_file():
            raise StudioError("the attached file is no longer saved; attach it again")
        upload = (user[2], path.read_bytes())
    web = bool(user[4])
    kind = _kind(user[1], user[2] or "", user[3] or "", web=web)
    with store._lock:
        store.conn().execute(
            "UPDATE messages SET body=?, status='writing', document_id=NULL, progress='', activity='', run_id='', model=? WHERE id=?",
            (_pending(kind, web), _choice_of(settings), message_id),
        )
        store.conn().execute("UPDATE chats SET updated_at=? WHERE id=?", (time.time(), chat_id))
        store.conn().commit()
    job = _Job(kind, chat_id, message_id, user[0], user[1], settings["spark_content"], settings, upload, user[3] or "", web)
    _dispatch(job)
    return get_chat(chat_id)


def recover_messages() -> int:
    """A reply that was still writing when the studio stopped cannot finish. Mark it so it can be tried again."""
    ensure()
    with store._lock:
        cursor = store.conn().execute(
            "UPDATE messages SET status='failed', progress='', body=? WHERE status='writing'",
            ("The studio restarted before this finished. Try again.",),
        )
        store.conn().commit()
    return cursor.rowcount or 0


# documents -----------------------------------------------------------------------


def list_documents(query: str = "", spark: str = "") -> list[dict]:
    ensure()
    sql = (
        "SELECT d.id, d.chat_id, d.title, d.body, d.pages, d.spark_content, d.created_at, c.title, d.version "
        "FROM documents d LEFT JOIN chats c ON c.id = d.chat_id"
    )
    args: list = []
    needle = query.strip()
    clauses = [LATEST]
    if needle:
        clauses.append("(d.title LIKE ? OR d.body LIKE ? OR c.title LIKE ?)")
        like = f"%{needle}%"
        args.extend([like, like, like])
    if spark == "on":
        clauses.append("d.spark_content=1")
    elif spark == "off":
        clauses.append("d.spark_content=0")
    sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY d.created_at DESC"
    rows = store.conn().execute(sql, args).fetchall()
    return [
        {
            "id": r[0],
            "chat_id": r[1],
            "title": r[2],
            "snippet": _snippet(r[3] or "", needle),
            "pages": r[4],
            "spark_content": bool(r[5]),
            "created_at": r[6],
            "chat_title": r[7] or "New chat",
            "version": r[8] or 1,
        }
        for r in rows
    ]


def _read_layout(pdf_path: str) -> dict:
    if not pdf_path:
        return {}
    path = Path(pdf_path).with_suffix(".layout.json")
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def document_sections(doc_id: str) -> list[dict]:
    """Sections from the markdown source, with PDF page numbers when layout is available."""
    from spark_docs.ir import load_markdown
    from spark_docs.links import slug

    from .edit import _label, _sections

    doc = get_document(doc_id)
    md_path = Path(doc["md_path"] or Path(doc["pdf_path"]).with_name("notes.spark.md"))
    if not md_path.is_file():
        return []
    ir = load_markdown(md_path.read_text(encoding="utf-8"))
    layout = _read_layout(doc["pdf_path"])
    pages_by_id: dict[str, set[int]] = {}
    pages_by_anchor: dict[str, set[int]] = {}
    for block in layout.get("blocks") or []:
        page = block.get("page")
        if not page:
            continue
        number = int(page)
        bid = block.get("id")
        if bid:
            pages_by_id.setdefault(str(bid), set()).add(number)
        anchor = block.get("anchor")
        if anchor:
            pages_by_anchor.setdefault(str(anchor), set()).add(number)
    out: list[dict] = []
    for section in _sections(ir):
        block_ids = [section.id]
        if section.title_id:
            block_ids.append(section.title_id)
        block_ids.extend(block.id for block in section.blocks)
        pages: set[int] = set()
        for bid in block_ids:
            pages.update(pages_by_id.get(bid, ()))
        if not pages and section.kicker:
            pages.update(pages_by_anchor.get(slug(section.kicker), ()))
        out.append(
            {
                "id": section.id,
                "label": _label(section),
                "kicker": section.kicker,
                "title": section.title,
                "pages": sorted(pages),
            }
        )
    return out


def get_document(doc_id: str) -> dict:
    _check_id(doc_id)
    ensure()
    row = store.conn().execute(
        "SELECT id, chat_id, message_id, title, pdf_path, docx_path, pages, spark_content, created_at, version, parent_id, root_id, md_path "
        "FROM documents WHERE id=?",
        (doc_id,),
    ).fetchone()
    if not row:
        raise StudioError("document not found")
    return {
        "id": row[0],
        "chat_id": row[1],
        "message_id": row[2],
        "title": row[3],
        "pdf_path": row[4],
        "docx_path": row[5],
        "pages": row[6],
        "spark_content": bool(row[7]),
        "created_at": row[8],
        "version": row[9] or 1,
        "parent_id": row[10] or "",
        "root_id": row[11] or row[0],
        "md_path": row[12] or "",
    }


def versions(doc_id: str) -> list[dict]:
    doc = get_document(doc_id)
    rows = store.conn().execute(
        "SELECT id, version, created_at, pages FROM documents WHERE COALESCE(NULLIF(root_id, ''), id)=? ORDER BY version",
        (doc["root_id"],),
    ).fetchall()
    return [{"id": r[0], "version": r[1] or 1, "created_at": r[2], "pages": r[3]} for r in rows]


def download_path(doc_id: str, kind: str) -> tuple[Path, str, str]:
    doc = get_document(doc_id)
    stem = file_stem(doc["title"], doc["version"])
    if kind == "docx":
        path, media, suffix = doc["docx_path"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"
    elif kind == "md":
        path, media, suffix = doc["md_path"] or str(Path(doc["pdf_path"]).with_name("notes.spark.md")), "text/markdown", ".md"
    else:
        path, media, suffix = doc["pdf_path"], "application/pdf", ".pdf"
    if not path or not Path(path).is_file():
        raise StudioError("file not found")
    return Path(path), f"{stem}{suffix}", media


def page_file(doc_id: str, number: int) -> Path:
    _check_id(doc_id)
    path = studio_dir() / "docs" / doc_id / "pages" / f"{number}.png"
    if number < 1 or not path.is_file():
        raise StudioError("page not found")
    return path


# models --------------------------------------------------------------------------


def with_choice(settings: dict, model: str | None) -> dict:
    """Apply the model picked in the composer on top of the saved settings."""
    chosen = dict(settings)
    if model in (None, ""):
        return chosen
    if model not in CHOICES:
        raise StudioError("choose Format only, Local, Claude, or OpenAI")
    if model == "off":
        chosen["use_model"] = False
    else:
        chosen["use_model"] = True
        chosen["profile"] = model
    return chosen


def model_options() -> dict:
    settings = load_settings()
    cfg = load_config()
    try:
        from . import secrets

        stored = {row["name"] for row in secrets.status() if row.get("set")}
    except Exception:
        stored = set()
    ollama = _ollama_cached(settings.get("ollama_url") or "")
    options = [
        {
            "id": "off",
            "label": "Format only",
            "detail": "Sets your notes as pages without a model",
            "model": "",
            "available": True,
            "reason": "",
        }
    ]
    needs = {"claude": ("anthropic", "Anthropic"), "openai": ("openai", "OpenAI")}
    for profile, label in PROFILES.items():
        writer = str(((cfg.get("profiles") or {}).get(profile) or {}).get("writer") or "")
        if profile == "dev":
            available = bool(ollama.get("ok"))
            reason = "" if available else "Start Ollama to use the local model"
            detail = f"{pretty_model(writer)} on this computer"
        else:
            key, name = needs[profile]
            available = key in stored and not settings.get("local_only")
            if settings.get("local_only"):
                reason = "Local only is on in Settings"
            elif key not in stored:
                reason = f"Add an {name} key in Settings"
            else:
                reason = ""
            detail = pretty_model(writer)
        options.append({"id": profile, "label": label, "detail": detail, "model": writer, "available": available, "reason": reason})
    return {"options": options, "current": _choice_of(settings)}


# settings ------------------------------------------------------------------------


def load_settings() -> dict:
    cfg = load_config()
    budgets = cfg.get("budgets") or {}
    ollama = cfg.get("ollama") or {}
    data = {
        "profile": cfg.get("active_profile") or "dev",
        "use_model": False,
        "spark_content": True,
        "per_run_usd": float(budgets.get("per_run_usd") or 3),
        "monthly_usd": 20.0,
        "hard_stop": False,
        "log_level": "metadata",
        "retention_days": 90,
        "local_only": False,
        "num_ctx": int(ollama.get("num_ctx") or 16384),
        "keep_alive": ollama.get("keep_alive") or "10m",
        "ollama_url": ollama.get("url") or "http://127.0.0.1:11434",
    }
    path = studio_dir() / "settings.json"
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            saved = {}
        for key in data:
            if key in saved:
                data[key] = saved[key]
    data["profile"] = data["profile"] if data["profile"] in PROFILES else "dev"
    data["use_model"] = bool(data["use_model"])
    data["spark_content"] = bool(data["spark_content"])
    data["per_run_usd"] = float(data["per_run_usd"])
    data["monthly_usd"] = float(data["monthly_usd"])
    data["hard_stop"] = bool(data["hard_stop"])
    data["log_level"] = data["log_level"] if data["log_level"] in {"full", "redacted", "metadata"} else "metadata"
    data["retention_days"] = int(data["retention_days"])
    data["local_only"] = bool(data["local_only"])
    data["num_ctx"] = int(data["num_ctx"]) if int(data["num_ctx"]) in {2048, 4096, 8192, 16384, 32768} else 16384
    data["keep_alive"] = str(data["keep_alive"])
    return data


def save_settings(incoming: dict) -> dict:
    current = load_settings()
    if "profile" in incoming:
        if incoming["profile"] not in PROFILES:
            raise StudioError("profile must be local, claude, or openai")
        current["profile"] = incoming["profile"]
    if "use_model" in incoming:
        current["use_model"] = bool(incoming["use_model"])
    if "spark_content" in incoming:
        current["spark_content"] = bool(incoming["spark_content"])
    if "per_run_usd" in incoming:
        amount = float(incoming["per_run_usd"])
        if amount < 0:
            raise StudioError("the spending limit cannot be negative")
        current["per_run_usd"] = amount
    if "ollama_url" in incoming:
        url = str(incoming["ollama_url"]).strip()
        if not url.startswith(("http://", "https://")):
            raise StudioError("the model address must start with http:// or https://")
        current["ollama_url"] = url
    if "monthly_usd" in incoming:
        amount = float(incoming["monthly_usd"])
        if amount < 0:
            raise StudioError("the monthly budget cannot be negative")
        current["monthly_usd"] = amount
    if "hard_stop" in incoming:
        current["hard_stop"] = bool(incoming["hard_stop"])
    if "log_level" in incoming:
        if incoming["log_level"] not in {"full", "redacted", "metadata"}:
            raise StudioError("log level must be full, redacted, or metadata")
        current["log_level"] = incoming["log_level"]
    if "retention_days" in incoming:
        days = int(incoming["retention_days"])
        if days < 1 or days > 3650:
            raise StudioError("retention must be between 1 and 3650 days")
        current["retention_days"] = days
    if "local_only" in incoming:
        current["local_only"] = bool(incoming["local_only"])
    if "num_ctx" in incoming:
        ctx = int(incoming["num_ctx"])
        if ctx not in {2048, 4096, 8192, 16384, 32768}:
            raise StudioError("context size must be 2048, 4096, 8192, 16384, or 32768")
        current["num_ctx"] = ctx
    if "keep_alive" in incoming:
        alive = str(incoming["keep_alive"]).strip()
        if not re.fullmatch(r"\d+[smh]", alive):
            raise StudioError("keep alive looks like 10m")
        current["keep_alive"] = alive
    secret = str(incoming.get("password") or "").strip()
    if secret or incoming.get("clear_password"):
        from .ops import OpsError, clear_password, set_password

        try:
            if secret:
                set_password(secret)
            else:
                clear_password()
        except OpsError as exc:
            raise StudioError(str(exc)) from exc
    path = studio_dir() / "settings.json"
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    from .panel import log_activity

    log_activity("settings", f"profile {current['profile']}, monthly {current['monthly_usd']}")
    purge_old(current["retention_days"])
    return public_settings(current)


def public_settings(data: dict | None = None) -> dict:
    body = dict(data or load_settings())
    body.pop("password", None)
    body.pop("clear_password", None)
    from .ops import has_password

    body["has_password"] = has_password()
    return body


def purge_old(days: int) -> int:
    """Drop activity lines and run events older than the retention window. Usage rows stay for budgets."""
    store.ensure_panel()
    cutoff = time.time() - max(1, int(days)) * 86400
    with store._lock:
        removed = store.conn().execute("DELETE FROM activity_log WHERE at<?", (cutoff,)).rowcount or 0
        removed += store.conn().execute("DELETE FROM run_events WHERE at<?", (cutoff,)).rowcount or 0
        store.conn().commit()
    return removed


def usage_summary() -> dict:
    ensure()
    now = datetime.now()
    start_today = datetime(now.year, now.month, now.day).timestamp()
    windows = {
        "today": start_today,
        "week": (now - timedelta(days=7)).timestamp(),
        "month": (now - timedelta(days=30)).timestamp(),
    }
    totals = {}
    for name, start in windows.items():
        row = store.conn().execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE at>=?",
            (start,),
        ).fetchone()
        totals[name] = {
            "calls": row[0],
            "input_tokens": row[1],
            "output_tokens": row[2],
            "tokens": row[1] + row[2],
            "cost_usd": round(row[3], 4),
        }
    models = [
        {"model": r[0], "label": pretty_model(r[0]), "calls": r[1], "tokens": r[2], "cost_usd": round(r[3], 4)}
        for r in store.conn().execute(
            "SELECT model, COUNT(*), COALESCE(SUM(input_tokens)+SUM(output_tokens),0), COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE at>=? GROUP BY model ORDER BY 3 DESC",
            (windows["month"],),
        )
        if r[0] and r[2]
    ]
    providers = [
        {"provider": r[0], "calls": r[1], "tokens": r[2], "cost_usd": round(r[3], 4)}
        for r in store.conn().execute(
            "SELECT provider, COUNT(*), COALESCE(SUM(input_tokens)+SUM(output_tokens),0), COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE at>=? GROUP BY provider ORDER BY 3 DESC",
            (windows["month"],),
        )
        if r[0]
    ]
    docs = store.conn().execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    settings = load_settings()
    monthly = float(settings["monthly_usd"])
    spent = float(totals["month"]["cost_usd"])
    ratio = 0 if monthly <= 0 else spent / monthly
    if monthly > 0 and ratio >= 1:
        level = "stop"
    elif monthly > 0 and ratio >= 0.8:
        level = "warn"
    else:
        level = "ok"
    return {
        "windows": totals,
        "models": models,
        "providers": providers,
        "documents": docs,
        "budget": {"monthly_usd": monthly, "spent_usd": round(spent, 4), "ratio": round(ratio, 4), "level": level},
    }


# work ----------------------------------------------------------------------------


def _dispatch(job: _Job, *, wait: bool = False) -> None:
    _jobs.claim(job)
    quick = job.kind in {"reply", "draft"} and not job.settings.get("use_model")
    if wait or quick:
        _work(job)
        return
    if _jobs.busy():
        with store._lock:
            store.conn().execute(
                "UPDATE messages SET progress=? WHERE id=?",
                (json.dumps({"text": "Waiting for the job before this one"}), job.assistant_id),
            )
            store.conn().commit()
    _jobs.submit(job)


def _work(job: _Job) -> None:
    progress = _Progress(job)
    try:
        if job.kind == "document":
            _make_document(job, progress)
        elif job.kind == "draft":
            _draft_document(job, progress)
        elif job.kind == "revise":
            _revise_document(job, progress)
        elif job.kind == "research":
            _research_message(job, progress)
        else:
            reply = _reply(job, progress)
            progress.check()
            _finish_message(job.assistant_id, job.chat_id, reply, "ready", None)
        progress.close("done")
    except Stopped:
        progress.close("stopped")
        if progress.run_id:
            store.finish_run(progress.run_id, "stopped", "", {"stopped": True})
    except Exception as exc:
        if not _jobs.live(job):
            return
        progress.close("failed")
        lead = {
            "document": "The document was not created.",
            "draft": "The draft was not written.",
            "revise": "That change was not made.",
            "research": "The web search did not finish.",
        }.get(job.kind, "The reply was not written.")
        _finish_message(job.assistant_id, job.chat_id, f"{lead} {_reason(exc)}", "failed", None)
    finally:
        _jobs.release(job)


def _draft_document(job: _Job, progress: _Progress) -> None:
    from .draft import draft_document

    if not job.settings.get("use_model"):
        _finish_message(job.assistant_id, job.chat_id, NEEDS_MODEL, "blocked", None)
        return
    brief = job.body
    searched = False
    if _asked_web(job):
        run = _run_search(job, progress)
        brief = _with_sources(job.body, run)
        searched = True
    router = _router(job.settings)
    router.run_id = store.start_run("draft", "brief", router.profile)
    progress.run_id = router.run_id
    try:
        draft = draft_document(router, brief, spark=job.spark, events=progress)
    except BaseException:
        store.finish_run(router.run_id, "failed", "", {})
        raise
    store.finish_run(router.run_id, "ok", "", {"sections": draft.sections, "missing": draft.missing})
    note = "I searched the web, then wrote the draft." if searched else "The draft is ready."
    if draft.missing:
        note += f" {', '.join(draft.missing)} could not be written and {'is' if len(draft.missing) == 1 else 'are'} marked to be confirmed."
    note += " Check the details marked to be confirmed before you send it."
    _make_document(job, progress, source_text=draft.markdown, done=note, title=draft.title)


def _make_document(job: _Job, progress: _Progress, *, source_text: str | None = None, done: str = "", title: str = "") -> None:
    from .convert import convert

    progress.start("read", "Reading the draft" if source_text else "Reading the notes")
    doc_id = uuid.uuid4().hex[:12]
    folder = studio_dir() / "docs" / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if source_text is None:
            source_text = _source_text(job.body, job.upload, job.spark)
        from .uploads import normalize_newlines

        source_text = normalize_newlines(source_text)
        source = folder / "notes.md"
        source.write_text(source_text, encoding="utf-8")
        router = _router(job.settings)
        result = convert(source, router, spark_content=job.spark, render=True, docx=True, out_dir=folder, log=progress.log)
        if not result.pdf or not Path(result.pdf).is_file():
            raise StudioError("the PDF was not written")
        notes = [item for item in result.issues if not item.startswith("section planner failed")]
        progress.finish("render", f"{len(notes)} notes from the checks" if notes else "checks passed")
        progress.start("preview", "Preparing the preview")
        pages = _render_pages(Path(result.pdf), folder / "pages")
        progress.finish("preview", f"{pages} pages")
        progress.check()
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    title = title or (result.plan.get("title") or "").strip() or _title_from(job.body)
    _insert_document(
        doc_id,
        job,
        title=title,
        body=source_text[:20000],
        pdf=Path(result.pdf),
        docx=Path(result.docx) if result.docx else None,
        md=folder / "notes.spark.md",
        pages=pages,
        spark=job.spark,
        version=1,
        parent_id="",
        root_id=doc_id,
    )
    _finish_message(job.assistant_id, job.chat_id, done or "The document is ready.", "ready", doc_id)


def _revise_document(job: _Job, progress: _Progress) -> None:
    from .blocks import split_frontmatter
    from .revise import revise
    from .verify import render_and_check

    parent = get_document(job.target_id)
    source = Path(parent["md_path"] or Path(parent["pdf_path"]).with_name("notes.spark.md"))
    if not source.is_file():
        raise StudioError("the source of that document is missing")
    progress.start("read", f"Reading {parent['title']}", detail=f"version {parent['version']}")
    router = _router(job.settings) if job.settings.get("use_model") else None
    if router is not None:
        router.run_id = store.start_run("revise", str(source), router.profile)
        progress.run_id = router.run_id
    progress.start("change", "Making the change")
    if router is not None:
        progress.start("change-agent", "Editor", kind="agent", parent="change", detail="only the named section")
    revision = revise(
        source.read_text(encoding="utf-8"),
        job.body,
        router,
        section_ids=job.section_ids or None,
    )
    if router is not None:
        store.finish_run(router.run_id, "ok" if revision.markdown else "no_change", "", {"summary": revision.summary})
    if revision.markdown is None:
        progress.finish("change", "nothing changed")
        _finish_message(job.assistant_id, job.chat_id, revision.summary, "ready", None)
        return
    progress.finish("change", revision.summary.rstrip("."))
    doc_id = uuid.uuid4().hex[:12]
    folder = studio_dir() / "docs" / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        md_path = folder / "notes.spark.md"
        md_path.write_text(revision.markdown, encoding="utf-8")
        progress.start("render", "Setting the pages and checking them")
        pdf, docx, issues = render_and_check(md_path, folder, docx=True)
        if not pdf or not Path(pdf).is_file():
            raise StudioError("the PDF was not written")
        progress.finish("render", f"{len(issues)} notes from the checks" if issues else "checks passed")
        progress.start("preview", "Preparing the preview")
        pages = _render_pages(Path(pdf), folder / "pages")
        progress.finish("preview", f"{pages} pages")
        progress.check()
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    meta, body = split_frontmatter(revision.markdown)
    spark = meta.get("include_spark_content")
    top = store.conn().execute(
        "SELECT MAX(version) FROM documents WHERE COALESCE(NULLIF(root_id, ''), id)=?", (parent["root_id"],)
    ).fetchone()[0]
    version = int(top or parent["version"]) + 1
    _insert_document(
        doc_id,
        job,
        title=str(meta.get("title") or parent["title"]),
        body=body[:20000],
        pdf=Path(pdf),
        docx=Path(docx) if docx else None,
        md=md_path,
        pages=pages,
        spark=parent["spark_content"] if spark is None else bool(spark),
        version=version,
        parent_id=parent["id"],
        root_id=parent["root_id"],
    )
    _finish_message(job.assistant_id, job.chat_id, f"{revision.summary} Version {version} is ready.", "ready", doc_id)


def _insert_document(doc_id: str, job: _Job, **row) -> None:
    if not _jobs.live(job):
        shutil.rmtree(studio_dir() / "docs" / doc_id, ignore_errors=True)
        raise Stopped()
    with store._lock:
        store.conn().execute(
            """INSERT INTO documents (
                id, chat_id, message_id, title, body, pdf_path, docx_path, pages, spark_content, created_at,
                version, parent_id, root_id, md_path
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc_id,
                job.chat_id,
                job.assistant_id,
                row["title"],
                row["body"],
                str(row["pdf"]),
                str(row["docx"] or ""),
                row["pages"],
                1 if row["spark"] else 0,
                time.time(),
                row["version"],
                row["parent_id"],
                row["root_id"],
                str(row["md"]),
            ),
        )
        store.conn().commit()


def _reply(job: _Job, progress: _Progress) -> str:
    settings = job.settings
    if settings.get("use_model"):
        router = _router(settings)
        history = _history(job.chat_id, job.assistant_id)
        user = f"[[task]]\n{job.body}\n[[history]]\n{history}" if history else job.body
        result = router.stream("writer", _REPLY_SYSTEM, user, progress.partial, max_tokens=800)
        return _clean_reply(result.text) or "I could not write a reply."
    if job.body.strip().endswith("?"):
        return (
            "No model is selected, so I can set your notes as a document but cannot answer questions. "
            "Pick Local, Claude, or OpenAI in the model menu, or paste the notes."
        )
    return "Paste the notes you want set as a Spark document, or describe the piece. The pages will open beside this chat."


def _history(chat_id: str, before_id: str, limit: int = 10) -> str:
    row = store.conn().execute("SELECT created_at FROM messages WHERE id=?", (before_id,)).fetchone()
    if not row:
        return ""
    rows = store.conn().execute(
        "SELECT role, body FROM messages WHERE chat_id=? AND created_at<? AND status='ready' ORDER BY created_at DESC LIMIT ?",
        (chat_id, row[0] - 0.0005, limit + 1),
    ).fetchall()
    turns = list(reversed(rows))[:-1] if rows else []
    lines = [f"{'Person' if role == 'user' else 'Docufy'}: {' '.join(str(body).split())[:1200]}" for role, body in turns]
    return "\n".join(lines)


def _router(settings: dict) -> Router:
    profile = "dev" if settings.get("local_only") else (settings.get("profile") or "dev")
    router = Router(profile=profile)
    ollama = dict(router.config.get("ollama") or {})
    ollama["url"] = settings.get("ollama_url") or ollama.get("url")
    ollama["num_ctx"] = int(settings.get("num_ctx") or ollama.get("num_ctx") or 16384)
    ollama["keep_alive"] = settings.get("keep_alive") or ollama.get("keep_alive")
    budgets = dict(router.config.get("budgets") or {})
    budgets["per_run_usd"] = float(settings.get("per_run_usd") or budgets.get("per_run_usd") or 0)
    budgets["monthly_usd"] = float(settings.get("monthly_usd") or 0)
    budgets["hard_stop"] = bool(settings.get("hard_stop"))
    privacy = dict(router.config.get("privacy") or {})
    if settings.get("local_only"):
        privacy["local_only"] = True
    router.config = {**router.config, "ollama": ollama, "budgets": budgets, "privacy": privacy}
    if not settings.get("use_model"):
        router.fake = _model_off
    return router


def _model_off(_model, _messages, _schema) -> str:
    raise LLMError("the model is turned off")


def _finish_message(message_id: str, chat_id: str, body: str, status: str, document_id: str | None) -> None:
    now = time.time()
    with store._lock:
        store.conn().execute(
            "UPDATE messages SET body=?, status=?, document_id=?, progress='' WHERE id=?",
            (body, status, document_id, message_id),
        )
        store.conn().execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
        store.conn().commit()


# helpers -------------------------------------------------------------------------


def _documents_for_chat(chat_id: str) -> list[dict]:
    rows = store.conn().execute(
        "SELECT id, title, pages, spark_content, created_at, version, parent_id, root_id FROM documents WHERE chat_id=? ORDER BY created_at",
        (chat_id,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "pages": r[2],
            "spark_content": bool(r[3]),
            "created_at": r[4],
            "chat_id": chat_id,
            "version": r[5] or 1,
            "parent_id": r[6] or "",
            "root_id": r[7] or r[0],
        }
        for r in rows
    ]


def _fold_research(text: str) -> str:
    folded = text or ""
    for typo in _RESEARCH_TYPOS:
        folded = re.sub(typo, "research", folded, flags=re.I)
    return folded


def wants_web(text: str) -> bool:
    folded = _fold_research(text)
    if _WEB.search(folded):
        return True
    return bool(re.search(r"\bresearch\b", folded, re.I) and re.search(r"\b(?:internet|web|online)\b", folded, re.I))


def research_intent(text: str) -> bool:
    """True when the message is asking for looked-up information, not only when Web is toggled."""
    folded = _fold_research(text)
    if wants_web(folded):
        return True
    if re.search(r"\bresearch\b", folded, re.I):
        return True
    return bool(_RESEARCH_INTENT.search(folded))


def _kind(body: str, filename: str, target: str, web: bool = False) -> str:
    fresh = bool(_NEW_DOC.search(body))
    asked = web or research_intent(body)
    if target and not filename and not fresh and not asked and len(body) <= 500 and not _HEADINGS.search(body):
        return "revise"
    if is_brief(body, filename):
        return "draft"
    if asked and not filename and not _HEADINGS.search(body) and len(body) <= 2500:
        return "research"
    return "document" if _wants_document(body, filename) else "reply"


def is_brief(body: str, filename: str = "") -> bool:
    """A short request to write something new, as opposed to notes that are ready to be set as pages."""
    if filename or _HEADINGS.search(body) or len(body) > 2500:
        return False
    return bool(_WRITE.search(body) and _DOC_WORD.search(body))


def _pending(kind: str, web: bool = False) -> str:
    if web and kind in {"draft", "research"}:
        return "Searching the web"
    return {
        "document": "Setting the document",
        "draft": "Writing the document",
        "revise": "Changing the document",
        "research": "Searching the web",
    }.get(kind, "Writing a reply")


def _clean_reply(text: str) -> str:
    return _MARKER.sub("", text or "").strip()


def _user_turns(job: _Job) -> list[str]:
    row = store.conn().execute("SELECT created_at FROM messages WHERE id=?", (job.assistant_id,)).fetchone()
    if not row:
        return [job.body]
    rows = store.conn().execute(
        "SELECT body FROM messages WHERE chat_id=? AND role='user' AND created_at<? ORDER BY created_at DESC LIMIT 4",
        (job.chat_id, row[0]),
    ).fetchall()
    return [str(item[0] or "") for item in reversed(rows)]


def _asked_web(job: _Job) -> bool:
    return job.web or any(wants_web(text) for text in _user_turns(job))


def _search_goal(job: _Job) -> str:
    body = " ".join(job.body.split()).strip()
    if _CONTINUE_RESEARCH.search(body):
        turns = [text for text in _user_turns(job) if text.strip()]
        if len(turns) >= 2 and turns[-1] == body:
            earlier = turns[-2]
            if earlier and earlier != body:
                return f"{earlier}\n{body}"
    return body


def _web_connectors():
    from . import secrets
    from .deep import default_connectors

    connectors = default_connectors()
    for connector in connectors:
        if not getattr(connector, "needs_key", False) or getattr(connector, "key", ""):
            continue
        try:
            connector.key = secrets.value(connector.name) or ""
        except Exception:
            connector.key = ""
    return connectors


def _run_web(goal: str, query: str = "", *, page_limit: int = 5):
    from .deep import chat_search

    return chat_search(goal, query=query, connectors=_web_connectors(), page_limit=page_limit)


def _run_search(job: _Job, progress: _Progress):
    progress.start("search", "Searching the web")
    run = _run_web(_search_goal(job))
    pages = len(getattr(run, "pages", None) or [])
    progress.finish("search", f"{pages} pages")
    return run


def _quoted(run):
    brief = getattr(run, "brief", None)
    if brief is not None:
        return [(item.text, getattr(item, "source", "")) for item in brief.findings]
    return [(text, "") for text in (getattr(run, "findings", None) or []) if text]


def _with_sources(brief: str, run) -> str:
    lines = [
        brief.strip(),
        "",
        "Web pages read for this brief. State a figure only when it is quoted below. Otherwise mark it to be confirmed.",
    ]
    quotes = _quoted(run)
    if quotes:
        lines.append("Findings:")
        for text, source in quotes[:8]:
            lines.append(f"- {text} ({source})" if source else f"- {text}")
    else:
        lines.append("No figure was confirmed from the pages.")
    for page in (getattr(run, "pages", None) or [])[:6]:
        lines.append(f"- {page.title or page.url} {page.url}")
    return "\n".join(lines)


def _page_title(page) -> str:
    title = " ".join((page.title or "").split())
    if not title or title.lower().startswith("http") or len(title) < 4:
        return page.url
    return title[:140]


def _chat_findings(run) -> str:
    brief = getattr(run, "brief", None)
    if brief is not None:
        return _legacy_findings(run)
    query = (getattr(run, "query", "") or "the question").strip()
    lines = [f"I searched for {query}."]
    pages = list(getattr(run, "pages", None) or [])
    blurbs = list(getattr(run, "findings", None) or [])
    if not pages:
        lines.extend(
            [
                "",
                "Nothing relevant came back. Name a sector, such as banking, energy, or telecoms, and I will search that.",
            ]
        )
        return "\n".join(lines)
    lines.extend(["", "Closest pages"])
    for index, page in enumerate(pages[:3]):
        title = _page_title(page).rstrip(".").replace("[", "").replace("]", "")
        blurb = " ".join((blurbs[index] if index < len(blurbs) else "").split())
        if blurb and blurb[-1] not in ".!?":
            blurb += "."
        line = f"- [{title}]({page.url})"
        if blurb and blurb.lower() not in title.lower():
            line += f" {blurb}"
        lines.append(line)
    lines.extend(["", "These are leads. Say which sector you want to go deeper on."])
    return "\n".join(lines)


def _legacy_findings(run) -> str:
    lines = ["I searched the web."]
    if run.brief.findings:
        lines.extend(["", "Findings"])
        for finding in run.brief.findings[:6]:
            lines.append(f"- {finding.text} {finding.source}")
    if run.pages:
        lines.extend(["", "Pages"])
        for page in run.pages[:6]:
            lines.append(f"- {page.title or page.url} {page.url}")
    if not run.brief.findings and not run.pages:
        lines.extend(
            [
                "",
                "The search came back without a page I could read. A shorter question, or a Serper or Brave key in Settings, gives it more to work with.",
            ]
        )
    elif not run.brief.findings:
        lines.extend(["", "I read those pages. None of them agreed strongly enough to state a figure."])
    return "\n".join(lines)


def _query_schema(limit: int) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"queries": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": limit}},
        "required": ["queries"],
    }


_FOLLOW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"follow": {"type": "string"}},
    "required": ["follow"],
}
_QUERY_SYSTEM = (
    "You are the research agent. Turn the question into web search queries. "
    "Each query is a short phrase: the place, the sector, and the topic. "
    "Cover different parts of the question. "
    "Leave out words such as help, research, and internet. Reply with JSON only."
)
_FOLLOW_SYSTEM = (
    "You are the research agent. Read the notes. "
    "If an important part of the question is still missing, give one follow up search query. "
    "If the notes already cover it, return an empty follow. Reply with JSON only."
)
_ANSWER_SYSTEM = (
    "You are the research agent for Docufy. Answer from the pages and notes below and from nothing else. "
    "Use UK English. Structure your reply with these headings exactly:\n"
    "## What you asked\n"
    "## Context\n"
    "## Common problems and where AI helps\n"
    "## Gaps\n"
    "## References\n"
    "Under Common problems, use bullets: problem, how AI is used, caveat. "
    "Only name organisations that appear in the pages. "
    "In References, list markdown links [title](url) for each page you used. "
    "Do not invent figures. No emoji."
)
_DEEP_ANSWER = (
    _ANSWER_SYSTEM
    + " This was a deep pass: prefer confirmed findings in the notes, and say clearly what is still open."
)


def _search_phrases(job: _Job, router: Router, limit: int) -> list[str]:
    from .deep import chat_query

    goal = _search_goal(job)
    phrases: list[str] = []
    try:
        result = router.complete("writer", _QUERY_SYSTEM, goal, schema=_query_schema(limit), max_tokens=220)
        for item in (result.data or {}).get("queries") or []:
            text = " ".join(str(item).split())[:140]
            if text and text.lower() not in {phrase.lower() for phrase in phrases}:
                phrases.append(text)
    except LLMError:
        phrases = []
    if not phrases:
        phrases = [chat_query(goal)]
    return phrases[:limit]


def _merge_search(runs: list):
    from .deep import ChatResult

    pages = []
    seen: set[str] = set()
    query = ""
    for run in runs:
        query = query or str(getattr(run, "query", "") or "")
        for page in getattr(run, "pages", None) or []:
            if not page.url or page.url in seen:
                continue
            seen.add(page.url)
            pages.append(page)
            if len(pages) == 8:
                break
    if len(runs) == 1 and getattr(runs[0], "brief", None) is not None:
        return runs[0]
    return ChatResult(query=query or "the question", pages=pages, findings=[""] * len(pages))


def _research_openui_prompt() -> str:
    path = ROOT / "spark_agent" / "prompts" / "research_openui_prompt.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


_RESEARCH_DEPTH = (
    "\n\nWrite a thorough consulting-style answer, not a skim. "
    "Context should be three to five sentences with concrete detail from the notes. "
    "Include five to eight insight rows when the notes support them; each problem, aiUse, and caveat should be one or two full sentences. "
    "Gaps should be two to four honest sentences. Do not pad with generic AI hype."
)


def _research_answer_system(*, deep: bool) -> str:
    prompt = _research_openui_prompt()
    if prompt:
        if deep:
            return (
                prompt
                + _RESEARCH_DEPTH
                + "\n\nThis was a deep pass: prefer confirmed findings in the notes and say clearly what is still open."
            )
        return prompt + _RESEARCH_DEPTH
    return _DEEP_ANSWER if deep else _ANSWER_SYSTEM


def _page_notes(pages: list) -> str:
    from .deep import page_blurb

    lines = []
    for page in pages[:8]:
        title = _page_title(page).replace("[", "").replace("]", "")
        line = f"- {title} {page.url}"
        terms = {word.lower() for word in re.findall(r"[A-Za-z]{3,}", f"{title} {page.text or ''}")}
        blurb = page_blurb(page, terms) if page.text else ""
        if blurb and blurb.lower() not in title.lower():
            line += f"\n  {blurb}"
        if page.text:
            excerpt = " ".join(page.text.split())[:1400]
            if excerpt and excerpt.lower() not in (blurb or "").lower():
                line += f"\n  Excerpt: {excerpt}"
        lines.append(line)
    return "\n".join(lines)


def _source_blob(pages: list, extra: str = "") -> str:
    parts = [extra]
    for page in pages:
        parts.append(f"{page.title} {page.url} {(page.text or '')[:4000]}")
    return " ".join(parts).casefold()


def _read_named_field(inner: str, key: str) -> str:
    match = re.search(
        rf"{key}\s*:\s*([\s\S]*?)(?=\n\s*(?:ask|context|insights|gaps|sources)\s*:|$)",
        inner,
        re.I,
    )
    if not match:
        return ""
    return re.sub(r",\s*$", "", match.group(1).strip())


def _named_research_view_to_positional(block: str) -> str:
    if not re.search(r"ResearchView\s*\(\s*(?:ask|context|insights|gaps|sources)\s*:", block, re.I):
        return block
    call = re.search(r"ResearchView\s*\(\s*([\s\S]*?)\s*\)", block)
    if not call:
        return block
    inner = call.group(1)
    parts = [_read_named_field(inner, key) for key in ("ask", "context", "insights", "gaps", "sources")]
    if not all(parts):
        return block
    positional = f"ResearchView({', '.join(parts)})"
    return block[: call.start()] + positional + block[call.end() :]


def _normalize_openui_lang(text: str) -> str:
    """Fix fences, named ResearchView args, and `key =` inside objects."""
    if not text:
        return text
    fenced = re.search(r"```(?:openui|openui-lang)?\s*\n([\s\S]*?)```", text, re.I)
    if fenced:
        text = f"---openui\n{fenced.group(1).strip()}\n---end"
    if "ResearchView" not in text and "---openui" not in text.casefold() and not re.search(r"^\s*root\s*=", text, re.M):
        return text

    def fix_objects(match: re.Match[str]) -> str:
        inner = match.group(1)
        return "{" + re.sub(r"(\b\w+)\s*=", r"\1:", inner) + "}"

    def fix_block(block: str) -> str:
        block = _named_research_view_to_positional(block)
        block = re.sub(r"\{([^{}]*)\}", fix_objects, block)
        if re.match(r"^\s*ResearchView\s*\(", block, re.I):
            block = f"root = {block.strip()}"
        return block

    if re.search(r"(?i)---openui", text):
        parts = re.split(r"(?i)---openui\s*\n?", text, maxsplit=1)
        if len(parts) < 2:
            return text
        rest = parts[1]
        end = re.search(r"(?i)\n---end\b", rest)
        inner = rest[: end.start()] if end else rest
        tail = rest[end.end() :] if end else ""
        inner = fix_block(inner.strip())
        return f"---openui\n{inner}\n---end{tail}"
    return fix_block(text)


def _guard_research_answer(text: str, pages: list, extra: str = "") -> str:
    """Drop lines that name brands absent from the sources (common search noise)."""
    if not text.strip():
        return text
    blob = _source_blob(pages, extra)
    cleaned: list[str] = []
    for line in text.splitlines():
        skip = False
        for brand in ("genspark", "fauji foundation"):
            if brand in line.casefold() and brand not in blob:
                skip = True
                break
        if not skip:
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _deep_run_notes(deep_run) -> str:
    lines: list[str] = []
    brief = getattr(deep_run, "brief", None)
    if brief is not None and getattr(brief, "findings", None):
        lines.append("Confirmed findings:")
        for finding in brief.findings[:8]:
            lines.append(f"- {finding.text} ({finding.source})")
    if lines:
        lines.append("")
    lines.append("Pages:")
    lines.append(_page_notes(list(getattr(deep_run, "pages", None) or [])))
    return "\n".join(lines)


def _answer_from_pages(
    router: Router,
    question: str,
    run,
    progress: _Progress,
    *,
    deep: bool = False,
    notes: str = "",
) -> str:
    pages = list(getattr(run, "pages", None) or [])
    if not pages:
        return _chat_findings(run)
    progress.start("answer", "Writing the answer")
    text = ""
    body = notes or _page_notes(pages)
    try:
        result = router.stream(
            "writer",
            _research_answer_system(deep=deep),
            f"Question:\n{question}\n\nNotes:\n{body}",
            progress.partial,
            max_tokens=4000 if deep else 3200,
        )
        text = _normalize_openui_lang(_clean_reply(result.text))
    except LLMError:
        text = ""
    progress.finish("answer")
    text = _guard_research_answer(text, pages, notes)
    return text or _chat_findings(run)


def _open_pages(pages: list, limit: int) -> list:
    from . import scout

    targets = pages[:limit]
    opened = scout.read_pages([page.url for page in targets], limit=limit)
    updated = []
    for page, found in zip(targets, opened, strict=False):
        if found is not None and found.text:
            title = found.title or page.title
            updated.append(page.model_copy(update={"text": found.text, "title": title}))
        else:
            updated.append(page)
    return updated + pages[limit:]


def _follow_phrase(router: Router, question: str, pages: list) -> str:
    try:
        result = router.complete(
            "writer",
            _FOLLOW_SYSTEM,
            f"Question:\n{question}\n\nNotes:\n{_page_notes(pages)}",
            schema=_FOLLOW_SCHEMA,
            max_tokens=80,
        )
    except LLMError:
        return ""
    return " ".join(str((result.data or {}).get("follow") or "").split())[:140]


def _research_message(job: _Job, progress: _Progress) -> None:
    from .deep import chat_fetch, run_research
    from .scout import budget, research_depth

    if not job.settings.get("use_model"):
        run = _run_search(job, progress)
        progress.check()
        _finish_message(job.assistant_id, job.chat_id, _chat_findings(run), "ready", None)
        return
    goal = _search_goal(job)
    depth = research_depth(goal)
    limits = budget(depth)
    router = _router(job.settings)
    router.run_id = store.start_run("research", "web", router.profile)
    progress.run_id = router.run_id
    try:
        progress.start("research", "Research")
        if limits.get("use_tree"):
            progress.start("scout", "Deep research", kind="agent", parent="research")
            deep_run = run_research(goal, depth="deep", connectors=_web_connectors(), fetch=chat_fetch)
            progress.finish("scout", f"{len(deep_run.pages)} pages, {len(deep_run.brief.findings)} findings")
            progress.finish("research", "Deep research")
            notes = _deep_run_notes(deep_run)
            from .deep import ChatResult

            run = ChatResult(query=goal, pages=list(deep_run.pages))
            body = _answer_from_pages(router, goal, run, progress, deep=True, notes=notes)
        else:
            progress.start("scout", "Quick lookup", kind="agent", parent="research")
            phrases = _search_phrases(job, router, limits["queries"])
            progress.finish("scout", "; ".join(phrases))
            runs = []
            page_limit = int(limits.get("page_limit") or 5)
            for index, phrase in enumerate(phrases):
                progress.start(f"q{index}", phrase[:80], kind="agent", parent="research", detail="Searching")
                found = _run_web(phrase, query=phrase, page_limit=page_limit)
                runs.append(found)
                progress.finish(f"q{index}", f"{len(getattr(found, 'pages', None) or [])} pages")
            run = _merge_search(runs)
            if not (getattr(run, "pages", None) or []):
                found = _run_web(goal, page_limit=page_limit)
                run = _merge_search([found])
            pages = list(getattr(run, "pages", None) or [])
            if pages and limits.get("read"):
                progress.start("read", "Reading pages", kind="agent", parent="research")
                pages = _open_pages(pages, limits["read"])
                read = sum(1 for page in pages if page.text)
                progress.finish("read", f"{read} pages opened")
                run = run.model_copy(update={"pages": pages}) if hasattr(run, "model_copy") else run
            if limits.get("follow") and pages:
                progress.start("follow", "Follow up", kind="agent", parent="research")
                extra = _follow_phrase(router, goal, pages)
                if extra and extra.lower() not in {phrase.lower() for phrase in phrases}:
                    found = _run_web(extra, query=extra, page_limit=page_limit)
                    run = _merge_search([run, found])
                    pages = list(getattr(run, "pages", None) or [])
                    if limits.get("read"):
                        pages = _open_pages(pages, limits["read"])
                        run = run.model_copy(update={"pages": pages}) if hasattr(run, "model_copy") else run
                    progress.finish("follow", extra[:80])
                else:
                    progress.finish("follow", "Nothing further")
            progress.finish("research", "Quick lookup")
            body = _answer_from_pages(router, goal, run, progress, deep=False)
    except BaseException:
        store.finish_run(router.run_id, "failed", "", {})
        raise
    store.finish_run(router.run_id, "ok", "", {})
    progress.check()
    _finish_message(job.assistant_id, job.chat_id, body, "ready", None)


def _activity(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _choice_of(settings: dict) -> str:
    if not settings.get("use_model"):
        return "off"
    return "dev" if settings.get("local_only") else (settings.get("profile") or "dev")


def _choice_label(choice: str, cfg: dict) -> str:
    if choice in ("", "off"):
        return "Format only"
    writer = str(((cfg.get("profiles") or {}).get(choice) or {}).get("writer") or "")
    return pretty_model(writer) if writer else PROFILES.get(choice, choice)


def _progress(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _ollama_cached(url: str) -> dict:
    now = time.time()
    hit = _probe_cache.get(url)
    if hit and now - hit[0] < 10:
        return hit[1]
    from .panel import ollama_status

    result = ollama_status(url)
    _probe_cache[url] = (now, result)
    return result


def _reason(exc: Exception) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    if "<para>" in text:
        return "A paragraph had formatting the PDF could not read."
    if len(text) > 220:
        text = text[:217].rsplit(" ", 1)[0] + "..."
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "...")) else text + "."


def _wants_document(text: str, filename: str) -> bool:
    if filename:
        return True
    if len(text) > 500:
        return True
    return bool(_DOC_HINT.search(text))


def _source_text(text: str, upload: tuple[str, bytes] | None, spark: bool) -> str:
    if upload is not None:
        from .uploads import UploadReadError, to_markdown

        try:
            decoded = to_markdown(upload[0], upload[1])
        except UploadReadError as exc:
            raise StudioError(str(exc)) from exc
        if decoded.strip():
            return decoded
    cleaned = text.strip()
    if cleaned.startswith("---") or cleaned.startswith("#"):
        return cleaned + ("\n" if not cleaned.endswith("\n") else "")
    title = _title_from(cleaned)
    fm = {
        "title": title,
        "doc_type": "DOCUMENTATION",
        "include_spark_content": bool(spark),
        "archetype": "technical_document",
    }
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n" + cleaned + "\n"


def _safe_name(name: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", Path(str(name or "")).name).strip(" .")
    return clean[:120] or "upload.txt"


def _check_id(value: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{12}", value or ""):
        raise StudioError("not found")


def _title_from(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if line:
            return " ".join(line.split())[:80]
    return "Untitled"


def file_stem(title: str, version: int) -> str:
    clean = re.sub(r"[^\w .,()&]+", " ", title or "").strip()
    clean = " ".join(clean.split())[:80] or "Document"
    return f"{clean} v{version}" if version and int(version) > 1 else clean


def _plain_text(body: str) -> str:
    text = body
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4 :] if end != -1 else text
    text = re.sub(r"(?m)^#\s+\d{2}\s+.*$", "", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+", "", text)
    text = re.sub(r"(?m)^:::\w*.*$|^```.*$|<!--.*?-->", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)|\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`|>]+", " ", text)
    return " ".join(text.split())


def _snippet(body: str, query: str) -> str:
    flat = _plain_text(body)
    if not query:
        return flat[:140]
    index = flat.lower().find(query.lower())
    if index < 0:
        return flat[:140]
    start = max(0, index - 40)
    clip = flat[start : index + len(query) + 80]
    return clip.strip()


def _render_pages(pdf: Path, dest: Path) -> int:
    import pymupdf

    dest.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf)
    try:
        count = doc.page_count
        for index in range(count):
            pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(1.35, 1.35), alpha=False)
            pix.save(dest / f"{index + 1}.png")
        return count
    finally:
        doc.close()
