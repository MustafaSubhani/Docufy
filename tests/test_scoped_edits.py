"""Scoped edits stay inside the selection. One hundred scripted edits check the guard."""

import json

from create_doc import main
from diagrams.spec import Node, elk_node, pin_graph
from spark_agent.edit import (
    EditRequest,
    EditScope,
    accept,
    apply_edit,
    locate_quote,
    region_scope,
    remake_spec,
    resolve_phrase,
    ripple,
    scope_violations,
    tweak_spec,
)
from spark_agent.llm import Router
from spark_agent.loop import run_loop
from spark_docs.ir import Block, Doc, load_markdown

BODY = "\n\n".join(f"Paragraph {i} keeps its own words." for i in range(20))
DOC = f"# 01 Body\n\n## Body\n\n{BODY}\n"


def _paras(doc: Doc) -> list[Block]:
    return [block for section in doc.parts[0].sections for block in section.blocks if block.kind == "para"]


def _text(doc: Doc, block_id: str) -> str:
    for block in _paras(doc):
        if block.id == block_id:
            return str(block.data.get("text") or "")
    raise AssertionError(block_id)


def _fake(model, messages, schema):
    user = messages[-1]["content"]
    if "KIND: text range" in user:
        return "holds"
    if "KIND: table cell" in user:
        return "9"
    if "KIND: list item" in user:
        return "Edited item"
    if "VARIANT 2" in user:
        return "option two"
    if user.startswith("VARIANT") and "---" in user and user.count("VARIANT") == 1 and "rejected" not in user:
        return "---\ntitle: spilled"
    found = __import__("re").search(r"Paragraph (\d+)", user)
    if found:
        return f"Paragraph {found.group(1)} was edited."
    if "rejected" in user:
        return "short"
    return "short"


def test_one_hundred_edits_stay_inside_the_scope():
    doc = load_markdown(DOC)
    paras = _paras(doc)
    router = Router(profile="dev", fake=_fake)
    originals = {block.id: block.data["text"] for block in paras}
    for n in range(100):
        block = paras[n % 20]
        before = {bid: _text(doc, bid) for bid in originals}
        if n < 20:
            text = before[block.id]
            start = text.index("keeps")
            request = EditRequest(
                scope=EditScope(kind="text_range", ids=[block.id], start=start, end=start + len("keeps")),
                instruction="use holds",
            )
            doc = apply_edit(doc, request, router).doc
            updated = _text(doc, block.id)
            assert updated == text.replace("keeps", "holds")
        else:
            request = EditRequest(scope=EditScope(kind="block", ids=[block.id]), instruction="shorten")
            doc = apply_edit(doc, request, router).doc
            assert "was edited" in _text(doc, block.id)
        for bid, text in before.items():
            if bid == block.id:
                continue
            assert _text(doc, bid) == text


def test_scope_guard_reports_a_block_outside_the_edit():
    doc = load_markdown(DOC)
    paras = _paras(doc)
    after = doc.model_copy(deep=True)
    after.parts[0].sections[0].blocks[1].data["text"] = "touched"
    assert paras[1].id in scope_violations(doc, after, {paras[0].id})


def test_locked_and_hand_edited_blocks_are_skipped_unless_named():
    doc = load_markdown("# 01 Overview\n\n## Overview\n\nFirst paragraph stays.\n\nSecond paragraph changes.\n")
    first, second = _paras(doc)
    first.locked = True
    second.edited_by = "user"
    third_doc = doc.model_copy(deep=True)
    router = Router(profile="dev", fake=_fake)
    result = apply_edit(
        doc,
        EditRequest(scope=EditScope(kind="section", ids=[doc.parts[0].sections[0].id]), instruction="shorten"),
        router,
    )
    assert first.id in result.skipped and second.id in result.skipped
    assert _text(result.doc, first.id) == "First paragraph stays."
    named = apply_edit(third_doc, EditRequest(scope=EditScope(kind="block", ids=[first.id]), instruction="shorten"), router)
    assert named.doc.parts[0].sections[0].blocks[0].data["text"] == "short"


