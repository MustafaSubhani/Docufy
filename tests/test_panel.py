import json

import pytest
from fastapi.testclient import TestClient

from spark_agent import panel, secrets, server, store, studio
from spark_agent.llm import LLMError, Router


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARK_STUDIO_DIR", str(tmp_path / "studio"))
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "spark.db")
    monkeypatch.setattr(store, "_conn", None)
    secrets.use(secrets.DictVault())
    panel._ollama_probe = lambda _url: {"ok": True, "detail": "reachable"}
    yield tmp_path
    secrets.use(None)
    panel._ollama_probe = None


def test_a_monthly_budget_warns_and_can_stop_the_next_call(isolated):
    store.record_call(run_id="run", role="writer", provider="openai", model="m", input_tokens=10, output_tokens=10, cost_usd=16)
    studio.save_settings({"monthly_usd": 20})
    assert studio.usage_summary()["budget"]["level"] == "warn"
    store.record_call(run_id="run2", role="writer", provider="openai", model="m", input_tokens=1, output_tokens=1, cost_usd=10)
    router = Router(profile="dev", fake=lambda model, messages, schema: "ok")
    router.config["budgets"] = {"monthly_usd": 20, "hard_stop": True}
    with pytest.raises(LLMError, match="monthly budget"):
        router.complete("writer", "system", "hello")


def test_keys_stay_out_of_the_database_and_the_panel(isolated, monkeypatch):
    from spark_agent import providers

    monkeypatch.setattr(providers, "list_openai_models", lambda refresh=False: ["gpt-5", "gpt-5-mini"])
    saved = secrets.put("openai", "sk-test-secret-value")
    assert saved == {"name": "openai", "set": True}
    visible = json.dumps(secrets.status()) + json.dumps(panel.activity())
    assert "sk-test-secret-value" not in visible
    assert "stored openai" in visible
    client = TestClient(server.app)
    listed = client.get("/api/settings/keys")
    assert listed.status_code == 200
    assert "sk-test-secret-value" not in listed.text
    tested = client.post("/api/settings/keys/test", json={"name": "openai"})
    assert tested.json()["ok"] is True
    assert "sk-test" not in tested.text
    refused = client.put("/api/settings/keys", json={"name": "stripe", "secret": "sk-test-secret-value"})
    assert refused.status_code == 400


def test_the_panel_shows_brand_runs_and_a_usage_file(isolated):
    store.start_run("convert", "notes.md", "dev")
    panel.record_tool("chart.create", detail="Quantity")
    client = TestClient(server.app)
    body = client.get("/api/panel").json()
    assert body["brand"]["phone"] == "+971 56 956 8061"
    assert body["health"]["ollama"]["ok"] is True
    assert any(item["role"] == "planner" and item["context"] == 32768 for item in body["catalog"])
    assert body["runs"]
    assert body["tools"][0]["name"] == "chart.create"
    assert "mode sequential" in " ".join(body["lanes"])
    sheet = client.get("/api/usage.csv")
    assert sheet.headers["content-type"].startswith("text/csv")
    assert "finish_reason" in sheet.text
    studio.ensure()
    now = 1_700_000_000
    store.conn().execute(
        "INSERT INTO documents (id, chat_id, message_id, title, body, pdf_path, docx_path, pages, spark_content, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("a" * 12, "b" * 12, "c" * 12, "Client", "north wall", "", "", 1, 1, now),
    )
    store.conn().execute(
        "INSERT INTO documents (id, chat_id, message_id, title, body, pdf_path, docx_path, pages, spark_content, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("d" * 12, "e" * 12, "f" * 12, "Internal", "south wall", "", "", 1, 0, now),
    )
    store.conn().commit()
    assert [item["title"] for item in studio.list_documents("", "on")] == ["Client"]
    assert [item["title"] for item in studio.list_documents("", "off")] == ["Internal"]
    saved = client.put("/api/settings", json={"monthly_usd": 12, "log_level": "metadata", "local_only": True})
    assert saved.status_code == 200
    assert saved.json()["monthly_usd"] == 12
    assert "sk-" not in (isolated / "studio" / "settings.json").read_text(encoding="utf-8")
