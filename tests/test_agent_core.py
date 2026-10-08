"""Agent core: tools, governor, budgets, fallback, and the checkpointed convert graph."""

import threading
import time

import pytest

from spark_agent import store
from spark_agent.governor import Lane, workers_for
from spark_agent.graph import resume_convert, start_convert
from spark_agent.llm import LLMError, Router
from spark_agent.tools import call_tool
from tests.test_agent_convert import SOURCE, fake_model


def test_calc_rounds_half_up_and_rejects_calls():
    assert call_tool("calc.eval", expr="1.005", places=2)["result"] == "1.01"
    assert call_tool("calc.total", values=["10.50", "2.25"], expected="12.75")["ok"]
    days = call_tool("calc.business_days", start="2026-09-28", end="2026-10-02")
    assert days["days"] == 5 and days["weekend"] == "Saturday-Sunday"
    with pytest.raises(ValueError):
        call_tool("calc.eval", expr="__import__('os').system('echo')")


def test_brand_lookup_and_now():
    assert call_tool("brand.lookup", key="phone")["phone"] == "+971 56 956 8061"
    assert "2026" in call_tool("now")["text"]
    assert call_tool("brand.lookup", key="missing")["error"]


def test_audit_ignores_single_digits():
    found = call_tool("calc.audit", source="340 vehicles", draft="340 vehicles, 99 sites and step 2")
    assert found["invented"] == ["99"]


def test_diagram_spec_counts_nodes():
    spec = "nodes:\n  - {id: a, title: A}\n  - {id: b, title: B}\nedges:\n  - {src: a, dst: b}\n"
    got = call_tool("diagram.spec", source=spec)
    assert got["ok"] and got["nodes"] == 2


def test_lane_capacity_is_exclusive():
    lane = Lane("llm", 1)
    order = []

    def work():
        with lane.acquire():
            order.append("in")
            time.sleep(0.05)
            order.append("out")

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert order == ["in", "out", "in", "out"]


def test_workers_follow_the_profile():
    assert workers_for(Router(profile="dev")) == 1
    assert workers_for(Router(profile="claude")) == 4


def test_budget_stops_before_the_model():
    called = {"n": 0}

    def fake(model, messages, schema):
        called["n"] += 1
        return "ok"

    router = Router(profile="dev", fake=fake)
    router.config["budgets"] = {"per_run_usd": 1.0}
    router.run_id = store.start_run("convert", "budget", "dev")
    store.record_call(run_id=router.run_id, role="planner", provider="ollama", model="m", cost_usd=5)
    with pytest.raises(LLMError, match="budget"):
        router.complete("planner", "system", "user")
    assert called["n"] == 0


def test_cloud_profile_falls_back_once():
    def fake(model, messages, schema):
        if "anthropic" in model:
            raise RuntimeError("provider down")
        return "ok"

    router = Router(profile="claude", fake=fake)
    res = router.complete("planner", "system", "user")
    assert res.model.startswith("openai/")
    assert res.text == "ok"


def test_convert_graph_waits_then_resumes(tmp_path):
    src = tmp_path / "acme.md"
    src.write_text(SOURCE, encoding="utf-8")
    router = Router(profile="dev", fake=fake_model)
    paused = start_convert(src, router, approve=False, render=True, out_dir=tmp_path, log=lambda *_: None)
    assert paused.status == "interrupted"
    assert paused.pdf is None
    assert "Our Assessment" in paused.warnings
    done = resume_convert(paused.run_id, Router(profile="dev", fake=fake_model), log=lambda *_: None)
    assert done.pdf and done.pdf.is_file()
    assert not done.issues, done.issues
    assert "Our Assessment" in done.warnings
