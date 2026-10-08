from fastapi.testclient import TestClient

from spark_agent import server, store
from spark_agent.studio import create_chat, list_documents, save_settings, send_message


def test_a_chat_keeps_its_document_and_files_search_finds_the_notes(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    chat = create_chat()
    hello = send_message(chat["id"], "Hello", wait=True)
    assert hello["messages"][-1]["body"].startswith("Paste the notes")
    assert hello["documents"] == []

    detail = send_message(
        chat["id"],
        "# Site survey notes\n\nThe north wall needs a second measurement pass.\n",
        spark_content=False,
        wait=True,
    )
    assert detail["messages"][-1]["body"] == "The document is ready."
    document = detail["documents"][-1]
    assert document["chat_id"] == chat["id"]
    assert document["pages"] >= 1
    found = list_documents("north wall")
    assert found and found[0]["id"] == document["id"]
    assert found[0]["chat_id"] == chat["id"]

    client = TestClient(server.app)
    page = client.get(f"/api/documents/{document['id']}/pages/1.png")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("image/png")
    missing = client.get("/api/chats/not-a-real-id")
    assert missing.status_code == 404
    saved = save_settings({"profile": "claude", "use_model": False, "per_run_usd": 1.5})
    assert saved["profile"] == "claude"
    assert client.get("/api/settings").json()["per_run_usd"] == 1.5
    usage = client.get("/api/usage").json()
    assert "today" in usage["windows"]
    assert usage["documents"] == 1


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPARK_STUDIO_DIR", str(tmp_path / "studio"))
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "spark.db")
    monkeypatch.setattr(store, "_conn", None)
