"""Local production helpers: backups, recovery, uploads, and the first-run check.

The server binds to this machine only. API keys stay in Credential Manager.
An optional app password is stored as a hash beside the studio settings.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets as token_source
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import yaml

from . import store, studio
from .migrate import VERSION
from .version import __version__

BIND_HOST = "127.0.0.1"
BACKUP_KEEP = 7
_PBKDF_ROUNDS = 60_000
ROOT = Path(__file__).resolve().parent.parent


class OpsError(RuntimeError):
    pass


class UploadError(OpsError):
    pass


def startup(*, backup: bool = False) -> list[str]:
    """Open the database, mark crashed runs resumable, clear old logs, and take a backup when one is due."""
    store.conn()
    recovered = recover_runs()
    studio.recover_messages()
    studio.purge_old(studio.load_settings()["retention_days"])
    if backup:
        try:
            maybe_backup()
        except OSError:
            pass
    note = note_update()
    if note:
        from .panel import log_activity

        log_activity("update", note)
    return recovered


def recover_runs() -> list[str]:
    """A run left in `running` did not finish. Keep its checkpoint and do not call the model again."""
    rows = store.conn().execute("SELECT id FROM runs WHERE status='running'").fetchall()
    recovered: list[str] = []
    for (run_id,) in rows:
        checkpoint = store.load_checkpoint(run_id)
        if checkpoint and checkpoint.get("node") != "done":
            store.finish_run(run_id, "interrupted", "", {"node": checkpoint.get("node"), "recovered": True})
        else:
            store.finish_run(run_id, "failed", "", {"recovered": True, "reason": "no checkpoint"})
        recovered.append(run_id)
    return recovered


def sniff_upload(name: str, data: bytes) -> None:
    """Reject a file whose bytes do not match its name."""
    if not data:
        raise UploadError("that file is empty")
    if data.startswith((b"MZ", b"\x7fELF")):
        raise UploadError("that file is not a document")
    suffix = Path(name).suffix.lower()
    if suffix in {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml"}:
        if b"\x00" in data[:4096]:
            raise UploadError("that text file looks binary")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UploadError("that text file is not UTF-8") from exc
        return
    if suffix == ".webp":
        if not (data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP"):
            raise UploadError("that file does not match its .webp name")
        return
    expected = {
        ".pdf": b"%PDF",
        ".png": b"\x89PNG",
        ".jpg": b"\xff\xd8\xff",
        ".jpeg": b"\xff\xd8\xff",
        ".gif": b"GIF8",
        ".docx": b"PK\x03\x04",
        ".xlsx": b"PK\x03\x04",
        ".pptx": b"PK\x03\x04",
    }.get(suffix)
    if expected is None:
        raise UploadError(f"unsupported file type {suffix or name}")
    if not data.startswith(expected):
        raise UploadError(f"that file does not match its {suffix} name")


def backup_workspace(dest: Path | None = None) -> Path:
    folder = store.DB_PATH.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = Path(dest) if dest else folder / f"workspace-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as raw:
        snapshot = Path(raw) / "spark.db"
        _snapshot_db(snapshot)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "spark.db")
            _zip_tree(archive, studio.studio_dir(), "studio")
            for name in ("memory", "charts", "images", "pages"):
                _zip_tree(archive, store.DB_PATH.parent / name, name)
    return target


def list_backups() -> list[dict]:
    folder = store.DB_PATH.parent / "backups"
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("workspace-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        rows.append({"name": path.name, "bytes": stat.st_size, "created_at": stat.st_mtime})
    return rows


def backup_file(name: str) -> Path:
    if not re.fullmatch(r"workspace-[\w.-]+\.zip", name or ""):
        raise OpsError("backup not found")
    path = store.DB_PATH.parent / "backups" / name
    if not path.is_file():
        raise OpsError("backup not found")
    return path


def maybe_backup(now: float | None = None, hours: float = 24) -> Path | None:
    folder = store.DB_PATH.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    existing = sorted(folder.glob("workspace-*.zip"), key=lambda path: path.stat().st_mtime)
    moment = time.time() if now is None else now
    if existing and moment - existing[-1].stat().st_mtime < hours * 3600:
        return None
    path = backup_workspace(folder / f"workspace-{time.strftime('%Y%m%d-%H%M%S')}.zip")
    _trim(folder)
    return path


def restore_workspace(zip_path: Path) -> None:
    archive_path = Path(zip_path)
    if not archive_path.is_file():
        raise OpsError(f"not found: {archive_path}")
    with tempfile.TemporaryDirectory() as raw:
        temp = Path(raw)
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, temp)
        database = temp / "spark.db"
        if not database.is_file():
            raise OpsError("that archive has no database")
        _close_store()
        store.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            side = Path(str(store.DB_PATH) + suffix)
            if side.exists():
                side.unlink()
        shutil.copy2(database, store.DB_PATH)
        extracted = temp / "studio"
        if extracted.is_dir():
            destination = studio.studio_dir()
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(extracted, destination)
        store.conn()


def export_chat(chat_id: str, dest: Path) -> Path:
    chat = studio.get_chat(chat_id)
    documents = []
    with tempfile.TemporaryDirectory() as raw:
        temp = Path(raw)
        for index, row in enumerate(_document_rows(chat_id)):
            folder = f"docs/{index}"
            entry = {
                "title": row["title"],
                "body": row["body"],
                "pages": row["pages"],
                "spark_content": bool(row["spark_content"]),
                "version": row["version"],
                "pdf": _stage_file(temp, row["pdf_path"], f"{folder}/document.pdf"),
                "docx": _stage_file(temp, row["docx_path"], f"{folder}/document.docx"),
                "md": _stage_file(temp, row["md_path"], f"{folder}/notes.spark.md"),
                "page_images": _stage_pages(temp, row["pdf_path"], f"{folder}/pages"),
            }
            documents.append(entry)
        manifest = {
            "kind": "spark-project",
            "version": 1,
            "title": chat["title"],
            "messages": [
                {"role": item["role"], "body": item["body"], "status": item["status"]}
                for item in chat["messages"]
            ],
            "documents": documents,
        }
        (temp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in temp.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(temp).as_posix())
    return dest


def import_project(zip_path: Path) -> dict:
    archive_path = Path(zip_path)
    with tempfile.TemporaryDirectory() as raw:
        temp = Path(raw)
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, temp)
        manifest_path = temp / "manifest.json"
        if not manifest_path.is_file():
            raise OpsError("that archive has no project manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("kind") != "spark-project":
            raise OpsError("that archive is not a Spark project")
        studio.ensure()
        now = time.time()
        chat_id = uuid.uuid4().hex[:12]
        title = str(manifest.get("title") or "Imported chat")[:80]
        with store._lock:
            store.conn().execute(
                "INSERT INTO chats (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                (chat_id, title, now, now),
            )
            for offset, item in enumerate(manifest.get("messages") or []):
                store.conn().execute(
                    "INSERT INTO messages (id, chat_id, role, body, status, document_id, created_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex[:12],
                        chat_id,
                        str(item.get("role") or "user"),
                        str(item.get("body") or ""),
                        str(item.get("status") or "ready"),
                        None,
                        now + offset / 1000,
                    ),
                )
            for item in manifest.get("documents") or []:
                doc_id = uuid.uuid4().hex[:12]
                folder = studio.studio_dir() / "docs" / doc_id
                folder.mkdir(parents=True, exist_ok=True)
                pdf = _copy_member(temp, item.get("pdf"), folder / "document.pdf")
                docx = _copy_member(temp, item.get("docx"), folder / "document.docx")
                md = _copy_member(temp, item.get("md"), folder / "notes.spark.md")
                for index, member in enumerate(item.get("page_images") or [], start=1):
                    _copy_member(temp, member, folder / "pages" / f"{index}.png")
                store.conn().execute(
                    """INSERT INTO documents (
                        id, chat_id, message_id, title, body, pdf_path, docx_path, pages, spark_content, created_at,
                        version, root_id, md_path
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        doc_id,
                        chat_id,
                        "",
                        str(item.get("title") or title),
                        str(item.get("body") or "")[:20000],
                        str(pdf or ""),
                        str(docx or ""),
                        int(item.get("pages") or 0),
                        1 if item.get("spark_content") else 0,
                        now,
                        1,
                        doc_id,
                        str(md or ""),
                    ),
                )
            store.conn().commit()
    return studio.get_chat(chat_id)


