import re
from pathlib import Path

import pytest
import yaml

from diagrams import style as S
from diagrams.mermaid_import import parse as parse_mermaid
from diagrams.qa import issues as qa_issues
from diagrams.qa import overlaps
from diagrams.render import render_svg, scene_from_spec
from diagrams.scene import Scene, SNode, TextLine, build_scene, text_w
from diagrams.sequence import parse as parse_sequence
from diagrams.spec import Diagram, Edge, Node

ROOT = Path(__file__).resolve().parent.parent


def test_mermaid_flowchart_parse():
    d = parse_mermaid(
        """flowchart LR
  A[PDF drawings]:::input --> B["Element detection<br/>YOLOv10"]:::ai -->|quantities| C([BOQ output]):::output
  D{Scale known?} -- no --> B
  subgraph out [Outputs]
    C
  end"""
    )
    assert d.direction == "LR"
    b = d.node("B")
    assert (b.title, b.subtitle, b.category) == ("Element detection", "YOLOv10", "ai")
    assert d.node("C").shape == "pill" and d.node("C").group == "out"
    assert d.node("D").shape == "diamond"
    assert any(e.src == "B" and e.dst == "C" and e.label == "quantities" for e in d.edges)
    assert any(e.src == "D" and e.dst == "B" and e.label == "no" for e in d.edges)


def test_sequence_parse():
    spec = parse_sequence("sequenceDiagram\n  participant A as Client\n  A->>B: request\n  B-->>A: reply\n  Note over B: stores it")
    assert [p["label"] for p in spec["participants"]] == ["Client", "B"]
    kinds = [e["type"] for e in spec["events"]]
    assert kinds == ["msg", "msg", "note"] and spec["events"][1]["dashed"]


def text_fits(scene) -> list[str]:
    bad = []
    for n in scene.nodes:
        if n.shape == "label":
            continue
        for ln in n.lines:
            limit = n.w * (0.72 if n.shape == "diamond" else 1.0) - 2 * (S.NODE_PAD_X - 4)
            if text_w(ln.text, ln.size) > limit + 0.5:
                bad.append(f"{n.id}: {ln.text!r} ({text_w(ln.text, ln.size):.0f} > {limit:.0f})")
    return bad


def boxes_overlap(scene) -> list[str]:
    boxes = [n for n in scene.nodes if n.shape != "label"]
    bad = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            if a.x < b.x + b.w - 1 and b.x < a.x + a.w - 1 and a.y < b.y + b.h - 1 and b.y < a.y + a.h - 1:
                bad.append(f"{a.id} overlaps {b.id}")
    return bad


def all_input_diagrams():
    specs = []
    for md in sorted((ROOT / "input").rglob("*.md")):
        for m in re.finditer(r"```mermaid\n(.*?)```", md.read_text(encoding="utf-8"), re.S):
            src = m.group(1)
            spec = parse_sequence(src) if src.strip().lower().startswith("sequencediagram") else (parse_mermaid(src) or Diagram()).to_dict()
            if spec and (spec.get("nodes") or spec.get("participants")):
                specs.append(pytest.param(spec, id=f"{md.stem}-{m.start()}"))
    for spec_path in sorted((ROOT / "tests/golden/diagrams").glob("*.yaml")):
        specs.append(pytest.param(yaml.safe_load(spec_path.read_text(encoding="utf-8")), id=spec_path.stem))
    return specs


@pytest.mark.parametrize("spec", all_input_diagrams())
def test_diagram_quality(spec):
    scene = scene_from_spec(spec)
    assert not text_fits(scene), text_fits(scene)
    if spec.get("type") != "sequence":
        assert not boxes_overlap(scene), boxes_overlap(scene)
    assert 0 < scene.height < 4000


def test_svg_export():
    d = parse_mermaid("flowchart TD\n  A[Start] --> B[End]")
    svg = render_svg(d)
    assert svg.startswith("<svg") and "Start" in svg and "marker" in svg


def test_band_spans_the_row_and_lane_labels_wrap():
    scene = scene_from_spec(yaml.safe_load((ROOT / "tests/golden/diagrams/p14_platform.yaml").read_text(encoding="utf-8")))
    band = next(n for n in scene.nodes if n.id == "found")
    card = next(n for n in scene.nodes if n.id == "m1")
    label = next(n for n in scene.nodes if n.id == "h_in")
    assert band.shape == "band" and band.w > card.w * 2.5
    assert label.shape == "label" and label.lines
    assert not overlaps(scene)
    pipes = scene_from_spec(yaml.safe_load((ROOT / "tests/golden/diagrams/p27_pipeline.yaml").read_text(encoding="utf-8")))
    assert any(not e.arrow for e in pipes.edges)  # the four pipeline tails join one rail into the dashboard


def test_timeline_is_one_row_on_a_spine():
    d = Diagram(
        layout="timeline",
        nodes=[
            Node(id="a", title="Discovery", subtitle="Week 1-2", category="blue"),
            Node(id="b", title="Build", subtitle="Week 3-6", category="purple"),
            Node(id="c", title="Handover", subtitle="Week 7", category="teal"),
        ],
    )
    scene = build_scene(d)
    assert len({round(n.y) for n in scene.nodes}) == 1
    assert any(e.points[0][1] == e.points[1][1] and not e.arrow for e in scene.edges)


def test_tall_diagram_is_relayout_as_a_row():
    nodes = [Node(id=f"n{i}", title=f"Step {i} of the process") for i in range(8)]
    edges = [Edge(f"n{i}", f"n{i+1}") for i in range(7)]
    edges.append(Edge("n0", "n4", label="skip"))
    scene = build_scene(Diagram(direction="TB", nodes=nodes, edges=edges))
    xs = {round(n.x, -1) for n in scene.nodes}
    assert len(xs) > 1


def test_qa_flags_overlapping_boxes():
    scene = Scene(width=200, height=80)
    line = TextLine("Hello", 14, "#222", False)
    scene.nodes = [
        SNode("a", 0, 0, 80, 40, "gray", "rect", [line]),
        SNode("b", 20, 10, 80, 40, "gray", "rect", [line]),
    ]
    assert any("overlap" in item for item in qa_issues(scene))


def test_linear_chain_snakes_into_rows():
    d = parse_mermaid("flowchart TD\n" + "\n".join(f"  N{i}[Step {i}] --> N{i + 1}[Step {i + 1}]" for i in range(6)))
    scene = build_scene(d)
    rows = {round(n.y) for n in scene.nodes}
    assert len(rows) == 2
