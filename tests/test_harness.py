"""The agent loop, driven by a scripted model so no provider is called."""

from __future__ import annotations

import json

import pytest

from spark_agent import harness, studio, workspace
from spark_agent.providers import Citation, ToolCall, Turn, Usage

DRAFT = """# Fleet Telematics Rollout

## Executive Summary

ACME Fleet wants live tracking across 120 vans. Pricing is to be confirmed.

## Approach

```mermaid
flowchart LR
  A[Vans]:::input --> B[Telematics gateway]:::platform --> C[Ops dashboard]:::output
```

| Phase | Duration |
| --- | --- |
| Pilot | 4 weeks |
| Rollout | 8 weeks |
"""


class ScriptedAdapter:
    """Plays back turns. Each entry is a Turn or a function of the transcript that returns one."""

    hosted_search = True
    web = True
    model = "scripted"

    def __init__(self, turns):
        self.turns = list(turns)
        self.results: list[list[tuple[ToolCall, str]]] = []
        self.started: dict = {}
        self.nudges: list[str] = []

    def start(self, system, history, user):
        self.started = {"system": system, "history": history, "user": user}

    def step(self, functions, events, check):
        check()
        turn = self.turns.pop(0)
        if callable(turn):
            turn = turn(self)
        for piece in (turn.text or "").split(" "):
            events.text(piece + " ")
        return turn

    def add_results(self, results):
        self.results.append(results)

    def nudge(self, text):
        self.nudges.append(text)


def _call(name, **args):
    return ToolCall(id=f"call_{name}_{len(json.dumps(args))}", name=name, arguments=args)


@pytest.fixture
def agent_on(monkeypatch):
    monkeypatch.setattr(harness, "agent_ready", lambda settings: True)
    holder = {}

    def make(settings, cfg):
        return holder["adapter"]

    monkeypatch.setattr(harness, "make_adapter", make)
    return holder


def test_agent_researches_writes_a_draft_and_answers(agent_on):
    agent_on["adapter"] = ScriptedAdapter(
        [
            Turn(text="I will draft it.", calls=[_call("create_draft", title="Fleet Telematics Rollout", markdown=DRAFT)],
                 citations=[Citation("https://example.com/fleet", "Fleet study")], usage=Usage(100, 50)),
            Turn(text="The draft is in the canvas. Shall I set it in the template?", usage=Usage(120, 30)),
        ]
    )
    chat = studio.create_chat()
    detail = studio.send_message(chat["id"], "Write a rollout plan for ACME Fleet telematics", model="off", wait=True)
    reply = detail["messages"][-1]
    assert reply["status"] == "ready"
    assert "Shall I set it in the template?" in reply["body"]
    assert reply["sources"] == [{"url": "https://example.com/fleet", "title": "Fleet study"}]
    assert len(detail["drafts"]) == 1 and detail["drafts"][0]["title"] == "Fleet Telematics Rollout"
    assert reply["drafts"] == [detail["drafts"][0]["root_id"]]
    tools = [step for step in reply["activity"] if step["kind"] == "tool"]
    assert tools and tools[0]["label"] == "Writing the draft" and tools[0]["status"] == "done"
    result = agent_on["adapter"].results[0][0][1]
    assert "Created draft" in result and "Executive Summary" in result
    assert "<workspace>" in agent_on["adapter"].started["user"][-1]["text"]


def test_edit_failures_come_back_to_the_model_and_a_good_edit_versions_the_draft(agent_on):
    chat = studio.create_chat()
    draft = workspace.create_draft(chat["id"], "Plan", DRAFT)
    agent_on["adapter"] = ScriptedAdapter(
        [
            Turn(calls=[_call("edit_draft", edits=[{"find": "not in the draft", "replace": "x"}])]),
            Turn(calls=[_call("edit_draft", edits=[{"find": "120 vans", "replace": "140 vans"}], note="Fleet size")]),
            Turn(text="Updated the fleet size."),
        ]
    )
    studio.send_message(chat["id"], "It is 140 vans, not 120", model="off", wait=True)
    first, second = agent_on["adapter"].results
    assert first[0][1].startswith("Error: no edit applied")
    assert "version 2" in second[0][1]
    latest = workspace.latest_in(draft["root_id"])
    assert "140 vans" in latest["body"] and latest["version"] == 2


def test_publish_sets_the_draft_in_the_template_and_a_second_publish_is_version_two(agent_on):
    chat = studio.create_chat()
    draft = workspace.create_draft(chat["id"], "Fleet Telematics Rollout", DRAFT)
    agent_on["adapter"] = ScriptedAdapter(
        [Turn(calls=[_call("publish_draft", client_pages=False)]), Turn(text="Set in the template.")]
    )
    detail = studio.send_message(chat["id"], "Convert it to the template", model="off", wait=True)
    reply = detail["messages"][-1]
    assert reply["status"] == "ready", reply["body"]
    doc = studio.get_document(reply["document_id"])
    assert doc["version"] == 1 and doc["pages"] >= 1
    assert workspace.latest_in(draft["root_id"])["document_root"] == doc["root_id"]
    tool = next(step for step in reply["activity"] if step["kind"] == "tool")
    children = [step for step in reply["activity"] if step["parent"] == tool["key"]]
    assert children, "the template pipeline's steps sit under the publish call"

    workspace.save_version(draft["id"], DRAFT.replace("4 weeks", "6 weeks"))
    agent_on["adapter"] = ScriptedAdapter([Turn(calls=[_call("publish_draft")]), Turn(text="Version 2 is ready.")])
    again = studio.send_message(chat["id"], "Publish the update", model="off", wait=True)
    second = studio.get_document(again["messages"][-1]["document_id"])
    assert second["root_id"] == doc["root_id"] and second["version"] == 2


