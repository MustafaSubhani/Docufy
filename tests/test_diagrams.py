import re
from pathlib import Path

import pytest
import yaml

from diagrams import style as S
from diagrams.mermaid_import import parse as parse_mermaid
from diagrams.render import render_svg, scene_from_spec
from diagrams.scene import build_scene, text_w
from diagrams.sequence import parse as parse_sequence
from diagrams.spec import Diagram

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
    specs.append(pytest.param(yaml.safe_load((ROOT / "tests/golden/diagrams/p06_estimation.yaml").read_text(encoding="utf-8")), id="p06-grid"))
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


def test_linear_chain_snakes_into_rows():
    d = parse_mermaid("flowchart TD\n" + "\n".join(f"  N{i}[Step {i}] --> N{i + 1}[Step {i + 1}]" for i in range(6)))
    scene = build_scene(d)
    rows = {round(n.y) for n in scene.nodes}
    assert len(rows) == 2
