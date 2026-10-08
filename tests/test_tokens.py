import json

from fastapi.testclient import TestClient

from spark_agent import server, store
from spark_agent.__main__ import main
from spark_agent.llm import Router
from spark_agent.tokens import (
    ModelCard,
    TokenError,
    choose_num_ctx,
    compress,
    cutoff_kind,
    estimate_tokens,
    fetch_show,
    fit_user,
    kv_bytes,
    lookup,
    ollama_options,
    recover,
    repeated_ngram,
    salvage_json,
    stitch,
    truncation_issues,
)
from spark_agent.verify import verify_markdown


def test_the_character_estimate_and_a_short_prompt_stay_put():
    assert estimate_tokens("abcd") == int(4 / 3.5) + 8
    assert fit_user("system", "user", 100, 8192) == "user"


def test_overrides_win_and_an_ollama_card_can_come_from_show():
    card = lookup(
        "openai/gpt-5",
        litellm_cost={"gpt-5": {"max_input_tokens": 111, "max_output_tokens": 50, "input_cost_per_token": 0.000002}},
        table={"openai/gpt-5": {"context": 222}},
    )
    assert card.context == 222
    assert card.max_output == 50
    assert card.input_usd_per_mtok == 2

    seen = {}

    def post(url, body):
        seen["url"] = url
        seen["body"] = body
        return {
            "capabilities": ["completion", "vision"],
            "model_info": {
                "general.architecture": "qwen2",
                "qwen2.context_length": 4096,
                "qwen2.block_count": 28,
                "qwen2.attention.head_count": 28,
                "qwen2.attention.head_count_kv": 4,
                "qwen2.embedding_length": 3584,
            },
        }

    payload = fetch_show("qwen2.5-coder:7b", url="http://127.0.0.1:9", post=post)
    assert seen["url"] == "http://127.0.0.1:9/api/show"
    assert seen["body"]["name"] == "qwen2.5-coder:7b"
    shown = lookup("ollama/qwen2.5-coder:7b", table={"ollama/qwen2.5-coder:7b": {"context": 100}}, show=payload)
    assert shown.context == 4096
    assert shown.layers == 28
    assert shown.kv_heads == 4
    assert shown.head_dim == 128
    assert shown.vision is True


def test_kv_math_keeps_sixteen_k_until_flash_and_q8_and_free_memory_allow_more():
    assert kv_bytes(28, 4, 128, 1) == 57344
    card = ModelCard("qwen", layers=28, kv_heads=4, head_dim=128, weight_gb=4.7)
    free = 8 * 1024**3
    assert choose_num_ctx(card, free_vram_bytes=free, configured=32768) == 16384
    assert choose_num_ctx(card, free_vram_bytes=free, configured=32768, flash=True, kv_q8=True) == 32768
    assert choose_num_ctx(card, free_vram_bytes=int(5.1 * 1024**3), configured=16384) == 4096
    one_slot = card.weight_bytes + kv_bytes(28, 4, 128, 16384)
    assert choose_num_ctx(card, free_vram_bytes=one_slot, configured=16384, parallel=2) < 16384
    options = ollama_options(16384, 512, 0.1)
    assert options["num_ctx"] == 16384
    assert options["num_predict"] == 512


def test_compression_drops_history_then_the_lowest_evidence_and_keeps_the_task():
    user = "\n".join(
        [
            "[[task]]",
            "TASK",
            "[[target]]",
            "BODY",
            "[[brief]]",
            "BRIEF",
            "[[evidence]]",
            "HIGH",
            "[[evidence]]",
            "L" * 80,
            "[[history]]",
            "H" * 80,
        ]
    )
    packed = compress("S" * 20, user, 10, 150, counter=len)
    assert "TASK" in packed
    assert "HIGH" in packed
    assert "L" * 80 not in packed
    assert "H" * 80 not in packed
    try:
        compress("S" * 500, "[[task]]\nKEEP", 10, 100, counter=len)
    except TokenError as exc:
        assert "do not fit" in str(exc)
    else:
        raise AssertionError("expected the instructions to be refused")


