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
    from .mcp_hub import hub

    hub().shutdown()


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
    agent: bool | None = None
    agent_local: bool | None = None
    openai_model: str | None = None
    openai_fast_model: str | None = None
    reasoning_effort: str | None = None
    search_context: str | None = None
    hosted_search: bool | None = None
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


class DraftBody(BaseModel):
    markdown: str
    title: str | None = None


class PublishBody(BaseModel):
    client_pages: bool | None = None
    model: str | None = None


class SourceBody(BaseModel):
    markdown: str


class DiagramBody(BaseModel):
    source: str


class McpBody(BaseModel):
    servers: list[dict]


_OPEN = {"/api/health", "/api/session", "/api/login"}


_DEV_ORIGINS = {"127.0.0.1:5173", "localhost:5173"}


def _foreign(request: Request) -> bool:
    """A write sent by another site's page. Browsers name the page in Origin; the app's own pages match the host."""
    origin = request.headers.get("origin")
    if not origin or request.method in {"GET", "HEAD", "OPTIONS"}:
        return False
    from urllib.parse import urlsplit

    netloc = urlsplit(origin).netloc
    return netloc != request.headers.get("host", "") and netloc not in _DEV_ORIGINS


@app.middleware("http")
async def _app_password(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api") and _foreign(request):
        return JSONResponse({"detail": "Requests from other sites are refused."}, status_code=403)
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
        effort = str(data.get("effort") or "") or None
        agent_model = str(data.get("agent_model") or "") or None
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
        effort = str(form.get("effort") or "") or None
        agent_model = str(form.get("agent_model") or "") or None
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
            effort=effort,
            agent_model=agent_model,
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


@app.get("/api/openai/models")
def openai_models(refresh: bool = False) -> dict:
    from .providers import list_openai_models, resolve_openai_model

    settings = studio.load_settings()
    found = list_openai_models(refresh=refresh)
    return {
        "models": found,
        "main": resolve_openai_model(settings.get("openai_model") or "") if found else "",
        "fast": resolve_openai_model(settings.get("openai_fast_model") or "", fast=True) if found else "",
    }


# drafts (the canvas) ---------------------------------------------------------------


def _draft_or_404(draft_id: str) -> dict:
    from . import workspace

    try:
        return workspace.get_draft(draft_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/drafts/{draft_id}")
def draft(draft_id: str) -> dict:
    from . import workspace

    item = _draft_or_404(draft_id)
    item["versions"] = workspace.draft_versions(draft_id)
    item["latest_id"] = workspace.latest_in(item["root_id"])["id"]
    return item


@app.put("/api/drafts/{draft_id}")
def save_draft(draft_id: str, body: DraftBody) -> dict:
    from . import workspace

    _draft_or_404(draft_id)
    try:
        saved = workspace.save_version(draft_id, body.markdown, title=body.title, note="Edited by hand", author="user")
    except workspace.WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    saved["versions"] = workspace.draft_versions(saved["id"])
    saved["latest_id"] = saved["id"]
    return saved


@app.post("/api/drafts/{draft_id}/publish")
def publish(draft_id: str, body: PublishBody | None = None) -> dict:
    item = _draft_or_404(draft_id)
    try:
        return studio.publish_draft(item["chat_id"], draft_id, spark=body.client_pages if body else None, model=body.model if body else None)
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.post("/api/diagram")
def diagram(body: DiagramBody):
    from fastapi.responses import Response

    try:
        svg = studio.diagram_svg(body.source)
    except studio.StudioError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=86400"})


@app.delete("/api/chats/{chat_id}/notes/{note_id}")
def drop_note(chat_id: str, note_id: str) -> dict:
    from . import workspace

    if not workspace.drop_note(chat_id, note_id):
        raise HTTPException(404, "note not found")
    return {"ok": True}


# MCP servers -----------------------------------------------------------------------


@app.get("/api/mcp")
def mcp_servers() -> dict:
    from .mcp_hub import hub

    return {"servers": hub().status()}


@app.put("/api/mcp")
def put_mcp(body: McpBody) -> dict:
    from .mcp_hub import MCPConfigError, hub, save_servers

    try:
        save_servers(body.servers)
    except MCPConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    panel.log_activity("mcp", f"{len(body.servers)} servers")
    return {"servers": hub().status()}


@app.post("/api/mcp/{name}/test")
def test_mcp(name: str) -> dict:
    from .mcp_hub import MCPConfigError, hub

    try:
        return hub().test(name)
    except MCPConfigError as exc:
        raise HTTPException(404, str(exc)) from exc


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


@app.get("/api/documents/{doc_id}/source")
def document_source(doc_id: str) -> dict:
    try:
        doc = studio.get_document(doc_id)
        return {"id": doc_id, "markdown": studio.document_markdown(doc)}
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.put("/api/documents/{doc_id}/source")
def put_document_source(doc_id: str, body: SourceBody) -> dict:
    try:
        doc = studio.get_document(doc_id)
        return studio.save_source(doc["chat_id"], doc_id, body.markdown)
    except studio.StudioError as exc:
        raise _fail(exc) from exc


@app.post("/api/documents/{doc_id}/restore")
def restore(doc_id: str) -> dict:
    try:
        doc = studio.restore_version(doc_id)
        return studio.get_chat(doc["chat_id"])
    except studio.StudioError as exc:
        raise _fail(exc) from exc


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
    if body.name == "openai":
        from .providers import list_openai_models

        def probe(_name: str) -> dict:
            found = list_openai_models(refresh=True)
            if not found:
                return {"ok": False, "detail": "OpenAI did not accept the key, or it has no chat models"}
            return {"ok": True, "detail": f"Works. {len(found)} models, newest {found[0]}"}

        try:
            return secrets.test_key(body.name, probe=probe)
        except secrets.SecretError as exc:
            raise HTTPException(400, str(exc)) from exc
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