def has_password() -> bool:
    return bool(_read_hash())


def set_password(secret: str) -> None:
    secret = (secret or "").strip()
    if len(secret) < 8 or len(secret) > 200:
        raise OpsError("Use at least 8 characters.")
    salt = token_source.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), _PBKDF_ROUNDS).hex()
    _write_auth(f"pbkdf2${salt}${digest}")
    from .panel import log_activity

    log_activity("settings", "password set")


def clear_password() -> None:
    path = _auth_path()
    if path.is_file():
        path.unlink()
    from .panel import log_activity

    log_activity("settings", "password removed")


def check_password(secret: str) -> bool:
    stored = _read_hash()
    if not stored:
        return False
    try:
        scheme, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    got = hashlib.pbkdf2_hmac("sha256", (secret or "").encode(), bytes.fromhex(salt), _PBKDF_ROUNDS).hex()
    return hmac.compare_digest(got, digest)


def session_token() -> str:
    stored = _read_hash()
    if not stored:
        return ""
    return hashlib.sha256(b"spark-session:" + stored.encode()).hexdigest()


def request_allowed(cookie: str) -> bool:
    if not has_password():
        return True
    expected = session_token()
    if not cookie or not expected:
        return False
    return hmac.compare_digest(cookie, expected)


def ready_checks(*, ollama=None, keys=None) -> list[dict]:
    checks = [
        _python_check(),
        _workspace_check(),
        _database_check(),
        _font_check(),
        _node_check(),
        _diagram_check(),
        _browser_check(),
        _ollama_check(ollama),
        _key_check(keys),
    ]
    return checks