def test_a_cut_off_is_stitched_and_a_partial_list_keeps_finished_items():
    assert stitch("The fleet is ready. The next", "The next step is review.") == "The fleet is ready. The next step is review."
    value, complete = salvage_json('{"items":[{"id":1},{"id":2')
    assert complete is False
    assert json.dumps(value).count("1") >= 1
    assert '"id": 2' not in json.dumps(value) and '"id":2' not in json.dumps(value)

    continued = []

    def continue_call(step, payload):
        continued.append(step)
        if step == "continue":
            return "The next step is review."
        return ""

    result = recover("The fleet is ready. The next", reason="length", call=continue_call)
    assert result.status == "continued"
    assert result.steps == ["continue"]
    assert result.text.endswith("review.")

    steps = []

    def missing_call(step, payload):
        steps.append(step)
        if step == "missing":
            return '{"items":[{"id":1},{"id":2}]}'
        return ""

    salvaged = recover('{"items":[{"id":1},{"id":2', reason="length", call=missing_call, schema={"type": "object"})
    assert salvaged.status == "salvaged"
    assert salvaged.data["items"][1]["id"] == 2
    assert steps == ["missing"]

    failed_steps = []
    failed = recover(
        "Hello there friend",
        reason="length",
        call=lambda step, payload: failed_steps.append(step) or "",
        fallback="openai/gpt-5",
    )
    assert failed.status == "failed"
    assert failed_steps[0] == "continue"
    assert "raise" in failed_steps and "split" in failed_steps and failed_steps[-1] == "fallback"
    assert cutoff_kind("MAX_TOKENS") == "length"
    assert cutoff_kind("", stalled=True) == "stalled"
    phrase = "alpha beta gamma delta epsilon zeta eta theta "
    assert repeated_ngram(phrase * 4)
    assert repeated_ngram("The fleet survey covers twelve sites.") is False


def test_the_same_call_is_not_sent_twice_and_a_dangling_ending_is_flagged(capsys):
    calls = {"n": 0}

    def fake(model, messages, schema):
        calls["n"] += 1
        return "ok"

    router = Router(profile="dev", fake=fake)
    assert router.complete("planner", "system", "user").text == "ok"
    assert router.complete("planner", "system", "user").text == "ok"
    assert calls["n"] == 1
    assert router.complete("planner", "system", "other").text == "ok"
    assert calls["n"] == 2
    assert truncation_issues("See the following:") == ["text ends on 'the following:'"]
    assert truncation_issues("The construc-") == ["text ends mid-word"]
    assert truncation_issues("The fleet is ready.") == []
    assert any("following" in item for item in verify_markdown("ready", "ready\n\nSee the following:"))
    assert main(["models"]) == 0
    printed = capsys.readouterr().out
    assert "ollama/qwen2.5-coder:7b" in printed
    assert "context 32768" in printed
    assert "num_ctx" in printed


def test_a_stream_replays_events_after_the_last_id(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "spark.db")
    monkeypatch.setattr(store, "_conn", None)
    store.event("run1", "token", {"n": 1})
    store.event("run1", "token", {"n": 2})
    store.save_checkpoint("run1", "llm", {"partial": "The fleet is ready."})
    assert store.load_checkpoint("run1")["partial"] == "The fleet is ready."
    rows = store.events_since("run1", 0)
    client = TestClient(server.app)
    replay = client.get("/api/runs/run1/stream", headers={"Last-Event-ID": str(rows[0]["id"])})
    assert replay.status_code == 200
    assert f"id: {rows[1]['id']}" in replay.text
    assert f"id: {rows[0]['id']}" not in replay.text
    listed = client.get(f"/api/runs/run1/events?after={rows[0]['id']}").json()
    assert listed["events"][0]["data"]["n"] == 2