def test_text_quote_list_item_and_table_cell():
    doc = load_markdown("# 01 Bits\n\n## Bits\n\nSee the fleet report.\n\n- Alpha\n- Beta\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n")
    found = locate_quote(doc, "fleet report")
    assert found.scope is not None and found.scope.kind == "text_range"
    router = Router(profile="dev", fake=_fake)
    listed = apply_edit(doc, EditRequest(scope=EditScope(kind="items", ids=[f"{_blocks(doc)[1].id}.i2"]), instruction="edit"), router)
    items = [block for block in listed.doc.parts[0].sections[0].blocks if block.kind == "list"][0].data["items"]
    assert items[0]["text"] == "Alpha" and items[1]["text"] == "Edited item"
    table = [block for block in doc.parts[0].sections[0].blocks if block.kind == "table"][0]
    edited = apply_edit(doc, EditRequest(scope=EditScope(kind="table_cells", ids=[f"{table.id}.r1c2"]), instruction="edit"), router)
    rows = [block for block in edited.doc.parts[0].sections[0].blocks if block.kind == "table"][0].data["rows"]
    assert rows[0][0] == "1" and rows[0][1] == "9"


def test_ambiguous_phrase_does_not_edit_and_ripple_only_suggests():
    doc = load_markdown("# 01 Summary\n\n## Summary\n\nOne.\n\n# 02 Summary\n\n## Summary\n\nTwo.\n")
    found = resolve_phrase(doc, "shorten the second paragraph of Summary")
    assert found.scope is None and len(found.choices) == 2
    left = load_markdown("# 01 Plan\n\n## Plan\n\nWeek 1-2 is the build.\n\n# 02 Notes\n\n## Notes\n\nSee Week 1-2 in the plan.\n")
    paras = _paras(left)
    paras[0].data["text"] = "Week 3-4 is the build."
    notes = [suggestion for suggestion in ripple(load_markdown("# 01 Plan\n\n## Plan\n\nWeek 1-2 is the build.\n\n# 02 Notes\n\n## Notes\n\nSee Week 1-2 in the plan.\n"), left, {paras[0].id})]
    assert notes and notes[0].block_id == paras[1].id
    assert "Week 1-2" in _text(left, paras[1].id)


def test_delete_renumbers_and_leaves_the_other_paragraph():
    doc = load_markdown("# 01 One\n\n## One\n\nKeep me.\n\n# 02 Two\n\n## Two\n\nDrop me.\n\n# 03 Three\n\n## Three\n\nAlso keep.\n")
    middle = doc.parts[0].sections[1].id
    result = apply_edit(doc, EditRequest(scope=EditScope(kind="section", ids=[middle]), instruction="remove", intent="delete"))
    kickers = [section.kicker for section in result.doc.parts[0].sections]
    assert kickers == ["01 One", "02 Three"]
    assert "Drop me." not in result.doc.model_dump_json()
    assert "Also keep." in result.doc.model_dump_json()


def test_diagram_tweak_pins_the_other_node_and_remake_clears_pins():
    spec = {"direction": "TB", "nodes": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}], "edges": []}
    tweaked = tweak_spec(spec, ["a"], {"a": {"title": "Start"}}, "LR")
    by_id = {node["id"]: node for node in tweaked["nodes"]}
    assert by_id["a"]["title"] == "Start" and "pin_x" not in by_id["a"]
    assert by_id["b"]["pin_x"] == 160 and tweaked["direction"] == "LR"
    remade = remake_spec(tweaked, ["a"], {"a": {"title": "Start"}})
    assert all("pin_x" not in node for node in remade["nodes"])
    assert remade["layout"] == "auto"
    bare = elk_node("a", 10, 10, None, None)
    pinned = elk_node("b", 10, 10, 4, 8)
    assert "layoutOptions" not in bare
    assert pinned["layoutOptions"]["elk.position"] == "(4.0,8.0)"
    graph = {"layoutOptions": {"elk.algorithm": "layered"}}
    assert "semiInteractive" not in pin_graph(graph, [Node("a", "A")])["layoutOptions"]
    assert pin_graph({"layoutOptions": {}}, [Node("b", "B", pin_x=1, pin_y=2)])["layoutOptions"]["elk.layered.crossingMinimization.semiInteractive"] == "true"