def ready_ok(checks: list[dict]) -> bool:
    return all(item["ok"] or not item["required"] for item in checks)


def note_update() -> str:
    path = store.DB_PATH.parent / "version.json"
    current = {"app": __version__, "theme": _theme_version()}
    previous: dict = {}
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    path.write_text(json.dumps(current), encoding="utf-8")
    if previous and (previous.get("app") != current["app"] or previous.get("theme") != current["theme"]):
        return f"updated to app {current['app']}"
    return ""


def _python_check() -> dict:
    ok = sys.version_info >= (3, 12)
    return _check(
        "Python",
        ok,
        True,
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "" if ok else "Install Python 3.12 or newer.",
    )


def _workspace_check() -> dict:
    try:
        store.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        probe = store.DB_PATH.parent / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return _check("Workspace", True, True, str(store.DB_PATH.parent), "")
    except OSError as exc:
        return _check("Workspace", False, True, str(exc), "Choose a folder this user can write.")


def _database_check() -> dict:
    try:
        mode = store.conn().execute("PRAGMA journal_mode").fetchone()[0]
        version = store.conn().execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0]
        ok = str(mode).lower() == "wal" and int(version) == VERSION
        detail = f"WAL, schema {version}"
        fix = "" if ok else "Restart the studio so the database can migrate."
        return _check("Database", ok, True, detail, fix)
    except sqlite3.Error as exc:
        return _check("Database", False, True, str(exc), "Restart the studio so the database can migrate.")


def _font_check() -> dict:
    from spark_docs.fonts import WIN_FONTS

    arial = WIN_FONTS / "arial.ttf"
    if arial.is_file():
        return _check("Fonts", True, False, "Arial", "")
    return _check("Fonts", True, False, "Helvetica fallback", "Install Arial if you want the Spark documents to match the reference.")


def _node_check() -> dict:
    node = shutil.which("node")
    if node:
        return _check("Node", True, False, node, "")
    return _check("Node", False, False, "missing", "Install Node 20 or newer for the diagram worker and the studio page.")


def _diagram_check() -> dict:
    elk = ROOT / "diagrams" / "node_worker" / "node_modules" / "elkjs"
    if elk.is_dir():
        return _check("Diagrams", True, False, "elkjs installed", "")
    return _check("Diagrams", False, False, "missing", "Run npm install in diagrams/node_worker.")


