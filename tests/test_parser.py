from spark_agent.blocks import segment
from spark_docs.parser import parse_markdown, tighten_tables


def kinds(md: str) -> list[str]:
    return [b["kind"] for b in parse_markdown(md)[1]]


def test_new_containers():
    md = """
:::steps
### Proposal Review
Client reviews.

### Kickoff
Team onboarded.
:::

:::roadmap widths=348,120
| Deliverables | Duration |
| --- | --- |
| Discovery | Week 1-2 |
:::

:::commercial
| Item | Description | Investment |
| --- | --- | --- |
| Module 1 | Build | AED 10 |
| TOTAL INVESTMENT |  | AED 10 |
:::

:::kpis
### Module 1
- faster takeoffs
:::

:::tight
Line one

Line two
:::
"""
    meta, blocks = parse_markdown(md)
    assert [b["kind"] for b in blocks] == ["steps", "roadmap", "commercial", "kpis", "tight"]
    assert blocks[0]["steps"][1] == {"title": "Kickoff", "body": "Team onboarded."}
    assert blocks[1]["widths"] == [348.0, 120.0]
    assert blocks[2]["total"][0] == "TOTAL INVESTMENT" and len(blocks[2]["rows"]) == 1
    assert blocks[3]["groups"][0]["items"] == ["faster takeoffs"]


def test_heading_flags_and_labels():
    meta, blocks = parse_markdown("## KPIs {.ruled .large}\n\n**Scope includes:**\n\n<!-- pagebreak -->\n")
    assert blocks[0]["ruled"] and blocks[0]["large"] and blocks[0]["text"] == "KPIs"
    assert blocks[1]["kind"] == "label"
    assert blocks[2]["kind"] == "pagebreak"


def test_image_width_attr(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"")
    meta, blocks = parse_markdown(f"![Fig]({img.as_posix()}){{width=432}}", source_dir=tmp_path)
    assert blocks[0]["kind"] == "image" and blocks[0]["width"] == 432.0


def test_addendum_fields():
    meta, blocks = parse_markdown(":::addendum\nlabel: ADDENDUM A\ntitle: ADICC\nnote: Read with the proposal.\n:::")
    assert blocks[0]["fields"]["label"] == "ADDENDUM A"
    assert blocks[0]["fields"]["note"] == "Read with the proposal."


GAPPED = """| Export | What you get |

|---|---|

| **CSV** | The columns you are showing. |

| **Excel** | A workbook. |

Next paragraph.
"""


def test_blank_lines_between_rows_still_make_one_table():
    blocks = parse_markdown(GAPPED)[1]
    tables = [block for block in blocks if block["kind"] == "table"]
    assert len(tables) == 1
    assert tables[0]["headers"] == ["Export", "What you get"]
    assert tables[0]["rows"][0] == ["**CSV**", "The columns you are showing."]
    assert tables[0]["rows"][1][0] == "**Excel**"
    assert blocks[-1]["text"].startswith("Next")


def test_a_blank_line_between_tables_keeps_them_apart():
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n| C | D |\n| --- | --- |\n| 3 | 4 |\n"
    tables = [block for block in parse_markdown(md)[1] if block["kind"] == "table"]
    assert len(tables) == 2
    assert tables[0]["rows"] == [["1", "2"]]
    assert tables[1]["headers"] == ["C", "D"]


def test_segment_joins_rows_that_were_split_by_blank_lines():
    blocks = segment(GAPPED)
    assert [block.kind for block in blocks] == ["table", "para"]
    assert blocks[0].text == "| Export | What you get |\n|---|---|\n| **CSV** | The columns you are showing. |\n| **Excel** | A workbook. |"


def test_tighten_tables_closes_gaps_inside_one_table_only():
    raw = "| A | B |\n\n|---|---|\n\n| 1 | 2 |\n\n| C | D |\n|---|---|\n| 3 | 4 |\n"
    tight = tighten_tables(raw)
    assert tight == "| A | B |\n|---|---|\n| 1 | 2 |\n\n| C | D |\n|---|---|\n| 3 | 4 |\n"


def test_a_gapped_table_renders_without_the_pipes(tmp_path):
    import pymupdf

    from spark_docs.pdf import build_pdf

    source = tmp_path / "tables.md"
    source.write_text(
        "---\ntitle: Tables\ndoc_type: DOCUMENTATION\ninclude_spark_content: false\n---\n\n# Tables\n\n" + GAPPED,
        encoding="utf-8",
    )
    meta, blocks = parse_markdown(source.read_text(encoding="utf-8"))
    out = build_pdf(meta, blocks, tmp_path / "tables.pdf")
    with pymupdf.open(out) as doc:
        text = "".join(page.get_text() for page in doc)
    assert "What you get" in text and "CSV" in text
    assert "|---|" not in text and "| CSV |" not in text