def test_variants_and_a_spilled_reply_is_retried_once():
    doc = load_markdown("# 01 Overview\n\n## Overview\n\nLeave this sentence alone.\n")
    calls = {"n": 0}

    def fake(model, messages, schema):
        calls["n"] += 1
        if calls["n"] == 1:
            return "---\ntitle: whole document"
        return "short"

    router = Router(profile="dev", fake=fake)
    result = apply_edit(doc, EditRequest(scope=EditScope(kind="block", ids=[_paras(doc)[0].id]), instruction="shorten"), router)
    assert result.doc.parts[0].sections[0].blocks[0].data["text"] == "short"
    assert calls["n"] == 2
    both = apply_edit(
        doc,
        EditRequest(scope=EditScope(kind="block", ids=[_paras(doc)[0].id]), instruction="shorten", variants=2),
        Router(profile="dev", fake=_fake),
    )
    assert [item.parts[0].sections[0].blocks[0].data["text"] for item in both.variants] == ["short", "option two"]
    assert accept(both, 2).parts[0].sections[0].blocks[0].data["text"] == "option two"


def test_region_selects_overlapping_blocks():
    scope = region_scope(
        {"blocks": [{"id": "b3", "page": 1, "x": 60, "y": 400, "w": 200, "h": 40}, {"id": "b4", "page": 1, "x": 60, "y": 100, "w": 40, "h": 20}]},
        1,
        [50, 390, 300, 80],
    )
    assert scope.ids == ["b3"]


def test_verify_loop_does_not_rewrite_a_locked_paragraph():
    md = "# 01 One\n\n## One\n\nThe fleet is organized and ready.\n\nThe other team is organized too.\n"

    def fake(model, messages, schema):
        if "Verification critic" in messages[-1]["content"]:
            return json.dumps({"issues": []})
        return "ok"

    res = run_loop(md, Router(profile="dev", fake=fake), protected=("The other team is organized too.",), log=lambda *_: None)
    assert "organised and ready" in res.markdown
    assert "organized too" in res.markdown
    assert res.status == "clean"


def test_cli_manual_edit_undo_and_lock(tmp_path):
    path = tmp_path / "note.md"
    original = "# 01 Body\n\n## Body\n\nHello there.\n"
    path.write_text(original, encoding="utf-8")
    doc = load_markdown(original)
    para = _paras(doc)[0].id
    assert main(["edit", str(path), "--manual", para, "--replace", "Hand written."]) == 0
    assert "Hand written." in path.read_text(encoding="utf-8")
    assert main(["edit", str(path), "--undo"]) == 0
    assert path.read_text(encoding="utf-8") == original
    assert main(["edit", str(path), "--redo"]) == 0
    assert "Hand written." in path.read_text(encoding="utf-8")
    assert main(["edit", str(path), "--lock", para]) == 0
    from spark_agent.edit import read_doc

    locked, _ = read_doc(path)
    assert _paras(locked)[0].locked
    ambiguous = tmp_path / "two.md"
    ambiguous.write_text("# 01 Summary\n\n## Summary\n\nOne.\n\n# 02 Summary\n\n## Summary\n\nTwo.\n", encoding="utf-8")
    before = ambiguous.read_text(encoding="utf-8")
    assert main(["edit", str(ambiguous), "--ask", "shorten the second paragraph of Summary"]) == 2
    assert ambiguous.read_text(encoding="utf-8") == before


def _blocks(doc: Doc) -> list[Block]:
    return [block for section in doc.parts[0].sections for block in section.blocks]