def _browser_check() -> dict:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]
    found = next((path for path in candidates if path.is_file()), None)
    if found:
        return _check("Browser", True, False, found.name, "")
    return _check("Browser", False, False, "missing", "Install Edge or Chrome if you want page captures.")


def _ollama_check(probe) -> dict:
    if probe is None:
        from .panel import ollama_status

        result = ollama_status(studio.load_settings().get("ollama_url") or "http://127.0.0.1:11434")
    else:
        result = probe()
    ok = bool(result.get("ok"))
    return _check(
        "Ollama",
        ok,
        False,
        str(result.get("detail") or ""),
        "" if ok else "Start Ollama, or save a Claude or OpenAI key in Settings.",
    )


def _key_check(keys) -> dict:
    if keys is None:
        from . import secrets

        rows = secrets.status()
    else:
        rows = keys
    stored = [row["label"] for row in rows if row.get("set")]
    detail = ", ".join(stored) if stored else "none stored"
    return _check("Keys", True, False, detail, "Save a provider key in Settings when you leave the local profile.")


def _check(name: str, ok: bool, required: bool, detail: str, fix: str) -> dict:
    return {"name": name, "ok": ok, "required": required, "detail": detail, "fix": fix}


def _theme_version() -> int:
    path = ROOT / "themes" / "spark" / "tokens.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return int(data.get("version") or 1)


def _snapshot_db(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(dest)
    try:
        store.conn().backup(out)
    finally:
        out.close()


def _zip_tree(archive: zipfile.ZipFile, folder: Path, prefix: str) -> None:
    if not folder.is_dir():
        return
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if "backups" in path.parts:
            continue
        archive.write(path, f"{prefix}/{path.relative_to(folder).as_posix()}")


def _trim(folder: Path) -> None:
    existing = sorted(folder.glob("workspace-*.zip"), key=lambda path: path.stat().st_mtime)
    for path in existing[:-BACKUP_KEEP]:
        path.unlink(missing_ok=True)


def _close_store() -> None:
    if store._conn is not None:
        store._conn.close()
        store._conn = None


def _safe_extract(archive: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in archive.infolist():
        target = (dest / info.filename).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise OpsError("that archive has an unsafe path") from exc
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)


def _document_rows(chat_id: str) -> list[dict]:
    studio.ensure()
    rows = store.conn().execute(
        "SELECT d.title, d.body, d.pdf_path, d.docx_path, d.pages, d.spark_content, d.version, d.md_path FROM documents d "
        f"WHERE d.chat_id=? AND {studio.LATEST} ORDER BY d.created_at",
        (chat_id,),
    ).fetchall()
    return [
        {
            "title": row[0],
            "body": row[1],
            "pdf_path": row[2],
            "docx_path": row[3],
            "pages": row[4],
            "spark_content": row[5],
            "version": row[6] or 1,
            "md_path": row[7] or "",
        }
        for row in rows
    ]


def _stage_pages(temp: Path, pdf_path: str | None, prefix: str) -> list[str]:
    if not pdf_path:
        return []
    folder = Path(pdf_path).parent / "pages"
    staged = []
    index = 1
    while (folder / f"{index}.png").is_file():
        member = _stage_file(temp, str(folder / f"{index}.png"), f"{prefix}/{index}.png")
        if member:
            staged.append(member)
        index += 1
    return staged


def _stage_file(temp: Path, source: str | None, member: str) -> str | None:
    if not source or not Path(source).is_file():
        return None
    dest = temp / member
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return member


def _copy_member(temp: Path, member: str | None, dest: Path) -> Path | None:
    if not member:
        return None
    source = (temp / member).resolve()
    try:
        source.relative_to(temp.resolve())
    except ValueError as exc:
        raise OpsError("that archive has an unsafe path") from exc
    if not source.is_file():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def _auth_path() -> Path:
    return studio.studio_dir() / "auth.json"


def _read_hash() -> str:
    path = _auth_path()
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(data.get("hash") or "")


def _write_auth(password_hash: str) -> None:
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hash": password_hash}), encoding="utf-8")
