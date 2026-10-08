"""Layout map, glyph fallbacks, and link checks."""

from spark_docs.links import check_links
from spark_docs.parser import inline_xml, parse_markdown
from spark_docs.pdf import LAST_LAYOUT, build_pdf

DOC = """---
title: Map
---

# 01 Start

## Hello

Hello world this is a short paragraph.

See [details](#details) and <https://example.com/docs>.

| A | B |
| --- | --- |
| 1 | 2 |

```mermaid
flowchart LR
  A[One] --> B[Two]
```

# 02 Details

## Details

Done.
"""


def test_layout_map_outline_and_links(tmp_path):
    meta, blocks = parse_markdown(DOC)
    pdf = tmp_path / "map.pdf"
    build_pdf(meta, blocks, pdf)
    layout = tmp_path / "map.layout.json"
    assert layout.is_file()
    body = [b for b in LAST_LAYOUT if b["kind"] == "body" and any("Hello world" in ln["text"] for ln in b["lines"])]
    assert body
    box = body[0]
    assert box["page"] >= 1
    assert 0 < box["y"] < 842
    assert box["h"] > 0
    assert any(b["cells"] for b in LAST_LAYOUT)
    assert any(n["text"] for b in LAST_LAYOUT for n in b["nodes"])

    import pymupdf

    with pymupdf.open(pdf) as doc:
        toc = doc.get_toc()
        assert any("START" in entry[1].upper() for entry in toc)
        assert any("DETAILS" in entry[1].upper() for entry in toc)
        text = "".join(page.get_text() for page in doc)
    assert "example.com/docs" in text
    assert not check_links(DOC, pdf)


def test_glyph_fallback_and_missing(tmp_path):
    meta, blocks = parse_markdown("# 01 Marks\n\n## Marks\n\nYes ✓ and no ✗ and bad \ue000.\n")
    pdf = tmp_path / "marks.pdf"
    from spark_docs import pdf as pdfmod

    build_pdf(meta, blocks, pdf)
    import pymupdf

    with pymupdf.open(pdf) as doc:
        text = "".join(page.get_text() for page in doc)
    assert "√" in text and "×" in text
    assert "\ue000" not in text
    assert any("U+E000" in w for w in pdfmod.WARNINGS)


def test_reference_links_footnotes_and_bad_targets():
    md = "See [the spec][s] and a note [^1].\n\n[s]: https://example.com/spec\n\n[^1]: The note itself.\n"
    meta, blocks = parse_markdown(md)
    text = " ".join(b.get("text") or "" for b in blocks)
    assert "https://example.com/spec" in text
    assert "[#fn-1]" in text
    assert "[s]:" not in text
    assert 'href="https://example.com/docs"' in inline_xml("Go <https://example.com/docs> now.")
    assert not check_links(md)
    issues = check_links("See [gone](#missing) and [x](javascript:alert(1)) and [nope][missing].\n")
    assert any("missing" in i for i in issues)
    assert any("invalid link" in i for i in issues)
    assert any("[nope]" in i for i in issues)
