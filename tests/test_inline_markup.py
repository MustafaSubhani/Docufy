from reportlab.lib.styles import getSampleStyleSheet

from spark_docs import pdf
from spark_docs.parser import balance_tags, inline_xml

STYLE = getSampleStyleSheet()["Normal"]


def test_bold_italic_and_overlapping_markup_become_nested_tags():
    text = "badges them plainly: ***Traced from scan pixels, verify edges before Create.*** Nudge the grips"
    assert "<b><i>Traced from scan pixels, verify edges before Create.</i></b>" in inline_xml(text)
    assert inline_xml("___both___") == "<b><i>both</i></b>"
    assert inline_xml("**bold *both** italic*") == "<b>bold <i>both</i></b><i> italic</i>"
    assert balance_tags("<b><i>x</b></i>") == "<b><i>x</i></b>"
    assert balance_tags("stray</i> <b>open") == "stray <b>open</b>"
    for sample in ("***a*** **b** *c*", "**bold *both** italic*", "*open only"):
        pdf.Paragraph(inline_xml(sample), STYLE)


GUIDE = """---
title: Takeoff guide
doc_type: DOCUMENTATION
---

# Takeoff guide

1. [Scale, set it first](#1-scale--set-it-first)
2. [Check a dimension](#check-a-dimension-k)
3. [Keyboard reference](#15-keyboard-reference)

## 1. Scale - set it first

Press \u21e7 and \u23ce to confirm. ***Traced from scan pixels, verify edges before Create.***

### Check a dimension (`K`)

Measure twice.

## 2. Exports

Send the report.
"""


def test_contents_links_resolve_and_a_missing_target_stays_as_text(tmp_path):
    import pymupdf

    from spark_docs.parser import parse_path

    source = tmp_path / "guide.md"
    source.write_text(GUIDE, encoding="utf-8")
    meta, blocks = parse_path(source)
    out = pdf.build_pdf(meta, blocks, tmp_path / "guide.pdf")
    with pymupdf.open(out) as doc:
        links = [link for page in doc for link in page.get_links() if link.get("kind") != pymupdf.LINK_URI]
        text = "".join(page.get_text() for page in doc)
    assert len(links) == 2
    assert "Keyboard reference" in text and "Shift" in text and "Enter" in text
    assert not any("missing glyph" in item for item in pdf.WARNINGS)


def test_markup_the_renderer_cannot_read_is_set_as_plain_text():
    pdf.WARNINGS.clear()
    para = pdf.Paragraph("<b><i>broken</b></i> text", STYLE)
    assert "broken text" in para.getPlainText()
    assert pdf.WARNINGS and "plain text" in pdf.WARNINGS[0]
    pdf.WARNINGS.clear()
