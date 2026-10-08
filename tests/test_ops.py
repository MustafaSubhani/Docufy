import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from spark_agent import memory, panel, server, store, studio
from spark_agent.__main__ import main
from spark_agent.graph import resume_convert, start_convert
from spark_agent.llm import LLMError, Router
from spark_agent.ops import (
    BIND_HOST,
    OpsError,
    UploadError,
    backup_workspace,
    export_chat,
    import_project,
    maybe_backup,
    ready_checks,
    ready_ok,
    recover_runs,
    restore_workspace,
    sniff_upload,
)


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPARK_STUDIO_DIR", str(tmp_path / "studio"))
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "spark.db")
    monkeypatch.setattr(store, "_conn", None)


def _off(model, messages, schema):
    raise LLMError("the model is turned off")


def test_a_fresh_database_uses_wal_and_skips_the_migration_copy(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert store.conn().execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.conn().execute("SELECT version FROM schema_version").fetchone()[0] == 2
    assert list((tmp_path / "backups").glob("pre-migrate-*.db")) == []


def test_an_old_database_is_copied_before_columns_are_added(tmp_path, monkeypatch):
    db = tmp_path / "spark.db"
    raw = sqlite3.connect(db)
    raw.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, kind TEXT, status TEXT)")
    raw.execute("INSERT INTO runs VALUES ('abc', 'convert', 'ok')")
    raw.commit()
    raw.close()
    monkeypatch.setattr(store, "DB_PATH", db)
    monkeypatch.setattr(store, "_conn", None)
    columns = {row[1] for row in store.conn().execute("PRAGMA table_info(runs)")}
    assert "user_id" in columns
    assert list((tmp_path / "backups").glob("pre-migrate-*.db"))
    assert store.conn().execute("SELECT version FROM schema_version").fetchone()[0] == 2


def test_a_crashed_run_stays_resumable_and_is_not_charged_again(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    source = tmp_path / "notes.md"
    source.write_text("# Fleet survey\n\nThe north wall needs a second pass.\n", encoding="utf-8")
    router = Router(profile="dev", fake=_off)
    started = start_convert(source, router, approve=False, render=False, out_dir=tmp_path / "out")
    store.set_status(started.run_id, "running")
    before = store.conn().execute("SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()
    assert recover_runs() == [started.run_id]
    assert store.conn().execute("SELECT status FROM runs WHERE id=?", (started.run_id,)).fetchone()[0] == "interrupted"
    after = store.conn().execute("SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()
    assert after == before
    finished = resume_convert(started.run_id, router)
    assert finished.status in {"ok", "ok_with_issues"}
    assert store.load_checkpoint(started.run_id)["node"] == "done"


def test_events_remain_when_the_stream_closes(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    store.event("run1", "token", {"n": 1})
    store.event("run1", "token", {"n": 2})
    client = TestClient(server.app)
    first = client.get("/api/runs/run1/stream")
    assert first.status_code == 200
    client.close()
    assert [item["data"]["n"] for item in store.events_since("run1", 0)] == [1, 2]


def test_uploads_must_match_their_type_and_a_program_is_refused(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    sniff_upload("notes.md", b"# Fleet survey\n")
    with pytest.raises(UploadError, match="does not match"):
        sniff_upload("notes.pdf", b"not a pdf")
    chat = studio.create_chat()
    with pytest.raises(studio.StudioError, match="not a document"):
        studio.send_message(chat["id"], "Hello", upload=("notes.pdf", b"MZ\x90\x00\x03"), wait=True)


def test_backup_restore_and_project_zip_round_trip(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    chat = studio.create_chat()
    studio.send_message(chat["id"], "Hello from the fleet survey.", wait=True)
    archive = backup_workspace(tmp_path / "backups" / "manual.zip")
    store.conn().execute("DELETE FROM chats")
    store.conn().commit()
    assert studio.list_chats() == []
    restore_workspace(archive)
    assert studio.list_chats()[0]["id"] == chat["id"]

    exported = export_chat(chat["id"], tmp_path / "project.zip")
    studio.delete_chat(chat["id"])
    imported = import_project(exported)
    assert imported["title"] == "Hello from the fleet survey."
    assert imported["id"] != chat["id"]
    assert imported["messages"]

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as handle:
        handle.writestr("../evil.txt", "no")
    with pytest.raises(OpsError, match="unsafe"):
        import_project(bad)


def test_a_second_backup_waits_and_the_host_stays_local(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    store.conn()
    assert maybe_backup() is not None
    assert maybe_backup() is None
    assert BIND_HOST == "127.0.0.1"


def test_the_app_password_is_a_hash_and_gates_the_api(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    with pytest.raises(studio.StudioError, match="8 characters"):
        studio.save_settings({"password": "short"})
    saved = studio.save_settings({"password": "correct horse"})
    assert saved["has_password"] is True
    stored = (tmp_path / "studio" / "settings.json").read_text(encoding="utf-8")
    visible = stored + json.dumps(panel.activity()) + (tmp_path / "studio" / "auth.json").read_text(encoding="utf-8")
    assert "correct horse" not in visible
    client = TestClient(server.app)
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/health").status_code == 200
    denied = client.post("/api/login", json={"password": "wrong password"})
    assert denied.status_code == 401
    assert client.post("/api/login", json={"password": "correct horse"}).status_code == 200
    assert client.get("/api/settings").status_code == 200
    studio.save_settings({"clear_password": True})
    assert studio.public_settings()["has_password"] is False


def test_deleting_a_chat_removes_its_memory_and_its_log_line(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    chat = studio.create_chat()
    memory.remember("fact", "North wall gate", scope="project", scope_key=chat["id"])
    panel.log_activity("note", chat["id"])
    studio.delete_chat(chat["id"])
    assert all(row["scope_key"] != chat["id"] for row in memory.list_memories())
    assert chat["id"] not in json.dumps(panel.activity())
    assert any(row["detail"] == "deleted a chat" for row in panel.activity())


def test_the_first_run_check_passes_when_local_services_answer(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    checks = ready_checks(ollama=lambda: {"ok": True, "detail": "reachable"}, keys=[{"label": "OpenAI", "set": False}])
    assert ready_ok(checks)
    assert any(item["name"] == "Database" and item["ok"] for item in checks)
    monkeypatch.setattr("spark_agent.ops.ready_checks", lambda **_kwargs: checks)
    assert main(["ready"]) == 0
    assert "Database" in capsys.readouterr().out
    assert main(["recover"]) == 0
