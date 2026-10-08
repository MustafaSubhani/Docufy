"""Docufy server. Binds to this machine only."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import ops, panel, secrets, store, studio


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    ops.startup()
    yield


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "dist"

app = FastAPI(title="Docufy", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SettingsBody(BaseModel):
    profile: str | None = None
    use_model: bool | None = None
    spark_content: bool | None = None
    per_run_usd: float | None = None
    monthly_usd: float | None = None
    hard_stop: bool | None = None
    log_level: str | None = None
    retention_days: int | None = None
    local_only: bool | None = None
    num_ctx: int | None = None
    keep_alive: str | None = None
    ollama_url: str | None = None
    password: str | None = None
    clear_password: bool | None = None


class LoginBody(BaseModel):
    password: str = ""


class RenameBody(BaseModel):
    title: str = ""


class RetryBody(BaseModel):
    model: str | None = None


class KeyBody(BaseModel):
    name: str
    secret: str = ""


_OPEN = {"/api/health", "/api/session", "/api/login"}


@app.middleware("http")
async def _app_password(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api") or path in _OPEN:
        return await call_next(request)
    if ops.request_allowed(request.cookies.get("docufy_session", "")):
        return await call_next(request)
    return JSONResponse({"detail": "Enter the app password."}, status_code=401)


def _fail(exc: studio.StudioError) -> HTTPException:
    status = 404 if "not found" in str(exc) else 400
    return HTTPException(status, str(exc))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/session")
def session(request: Request) -> dict:
    required = ops.has_password()
    ok = ops.request_allowed(request.cookies.get("docufy_session", ""))
    return {"required": required, "ok": ok}


@app.post("/api/login")
def login(body: LoginBody) -> JSONResponse:
    if not ops.check_password(body.password):
        raise HTTPException(401, "That password is not right.")
    response = JSONResponse({"ok": True})
    response.set_cookie("docufy_session", ops.session_token(), httponly=True, samesite="lax")
    return response


@app.get("/api/chats")
def chats(q: str = "") -> dict:
    return {"chats": studio.list_chats(q)}


@app.post("/api/chats")
def new_chat() -> dict:
    return studio.create_chat()


@app.get("/api/chats/{chat_id}")
def chat(chat_id: str) -> dict:
    try:
        return studio.get_chat(chat_id)
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: str, body: RenameBody) -> dict:
    try:
        return studio.rename_chat(chat_id, body.title)
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.delete("/api/chats/{chat_id}")
def remove_chat(chat_id: str) -> dict:
    studio.delete_chat(chat_id)
    return {"ok": True}


@app.post("/api/chats/{chat_id}/messages")
async def message(chat_id: str, request: Request) -> dict:
    upload = None
    spark: bool | None = None
    web = False
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        text = str(data.get("text") or "")
        if "spark_content" in data and data["spark_content"] is not None:
            spark = bool(data["spark_content"])
        web = bool(data.get("web"))
        model = str(data.get("model") or "") or None
        document_id = str(data.get("document_id") or "") or None
        raw_sections = data.get("section_ids")
        section_ids = [str(item) for item in raw_sections] if isinstance(raw_sections, list) else None
    else:
        form = await request.form()
        text = str(form.get("text") or "")
        flag = form.get("spark_content")
        if flag is not None:
            spark = str(flag).lower() in {"1", "true", "on", "yes"}
        web = str(form.get("web") or "").lower() in {"1", "true", "on", "yes"}
        model = str(form.get("model") or "") or None
        document_id = str(form.get("document_id") or "") or None
        raw_sections = form.get("section_ids")
        section_ids = None
        if raw_sections is not None:
            text_sections = str(raw_sections).strip()
            if text_sections:
                try:
                    parsed = json.loads(text_sections)
                    section_ids = [str(item) for item in parsed] if isinstance(parsed, list) else None
                except json.JSONDecodeError:
                    section_ids = None
        uploaded = form.get("file")
        if uploaded is not None and getattr(uploaded, "filename", ""):
            upload = (Path(uploaded.filename).name, await uploaded.read(studio.MAX_UPLOAD + 1))
    try:
        return studio.send_message(
            chat_id,
            text,
            spark_content=spark,
            upload=upload,
            model=model,
            document_id=document_id,
            section_ids=section_ids,
            web=web,
        )
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.post("/api/chats/{chat_id}/messages/{message_id}/stop")
def stop(chat_id: str, message_id: str) -> dict:
    try:
        return studio.stop_message(chat_id, message_id)
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.post("/api/chats/{chat_id}/messages/{message_id}/retry")
def retry(chat_id: str, message_id: str, body: RetryBody | None = None) -> dict:
    try:
        return studio.retry_message(chat_id, message_id, (body.model if body else None) or None)
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.get("/api/chats/{chat_id}/export")
def export(chat_id: str) -> FileResponse:
    try:
        chat = studio.get_chat(chat_id)
    except studio.StudioError as exc:
        raise _fail(exc) from exc
    folder = Path(tempfile.mkdtemp(prefix="docufy-export-"))
    path = ops.export_chat(chat_id, folder / "chat.zip")
    name = studio.file_stem(chat["title"], 1) + ".zip"
    return FileResponse(path, filename=name, media_type="application/zip", background=BackgroundTask(shutil.rmtree, folder, True))


@app.post("/api/import")
async def import_chat(request: Request) -> dict:
    form = await request.form()
    uploaded = form.get("file")
    if uploaded is None or not getattr(uploaded, "filename", ""):
        raise HTTPException(400, "attach a chat zip")
    data = await uploaded.read(200 * 1024 * 1024 + 1)
    if not data.startswith(b"PK"):
        raise HTTPException(400, "that file is not a chat zip")
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "chat.zip"
        path.write_bytes(data)
        try:
            return ops.import_project(path)
        except (ops.OpsError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/models")
def models() -> dict:
    return studio.model_options()


@app.get("/api/documents")
def documents(q: str = "", spark: str = "") -> dict:
    return {"documents": studio.list_documents(q, spark)}


@app.get("/api/documents/{doc_id}")
def document(doc_id: str) -> dict:
    try:
        doc = studio.get_document(doc_id)
        doc["versions"] = studio.versions(doc_id)
        doc["sections"] = studio.document_sections(doc_id)
    except studio.StudioError as exc:
        raise _fail(exc) from exc
    return {key: value for key, value in doc.items() if key not in {"pdf_path", "docx_path", "md_path"}}


@app.get("/api/documents/{doc_id}/pages/{number}.png")
def page(doc_id: str, number: int) -> FileResponse:
    try:
        path = studio.page_file(doc_id, number)
    except studio.StudioError as exc:
        raise _fail(exc) from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.get("/api/documents/{doc_id}/download")
def download(doc_id: str, kind: str = "pdf") -> FileResponse:
    try:
        path, name, media = studio.download_path(doc_id, kind)
    except studio.StudioError as exc:
        raise _fail(exc) from exc
    return FileResponse(path, filename=name, media_type=media)


@app.get("/api/backups")
def backups() -> dict:
    return {"backups": ops.list_backups()}


@app.post("/api/backups")
def make_backup() -> dict:
    path = ops.backup_workspace()
    panel.log_activity("backup", "workspace backup")
    return {"name": path.name, "bytes": path.stat().st_size}


@app.get("/api/backups/{name}")
def get_backup(name: str) -> FileResponse:
    try:
        path = ops.backup_file(name)
    except ops.OpsError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/api/ready")
def ready() -> dict:
    checks = ops.ready_checks()
    return {"ok": ops.ready_ok(checks), "checks": checks}


@app.get("/api/settings")
def settings() -> dict:
    return studio.public_settings()


@app.put("/api/settings")
def put_settings(body: SettingsBody) -> dict:
    try:
        return studio.save_settings(body.model_dump(exclude_none=True))
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0) -> dict:
    return {"events": store.events_since(run_id, after)}


@app.get("/api/runs/{run_id}/stream")
def run_stream(run_id: str, request: Request) -> StreamingResponse:
    after = int(request.headers.get("last-event-id") or 0)

    def gen():
        for item in store.events_since(run_id, after):
            yield f"id: {item['id']}\ndata: {json.dumps(item)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/usage")
def usage() -> dict:
    return studio.usage_summary()


@app.get("/api/usage.csv")
def usage_csv():
    from fastapi.responses import Response

    return Response(panel.usage_csv(), media_type="text/csv")


@app.get("/api/panel")
def panel_view() -> dict:
    return panel.snapshot(studio.load_settings()["profile"])


@app.get("/api/settings/keys")
def keys() -> dict:
    return {"keys": secrets.status()}


@app.put("/api/settings/keys")
def put_key(body: KeyBody) -> dict:
    try:
        return secrets.put(body.name, body.secret)
    except secrets.SecretError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/settings/keys/test")
def test_key(body: KeyBody) -> dict:
    try:
        return secrets.test_key(body.name)
    except secrets.SecretError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/memory/{memory_id}/confirm")
def confirm_memory(memory_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{12}", memory_id):
        raise HTTPException(404, "not found")
    from .memory import MemoryError, confirm

    try:
        confirm(memory_id)
    except MemoryError as exc:
        raise HTTPException(404, str(exc)) from exc
    panel.log_activity("memory", f"confirmed {memory_id}")
    return {"ok": True}


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="studio")
