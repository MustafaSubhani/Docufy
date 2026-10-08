"""Seeded visual defects: measurement, two-signal mismatch, scoped repair, and the sandbox."""

import json

from PIL import Image

from spark_agent.llm import Router
from spark_agent.visual import (
    SandboxError,
    clip_rect,
    code_hash,
    is_mismatch,
    repair,
    review,
    run_sandbox,
    structure_score,
    structure_signal,
    vision_signal,
)
from spark_docs.ir import Section, load_markdown

STEPS = """# 01 How We Move Forward

## How We Move Forward

:::cards
### Review

Read the draft.

### Sign

Sign the statement.
:::

# 02 Notes

## Notes

Leave this paragraph alone.
"""


def _block(doc, kind):
    return next(block for section in doc.parts[0].sections for block in section.blocks if block.kind == kind)


def _notes(doc) -> str:
    return next(block.data["text"] for section in doc.parts[0].sections if "Notes" in section.title for block in section.blocks)


def test_seeded_cards_become_steps_without_touching_the_next_section():
    doc = load_markdown(STEPS)
    notes = _notes(doc)
    report = review(doc, apply=True)
    fixed = _block(report.doc, "steps")
    assert report.findings[0].action == "declarative"
    assert [step["title"] for step in fixed.data["steps"]] == ["Review", "Sign"]
    assert "Read the draft." in fixed.data["steps"][0]["body"]
    assert _notes(report.doc) == notes


def test_style_override_is_cleared_and_a_wide_figure_is_scaled():
    doc = load_markdown("# 01 Overview\n\n## Overview\n\nKeep the words.\n")
    para = doc.parts[0].sections[0].blocks[0]
    para.style_overrides = {"width": 900}
    report = review(doc, apply=True)
    assert report.doc.parts[0].sections[0].blocks[0].style_overrides == {}
    assert report.doc.parts[0].sections[0].blocks[0].data["text"] == "Keep the words."

    wide = load_markdown("# 01 Figures\n\n## Figures\n\nKeep the caption.\n")
    wide.parts[0].sections[0].blocks.append(
        __import__("spark_docs.ir", fromlist=["Block"]).Block(id="b9", kind="image", data={"text": "cap", "width": 900, "path": "a.png"})
    )
    # the caption paragraph must stay; only the image width changes
    repaired, action = repair(wide, "b9")
    image = next(block for block in repaired.parts[0].sections[0].blocks if block.id == "b9")
    assert action == "layout"
    assert image.data["width"] == 478
    assert repaired.parts[0].sections[0].blocks[0].data["text"] == "Keep the caption."


def test_reslot_and_escalate_and_custom_snippet_stay_in_scope():
    doc = load_markdown(STEPS.replace("How We Move Forward", "Overview"))
    notes = _notes(doc)
    cards = _block(doc, "cards")

    def fake(model, messages, schema):
        return json.dumps({"kind": "steps"})

    repaired, action = repair(doc, cards.id, Router(profile="dev", fake=fake))
    assert action == "reslot"
    assert _block(repaired, "steps").id == cards.id
    assert _notes(repaired) == notes

    plain = load_markdown("# 01 Overview\n\n## Overview\n\nNothing to change.\n")
    same, action = repair(plain, plain.parts[0].sections[0].blocks[0].id)
    assert action == "escalate"
    assert same.parts[0].sections[0].blocks[0].data["text"] == "Nothing to change."

    code = "import json\nprint(json.dumps({'width': 12}))\n"
    boxed, action = repair(plain, plain.parts[0].sections[0].blocks[0].id, code=code)
    assert action == "sandbox"
    assert boxed.parts[0].sections[0].blocks[0].data["custom_component"]["sha256"] == code_hash(code)
    assert boxed.parts[0].sections[0].blocks[0].data["text"] == "Nothing to change."


def test_mismatch_rule_ssim_and_vision_bbox():
    assert is_mismatch(True, None, None)
    assert is_mismatch(False, True, True)
    assert not is_mismatch(False, True, None)
    assert not is_mismatch(False, True, False)
    white = Image.new("L", (40, 40), 255)
    black = Image.new("L", (40, 40), 0)
    same = structure_score(white, white)
    diff = structure_score(white, black)
    assert same is not None and same > 0.9
    assert structure_signal(same).different is False
    assert structure_signal(diff).different is True
    assert clip_rect({"x": 60, "y": 700, "w": 100, "h": 20}, page_h=842) == (60, 122, 160, 142)

    def fake(model, messages, schema):
        user = messages[-1]["content"]
        if "no box" in user:
            return json.dumps({"issues": [{"message": "loose", "severity": "soft"}]})
        if "bad crop" in user:
            return json.dumps({"issues": [{"message": "padding", "page": 1, "bbox": [60, 400, 200, 40], "severity": "soft"}]})
        return json.dumps({"issues": []})

    router = Router(profile="dev", fake=fake)
    section = Section(id="b1", kicker="bad crop", title="Crop")
    vote = vision_signal(router, section, load_markdown("# 01 A\n\n## A\n\nHi.\n").parts[0].sections[0].blocks[0])
    assert vote.different is True and vote.detail == "padding"
    section.kicker = "no box"
    assert vision_signal(router, section, load_markdown("# 01 A\n\n## A\n\nHi.\n").parts[0].sections[0].blocks[0]).different is None


def test_sandbox_rejects_imports_theme_reads_and_timeouts():
    assert run_sandbox("import json\nprint(json.dumps({'width': 12}))")["width"] == 12
    try:
        run_sandbox("import os\nprint(1)")
        raise AssertionError("os import should fail")
    except SandboxError as exc:
        assert "os" in str(exc)
    try:
        run_sandbox("print(open('themes/spark/tokens.yaml').read())")
        raise AssertionError("theme read should fail")
    except SandboxError as exc:
        assert "theme" in str(exc)
    try:
        run_sandbox("while True:\n    pass\n", timeout=0.4)
        raise AssertionError("loop should time out")
    except SandboxError as exc:
        assert "timed out" in str(exc)
