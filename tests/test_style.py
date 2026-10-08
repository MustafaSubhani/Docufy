from PIL import Image

from spark_agent.edit import EditError, EditRequest, EditScope, apply_edit
from spark_agent.llm import Router
from spark_agent.style import StyleIntent, judge_style, locate_preview, restyle, sample_colors, snap_color
from spark_docs.ir import load_markdown, to_spark_markdown

DOC = """---
title: ACME
---

# 01 Overview

## Summary

A sentence that must stay.

:::cards
### One
Keep the card wording.
:::

:::callout
### Note
Keep the callout wording.
:::

:::callout
### Other
Also keep this.
:::
"""


def _doc():
    return load_markdown(DOC)


def _block(doc, kind, nth=0):
    found = [block for part in doc.parts for section in part.sections for block in section.blocks if block.kind == kind]
    return found[nth]


def test_steps_variant_keeps_the_words_and_leaves_the_paragraph():
    doc = _doc()
    card = _block(doc, "cards")
    paragraph = _block(doc, "para")
    result = apply_edit(
        doc,
        EditRequest(scope=EditScope(kind="block", ids=[card.id]), instruction="use steps", intent="restyle"),
    )
    styled = _block(result.doc, "cards")
    assert styled.variant == "steps"
    assert styled.data == card.data
    assert _block(result.doc, "para").data["text"] == paragraph.data["text"]
    assert result.diff.startswith("style variant")


def test_all_of_type_styles_every_callout_and_skips_a_locked_one():
    doc = _doc()
    first, second = _block(doc, "callout", 0), _block(doc, "callout", 1)
    second.locked = True
    result = apply_edit(
        doc,
        EditRequest(
            scope=EditScope(kind="block", ids=[first.id]),
            instruction="make this tight",
            intent="restyle",
            apply_to="all_of_type",
        ),
    )
    assert _block(result.doc, "callout", 0).style_overrides["spacing"] == "tight"
    assert _block(result.doc, "callout", 1).style_overrides == {}
    assert second.id in result.skipped
    assert _block(result.doc, "para").style_overrides == {}


def test_sampled_red_snaps_to_the_palette_unless_brand_lock_is_off(tmp_path):
    image = tmp_path / "red.png"
    Image.new("RGB", (16, 16), (255, 0, 0)).save(image)
    colors = sample_colors(image, k=1)
    assert colors[0].upper().startswith("#FF")
    snapped, warning = snap_color(colors[0], brand_lock=True)
    assert snapped != "#FF0000"
    assert "palette" in warning
    exact, quiet = snap_color("#FF0000", brand_lock=False)
    assert exact == "#FF0000" and quiet == ""


def test_preview_crop_is_found_and_a_red_mark_is_reported(tmp_path):
    page = Image.new("RGB", (36, 28), (230, 230, 230))
    for x in range(8, 16):
        for y in range(5, 12):
            page.putpixel((x, y), (20, 20, 20) if (x + y) % 2 == 0 else (80, 40, 40))
    page_path = tmp_path / "page.png"
    page.save(page_path)
    crop = page.crop((8, 5, 16, 12))
    crop_path = tmp_path / "crop.png"
    crop.save(crop_path)
    found = locate_preview(crop_path, page_path)
    assert found["score"] > 0.9
    assert abs(found["x"] - 8) <= 1
    assert abs(found["y"] - 5) <= 1

    marked = page.copy()
    for x in range(10, 13):
        for y in range(6, 9):
            marked.putpixel((x, y), (255, 0, 0))
    marked_path = tmp_path / "marked.png"
    marked.save(marked_path)
    marked_found = locate_preview(marked_path, page_path)
    assert "mark" in marked_found
    assert marked_found["mark"]["w"] >= 2


def test_vision_intent_and_judge(tmp_path):
    doc = _doc()
    card = _block(doc, "cards")

    def fake(model, messages, schema):
        user = "\n".join(message["content"] for message in messages)
        if "Compare the reference" in user:
            return '{"match": true, "note": "the cards match"}'
        return '{"layout": "cards", "spacing": "airy"}'

    image = tmp_path / "swatch.png"
    Image.new("RGB", (12, 12), (107, 78, 255)).save(image)
    result = apply_edit(
        doc,
        EditRequest(
            scope=EditScope(kind="block", ids=[card.id]),
            instruction="match the attachment",
            intent="restyle",
            attachments=[{"path": str(image)}],
        ),
        Router(fake=fake),
    )
    assert _block(result.doc, "cards").style_overrides["spacing"] == "airy"
    assert any("cards match" in note for note in result.notes)


def test_off_brand_font_stays_in_arial():
    doc = _doc()
    card = _block(doc, "cards")
    result = apply_edit(
        doc,
        EditRequest(
            scope=EditScope(kind="block", ids=[card.id]),
            instruction="set the font to Comic Sans",
            intent="restyle",
        ),
    )
    assert _block(result.doc, "cards").style_overrides["font"] == "Arial"
    assert any("Arial" in item.message for item in result.suggestions)


def test_save_as_variant_writes_a_file(tmp_path):
    doc = _doc()
    card = _block(doc, "cards")
    folder = tmp_path / "variants"
    result = restyle(
        doc,
        EditRequest(
            scope=EditScope(kind="block", ids=[card.id]),
            instruction="use steps",
            intent="restyle",
            apply_to="save_as_variant",
        ),
        variant_dir=folder,
    )
    saved = folder / "steps.yaml"
    assert saved.is_file()
    assert "cards" in saved.read_text(encoding="utf-8")
    assert _block(result.doc, "cards").variant == "steps"


def test_style_comment_round_trips():
    doc = _doc()
    card = _block(doc, "cards")
    card.variant = "steps"
    card.style_overrides = {"spacing": "tight"}
    again = load_markdown(to_spark_markdown(doc))
    restored = _block(again, "cards")
    assert restored.variant == "steps"
    assert restored.style_overrides["spacing"] == "tight"
    assert "Keep the card wording." in to_spark_markdown(again)


def test_custom_component_runs_in_the_sandbox():
    doc = _doc()
    card = _block(doc, "cards")
    result = apply_edit(
        doc,
        EditRequest(
            scope=EditScope(kind="block", ids=[card.id]),
            instruction='custom component\n```python\nimport json\nprint(json.dumps({"width": 12}))\n```',
            intent="restyle",
        ),
    )
    overrides = _block(result.doc, "cards").style_overrides
    assert overrides["custom_result"]["width"] == 12
    assert result.diff.startswith("style custom")


def test_a_plain_rewrite_still_refuses_save_as_variant():
    doc = _doc()
    try:
        apply_edit(
            doc,
            EditRequest(
                scope=EditScope(kind="block", ids=[_block(doc, "para").id]),
                instruction="shorten",
                apply_to="save_as_variant",
            ),
            Router(fake=lambda model, messages, schema: "short"),
        )
    except EditError as exc:
        assert "not available" in str(exc)
    else:
        raise AssertionError("save as variant is only for a style edit")


def test_judge_without_a_model_compares_colors(tmp_path):
    image = tmp_path / "purple.png"
    Image.new("RGB", (10, 10), (107, 78, 255)).save(image)
    matched, note = judge_style(StyleIntent(colors=["#6B4EFF"]), image, router=None)
    assert matched is True
    assert "agree" in note