def test_edit_document_patches_the_template_source_as_a_new_version(agent_on):
    chat = studio.create_chat()
    workspace.create_draft(chat["id"], "Fleet Telematics Rollout", DRAFT)
    agent_on["adapter"] = ScriptedAdapter([Turn(calls=[_call("publish_draft", client_pages=False)]), Turn(text="Done.")])
    first = studio.send_message(chat["id"], "Publish", model="off", wait=True)
    doc_id = first["messages"][-1]["document_id"]
    source = studio.document_markdown(studio.get_document(doc_id))
    assert "120 vans" in source

    def read_then_edit(adapter):
        listing = adapter.results[-1][0][1]
        assert "Sections:" in listing
        return Turn(calls=[_call("edit_document", edits=[{"find": "120 vans", "replace": "150 vans"}], prepared_for="ACME Fleet")])

    agent_on["adapter"] = ScriptedAdapter([Turn(calls=[_call("read_document")]), read_then_edit, Turn(text="Changed.")])
    detail = studio.send_message(chat["id"], "Make it 150 vans and prepared for ACME Fleet", model="off", document_id=doc_id, wait=True)
    new = studio.get_document(detail["messages"][-1]["document_id"])
    assert new["version"] == 2
    text = studio.document_markdown(new)
    assert "150 vans" in text and "prepared_for: ACME Fleet" in text


def test_history_is_fitted_and_older_turns_fold_into_the_summary(monkeypatch):
    from spark_agent import context, store

    chat = studio.create_chat()
    now = 1000.0
    with store._lock:
        for index in range(30):
            role = "user" if index % 2 == 0 else "assistant"
            store.conn().execute(
                "INSERT INTO messages (id, chat_id, role, body, status, created_at) VALUES (?,?,?,?,?,?)",
                (f"{index:012x}", chat["id"], role, f"turn {index} " + "word " * 900, "ready", now + index),
            )
        store.conn().execute(
            "INSERT INTO messages (id, chat_id, role, body, status, created_at) VALUES (?,?,?,?,?,?)",
            ("ffffffffffff", chat["id"], "user", "latest question", "ready", now + 100),
        )
        store.conn().commit()
    monkeypatch.setattr(context, "HISTORY_TOKENS", 6000)
    asked = []

    def summarize(system, user):
        asked.append(user)
        return "- earlier: the client is ACME"

    built = context.build(chat["id"], "", "ffffffffffff", "latest question", target_doc="", studio_dir=studio.studio_dir(), summarize=summarize)
    assert asked and "turn 0" in asked[0]
    assert built.history[0]["content"].startswith("<earlier_conversation_summary>")
    assert "turn 29" in built.history[-1]["content"]
    assert workspace.summary(chat["id"])[0] == "- earlier: the client is ACME"
    roles = [item["role"] for item in built.history]
    assert all(a != b for a, b in zip(roles, roles[1:]))


def test_apply_edits_tolerates_whitespace_and_refuses_ambiguity():
    body = "Alpha  beta\ngamma\nrepeat repeat"
    out, problems = workspace.apply_edits(body, [{"find": "Alpha beta gamma", "replace": "AB"}])
    assert out.startswith("AB") and not problems
    _out, problems = workspace.apply_edits(body, [{"find": "repeat", "replace": "x"}])
    assert "appears 2 times" in problems[0]


def test_stopping_keeps_what_was_streamed(agent_on):
    chat = studio.create_chat()

    def slow(adapter):
        raise studio.Stopped()

    agent_on["adapter"] = ScriptedAdapter([slow])
    detail = studio.send_message(chat["id"], "Hello", model="off", wait=True)
    assert detail["messages"][-1]["status"] in {"writing", "stopped", "ready", "failed"}


def test_the_composer_can_set_effort_and_model_for_one_message():
    base = studio.load_settings()
    turn = studio.with_turn(base, "high", "openai/gpt-5-mini")
    assert turn["reasoning_effort"] == "high" and turn["openai_model"] == "gpt-5-mini"
    assert studio.load_settings()["reasoning_effort"] == base["reasoning_effort"]
    with pytest.raises(studio.StudioError):
        studio.with_turn(base, "extreme", None)


def test_read_url_refuses_this_computer_and_private_networks():
    from spark_agent.agent_tools import _public_host

    assert not _public_host("http://127.0.0.1:8787/api/chats")
    assert not _public_host("http://localhost/x")
    assert not _public_host("http://192.168.1.10/admin")
    assert not _public_host("http://169.254.169.254/latest")


def test_writes_from_another_site_are_refused():
    from fastapi.testclient import TestClient

    from spark_agent import server

    client = TestClient(server.app)
    chat = client.post("/api/chats").json()
    refused = client.post(f"/api/chats/{chat['id']}/messages", data={"text": "hi"}, headers={"origin": "https://evil.example"})
    assert refused.status_code == 403
    same = client.post("/api/chats", headers={"origin": "http://testserver"})
    assert same.status_code == 200


def test_reaching_the_budget_answers_pending_calls_then_closes_without_tools(agent_on, monkeypatch):
    from spark_agent import store

    monkeypatch.setattr(store, "usage", lambda run_id: {"cost_usd": 99.0})
    seen = {}

    def closing(adapter):
        seen["results"] = adapter.results[-1]
        seen["nudge"] = adapter.nudges[-1]
        return Turn(text="Here is what I have.")

    agent_on["adapter"] = ScriptedAdapter([Turn(calls=[_call("calculate", expression="1+1")]), closing])
    detail = studio.send_message(studio.create_chat()["id"], "Sum it", model="off", wait=True)
    assert detail["messages"][-1]["body"].strip() == "Here is what I have."
    assert seen["results"][0][1].startswith("Not run") and "budget" in seen["nudge"]
