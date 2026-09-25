from spark_docs.parser import parse_markdown


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
