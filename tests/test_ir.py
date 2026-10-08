"""Document IR: Spark markdown round trip, GFM export, DOCX named styles."""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING

from create_doc import main
from spark_docs.docx_export import build_docx
from spark_docs.ir import Block, document_from_parse, load_markdown, to_blocks, to_gfm, to_spark_markdown
from spark_docs.parser import parse_markdown

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample.md"

RICH = """
---
title: Rich
subtitle: Round trip
doc_type: NOTE
prepared_for: ADICC
date: June 2026
show_contact: true
---

# 01 Components {.unnumbered}

## Roadmap {.ruled .large}

A paragraph with {{calc:fee}} and a dangling [^S12].

**Includes:**

- Parent
  - Child

1. First step

See the note [^n1].

[^n1]: A cited fact.

Read the [guide][g].

[g]: https://sparktechnologies.ai

> **Note:**
> Keep this short.

:::columns
### Left
Left body.

- Left item

### Right
Right body.
:::

:::callout
### Boxed
Inside the box.
:::

:::cards
### One
Alpha.
:::

:::steps
### Discover
Interview the team.

### Build
Ship the module.
:::

:::kpis
### Speed
- Cycle time
### Quality
- Accuracy
:::

:::roadmap widths=200,120 bold_first
| Phase | Weeks |
| --- | --- |
| Discovery | 2 |
:::

:::commercial
| Item | Fee |
| --- | --- |
| Build | 10 |
| TOTAL | 10 |
:::

:::tight
- Tight item
:::

:::large
- Large item
:::

:::stageflow
### Ingest {layer=bot}
Pull the files.

:::callout
### Store
Keep the original.
:::
:::

```diagram
title: Flow
direction: LR
nodes:
  - id: a
    title: Start
  - id: b
    title: End
edges:
  - src: a
    dst: b
```

```mermaid
sequenceDiagram
    Alice->>Bob: Hello
```

```python
print("hi")
```

![Logo](assets/spark-logo.png){width=80}

---

<!-- pagebreak -->

### Module 2 — Scheduling

# 02 Close

## Done {.compact}

Closing line.

:::addendum
title: Annex A
prepared_for: ADICC
:::

# A1 Annex

## Notes

Annex body.
""".lstrip()

DOCX_MD = """
---
title: IR Check
doc_type: NOTE
show_contact: false
---

# 01 Checks

## What we verify

A paragraph with a [site](https://sparktechnologies.ai) link.

| Item | Status |
| --- | --- |
| Styles | Pass |

:::cards
### One
Alpha.

### Two
Beta.
:::

:::steps
### First
Do this.
:::
""".lstrip()


def _strip(value):
    skip = {"id", "rev", "origin", "edited_by", "locked", "variant", "style_overrides"}
    if isinstance(value, dict):
        return {key: _strip(item) for key, item in value.items() if key not in skip}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def _same(text: str, source_dir: Path) -> None:
    doc = load_markdown(text, source_dir=source_dir)
    meta, blocks = parse_markdown(text, source_dir=source_dir)
    assert _strip(to_blocks(doc)) == _strip(blocks)
    again = load_markdown(to_spark_markdown(doc), source_dir=source_dir)
    third = load_markdown(to_spark_markdown(again), source_dir=source_dir)
    assert again.model_dump() == doc.model_dump()
    assert third.model_dump() == doc.model_dump()
    assert document_from_parse(meta, blocks).model_dump() == doc.model_dump()


def test_sample_round_trip():
    text = SAMPLE.read_text(encoding="utf-8")
    doc = load_markdown(text, source_dir=SAMPLE.parent)
    assert doc.closing is not None
    assert "why choose" in doc.closing.title.lower()
    assert doc.parts[0].sections[0].id == "b1"
    _same(text, SAMPLE.parent)


def test_rich_round_trip():
    doc = load_markdown(RICH, source_dir=ROOT)
    kinds = [block.kind for section in doc.parts[0].sections for block in section.blocks]
    assert "diagram" in kinds
    assert "commercial" in kinds
    assert "stageflow" in kinds
    assert doc.parts[-1].kind == "addendum"
    assert any(section.kicker.startswith("A1") for section in doc.parts[-1].sections)
    _same(RICH, ROOT)


def test_adjacent_lists_stay_split():
    doc = load_markdown("# T\n\n## Title\n\n- A\n", source_dir=ROOT)
    doc.parts[0].sections[0].blocks.append(
        Block(id="extra", kind="list", data={"ordered": False, "items": [{"text": "B", "indent": 0}]})
    )
    again = load_markdown(to_spark_markdown(doc), source_dir=ROOT)
    lists = [block for block in again.parts[0].sections[0].blocks if block.kind == "list"]
    assert len(lists) == 2
    assert lists[1].data["items"][0]["text"] == "B"


def test_gfm_exports_diagram_image(tmp_path: Path):
    doc = load_markdown(RICH, source_dir=ROOT)
    folder = tmp_path / "files"
    gfm = to_gfm(doc, asset_dir=folder)
    assert "Flow" in gfm or "Start" in gfm
    assert "```" in gfm or "![" in gfm
    pngs = list(folder.glob("*.png"))
    if pngs:
        assert pngs[0].name in gfm


def test_docx_named_styles(tmp_path: Path):
    meta, blocks = parse_markdown(DOCX_MD, source_dir=ROOT)
    path = tmp_path / "check.docx"
    build_docx(meta, blocks, path)
    doc = Document(str(path))
    xml = doc.element.xml
    names = {p.style.name for p in doc.paragraphs if p.style}
    assert "Spark Kicker" in names
    assert "Spark Title" in names
    assert "Spark Body" in names
    kicker = doc.styles["Spark Kicker"].element.xml
    assert "w:bottom" in kicker
    body = doc.styles["Spark Body"]
    assert body.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert body.paragraph_format.line_spacing_rule == WD_LINE_SPACING.EXACTLY
    assert abs(body.paragraph_format.line_spacing.pt - 11.5) < 0.05
    assert "w:bookmarkStart" not in xml
    assert "w:tblHeader" in xml
    assert "w:cantSplit" in xml
    assert "w:hyperlink" in xml
    assert "fixed" in xml


def test_cli_spark_markdown(tmp_path: Path):
    out = tmp_path / "sample.spark.md"
    assert main([str(SAMPLE), "--format", "spark", "-o", str(out)]) == 0
    text = SAMPLE.read_text(encoding="utf-8")
    saved = load_markdown(out.read_text(encoding="utf-8"), source_dir=SAMPLE.parent)
    original = load_markdown(text, source_dir=SAMPLE.parent)
    assert saved.model_dump() == original.model_dump()
