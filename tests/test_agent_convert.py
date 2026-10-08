"""Smart Convert with a scripted fake model: planning is exercised without Ollama or API keys."""

import json

from spark_agent.blocks import segment
from spark_agent.convert import convert
from spark_agent.llm import Router

SOURCE = """# ACME Fleet Tracking

## The problem

ACME loses 12% of fuel to unverified refills across 340 vehicles.

## What we will build

Driver identity is checked at ignition. See [the spec](https://example.com/spec).

- GPS unit per vehicle
- Fuel card reconciliation

## How we move forward

Review the document.

Run a discovery session.

Sign the statement of work.
"""


def fake_model(model, messages, schema):
    user = messages[-1]["content"]
    if "Source outline" in user:
        return json.dumps(
            {
                "archetype": "technical_document",
                "title": "ACME",
                "subtitle": "Fleet Tracking",
                "doc_type": "DOCUMENTATION",
                "prepared_for": "ACME",
                "title_block": "b1",
                "sections": [
                    {"kicker": "Overview", "title": "The problem", "blocks": ["b2", "b3"]},
                    {"kicker": "Scope of Work", "title": "What we will build", "blocks": ["b4", "b5", "b6"]},
                    {"kicker": "Next Steps", "title": "How we move forward", "blocks": ["b7", "b8", "b9", "b10"]},
                ],
            }
        )
    if "How we move forward" in user:
        return json.dumps(
            {"items": [{"component": "steps", "parts": [{"title": "Review", "blocks": ["b8"]}, {"title": "Discovery", "blocks": ["b9"]}, {"title": "Sign-off", "blocks": ["b10"]}]}]}
        )
    return json.dumps({"items": [{"component": "text", "blocks": []}]})


def test_segment_ids_are_stable():
    blocks = segment(SOURCE)
    assert [b.kind for b in blocks] == ["heading", "heading", "para", "heading", "para", "list", "heading", "para", "para", "para"]
    assert blocks[0].id == "b1" and blocks[-1].id == "b10"


def test_convert_preserves_content_and_uses_components(tmp_path):
    src = tmp_path / "acme.md"
    src.write_text(SOURCE, encoding="utf-8")
    router = Router(profile="dev", fake=fake_model)
    res = convert(src, router, render=True, out_dir=tmp_path, log=lambda *a: None)
    md = res.markdown
    assert "title: ACME" in md and "doc_type: DOCUMENTATION" in md
    assert "# 01 Overview" in md and "## The problem" in md
    assert "12%" in md and "340" in md and "https://example.com/spec" in md
    assert ":::steps" in md and "### Discovery" in md
    assert md.count("## What we will build") == 1
    assert not res.issues, res.issues
    assert res.pdf and res.pdf.is_file()


def test_h2_boundaries_win_when_the_model_merges_sections(tmp_path):
    src = tmp_path / "guide.md"
    src.write_text(
        "# Trial Guide\n\nHand this to a client.\n\n"
        "## 14. Drawings Chat\n\nAsk a question about the plan.\n\n"
        "## 19. Suggested Walkthrough\n\nOpen the sample plan.\n",
        encoding="utf-8",
    )

    def merging(model, messages, schema):
        user = messages[-1]["content"]
        if "Source outline" in user:
            return json.dumps(
                {
                    "archetype": "client_guide",
                    "title": "Trial",
                    "doc_type": "GUIDE",
                    "title_block": "b4",
                    "sections": [{"kicker": "All", "title": "All", "blocks": ["b2"]}],
                }
            )
        return json.dumps({"items": [{"component": "text", "blocks": []}]})

    res = convert(src, Router(profile="dev", fake=merging), render=False, out_dir=tmp_path, log=lambda *a: None)
    assert "# 01 Overview" in res.markdown
    assert "# 02 Drawings Chat" in res.markdown and "## Drawings Chat" in res.markdown
    assert "# 03 Suggested Walkthrough" in res.markdown
    assert "Ask a question about the plan." in res.markdown
    assert not any("not placed" in i or "numbers missing" in i for i in res.issues), res.issues


def test_convert_falls_back_when_model_fails(tmp_path):
    src = tmp_path / "acme.md"
    src.write_text(SOURCE, encoding="utf-8")

    def broken(model, messages, schema):
        return "not json at all"

    res = convert(src, Router(profile="dev", fake=broken), render=False, out_dir=tmp_path, log=lambda *a: None)
    assert "12%" in res.markdown and "https://example.com/spec" in res.markdown
    assert any("planner failed" in i for i in res.issues)
