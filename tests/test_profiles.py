import json
import os

import pytest

from spark_agent import secrets, store
from spark_agent.__main__ import main
from spark_agent.cassettes import Cassette
from spark_agent.convert import convert, dev_units
from spark_agent.llm import LLMError, Router, _apply_provider_keys, profile_report


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPARK_STUDIO_DIR", str(tmp_path / "studio"))
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "spark.db")
    monkeypatch.setattr(store, "_conn", None)


def _plan(blocks: list[str]) -> str:
    return json.dumps(
        {
            "archetype": "technical_document",
            "title": "Notes",
            "doc_type": "DOCUMENTATION",
            "title_block": "b1",
            "sections": [{"kicker": "Notes", "title": "Notes", "blocks": blocks}],
        }
    )


def test_dev_plans_a_long_section_in_smaller_units_and_claude_does_not(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\n" + "\n\n".join(f"Point {i} about the fleet survey." for i in range(1, 8)), encoding="utf-8")
    section = {"kicker": "Notes", "title": "Notes", "blocks": ["b1", "b2", "b3", "b4", "b5", "b6"]}
    assert len(dev_units(section, Router(profile="dev"))) == 2
    assert len(dev_units(section, Router(profile="claude"))) == 1

    def script(counter):
        def fake(model, messages, schema):
            user = messages[-1]["content"]
            if "Source outline" in user:
                return _plan(["b2", "b3", "b4", "b5", "b6", "b7", "b8"])
            counter["n"] += 1
            return json.dumps({"items": [{"component": "text", "blocks": []}]})

        return fake

    dev_calls = {"n": 0}
    convert(source, Router(profile="dev", fake=script(dev_calls)), render=False, out_dir=tmp_path, log=lambda *_: None)
    cloud_calls = {"n": 0}
    convert(source, Router(profile="claude", fake=script(cloud_calls)), render=False, out_dir=tmp_path / "cloud", log=lambda *_: None)
    assert dev_calls["n"] == 2
    assert cloud_calls["n"] == 1


def test_a_cassette_replays_the_reply_and_leaves_the_prompt_out(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "calls.json"
    recorded = Router(profile="dev", fake=lambda model, messages, schema: "kept", cassette=Cassette(path, "record"))
    assert recorded.complete("writer", "Be brief.", "The fleet survey.").text == "kept"
    saved = path.read_text(encoding="utf-8")
    assert "kept" in saved
    assert "fleet" not in saved
    replay = Router(profile="dev", cassette=Cassette(path, "replay"))
    assert replay.complete("writer", "Be brief.", "The fleet survey.").text == "kept"
    with pytest.raises(LLMError, match="cassette"):
        replay.complete("writer", "Be brief.", "A different question about the yard.")


def test_a_stored_key_is_used_for_a_cloud_call_and_local_only_refuses_it(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    secrets.use(secrets.DictVault())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        secrets.put("openai", "sk-profile-test")
        _apply_provider_keys()
        assert os.environ["OPENAI_API_KEY"] == "sk-profile-test"
        visible = json.dumps(store.conn().execute("SELECT detail FROM activity_log").fetchall())
        assert "sk-profile-test" not in visible
        router = Router(profile="claude")
        router.config = {**router.config, "privacy": {"local_only": True}}
        with pytest.raises(LLMError, match="local model"):
            router._call("anthropic/claude-sonnet-4-5", [{"role": "user", "content": "hi"}], None, 16, 0.1)
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        secrets.use(None)


def test_profiles_lists_the_local_model_and_waits_for_cloud_keys(capsys):
    secrets.use(secrets.DictVault())
    try:
        lines = profile_report(keys=[])
        text = "\n".join(lines)
        assert "ollama/qwen2.5-coder:7b" in text
        assert "anthropic/claude-sonnet-4-5" in text
        assert "real use waits until a key is stored" in text
        assert main(["profiles"]) == 0
        printed = capsys.readouterr().out
        assert "work unit 4 blocks" in printed
    finally:
        secrets.use(None)
