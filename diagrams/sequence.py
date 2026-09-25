"""Mermaid sequenceDiagram -> Claude-style scene (participants, lifelines, messages, notes, frames)."""

from __future__ import annotations

import re

from . import style as S
from .scene import Scene, SEdge, SGroup, SNode, TextLine, text_w, wrap

PART_RE = re.compile(r"^(participant|actor)\s+(?P<id>[^\s]+)(?:\s+as\s+(?P<label>.+))?$", re.I)
MSG_RE = re.compile(r"^(?P<a>[^-<>]+?)\s*(?P<op>-->>|->>|-->|->|--x|-x|--\)|-\))\s*(?P<b>[^:]+?)\s*:\s*(?P<text>.*)$")
NOTE_RE = re.compile(r"^note\s+(?P<pos>over|left of|right of)\s+(?P<who>[^:]+):\s*(?P<text>.*)$", re.I)
FRAME_RE = re.compile(r"^(?P<kind>loop|alt|opt|par|critical|break|rect)\b\s*(?P<text>.*)$", re.I)

COL_TOP = 20.0
BOX_H = 40.0
FIRST_GAP = 34.0
STEP = 36.0


def parse(source: str) -> dict | None:
    lines = [ln.strip() for ln in source.strip().splitlines()]
    if not lines or not lines[0].lower().startswith("sequencediagram"):
        return None
    participants: list[dict] = []
    events: list[dict] = []
    autonumber = False

    def ensure(pid: str) -> None:
        pid = pid.strip()
        if pid and not any(p["id"] == pid for p in participants):
            participants.append({"id": pid, "label": pid})

    for ln in lines[1:]:
        if not ln or ln.startswith("%%"):
            continue
        if ln.lower() == "autonumber":
            autonumber = True
            continue
        m = PART_RE.match(ln)
        if m:
            pid = m.group("id")
            ensure(pid)
            if m.group("label"):
                next(p for p in participants if p["id"] == pid)["label"] = m.group("label").strip()
            continue
        m = NOTE_RE.match(ln)
        if m:
            who = [w.strip() for w in m.group("who").split(",")]
            for w in who:
                ensure(w)
            events.append({"type": "note", "who": who, "pos": m.group("pos").lower(), "text": m.group("text").strip()})
            continue
        m = FRAME_RE.match(ln)
        if m:
            events.append({"type": "frame_start", "kind": m.group("kind").lower(), "text": m.group("text").strip()})
            continue
        if ln.lower().startswith(("else", "and ")) or ln.lower() == "else":
            events.append({"type": "frame_else", "text": ln[4:].strip()})
            continue
        if ln.lower() == "end":
            events.append({"type": "frame_end"})
            continue
        if ln.lower().startswith(("activate", "deactivate")):
            continue
        m = MSG_RE.match(ln)
        if m:
            a, b = m.group("a").strip().lstrip("+-"), m.group("b").strip().lstrip("+-")
            ensure(a)
            ensure(b)
            events.append({"type": "msg", "src": a, "dst": b, "text": m.group("text").strip(), "dashed": m.group("op").startswith("--")})
    if not participants:
        return None
    return {"type": "sequence", "participants": participants, "events": events, "autonumber": autonumber}


def build_scene(spec: dict) -> Scene:
    parts = spec["participants"]
    n = len(parts)
    avail = S.CANVAS_W - 2 * S.MARGIN
    col_w = avail / n
    xs = {p["id"]: S.MARGIN + col_w * (i + 0.5) for i, p in enumerate(parts)}
    scene = Scene(width=S.CANVAS_W, height=0)
    ramps = ["blue"] + ["purple"] * max(n - 2, 0) + (["teal"] if n > 1 else [])
    for i, p in enumerate(parts):
        ramp = ramps[i]
        r = S.ramp(ramp)
        lines = [TextLine(t, S.SUB_SIZE + 1, r[S.STOP["800"]]) for t in wrap(p["label"], S.SUB_SIZE + 1, col_w - 20)]
        w = min(col_w - 8, max(text_w(ln.text, ln.size) for ln in lines) + 24)
        h = max(BOX_H, 16 * len(lines) + 16)
        scene.nodes.append(SNode(p["id"], xs[p["id"]] - w / 2, COL_TOP, w, h, ramp, "rect", lines))
    head_bottom = COL_TOP + max(sn.h for sn in scene.nodes)
    y = head_bottom + FIRST_GAP
    frames: list[dict] = []
    number = 0
    for ev in spec["events"]:
        t = ev["type"]
        if t == "msg":
            number += 1
            a, b = xs[ev["src"]], xs[ev["dst"]]
            label = f"{number}. {ev['text']}" if spec.get("autonumber") else ev["text"]
            color = S.ramp("gray")[S.STOP["600"]] if ev["dashed"] else S.ramp("purple")[S.STOP["600"]]
            if abs(a - b) < 1:  # self message: small loop to the right
                pts = [(a, y - 8), (a + 34, y - 8), (a + 34, y + 6), (a + 2, y + 6)]
                scene.edges.append(SEdge(pts, color, ev["dashed"], label, (a + 40 + text_w(label, S.LABEL_SIZE) / 2, y - 1)))
            else:
                start = a + (4 if b > a else -4)
                end = b + (-4 if b > a else 4)
                scene.edges.append(SEdge([(start, y), (end, y)], color, ev["dashed"], label, ((a + b) / 2, y - 11)))
            y += STEP
        elif t == "note":
            who = [w for w in ev["who"] if w in xs]
            lo, hi = min(xs[w] for w in who), max(xs[w] for w in who)
            text_lines = wrap(ev["text"], S.SUB_SIZE, max(hi - lo + col_w * 0.8, 120) - 20)
            w = max(max(text_w(t, S.SUB_SIZE) for t in text_lines) + 20, hi - lo + 40)
            h = 16 * len(text_lines) + 12
            cx = (lo + hi) / 2
            if ev["pos"] == "right of":
                cx = lo + w / 2 + 10
            elif ev["pos"] == "left of":
                cx = lo - w / 2 - 10
            r = S.ramp("amber")
            scene.nodes.append(
                SNode(f"note{len(scene.nodes)}", cx - w / 2, y - 12, w, h, "amber", "rect", [TextLine(t, S.SUB_SIZE, r[S.STOP["800"]]) for t in text_lines])
            )
            y += h + 14
        elif t == "frame_start":
            frames.append({"y": y - 26, "text": f"{ev['kind']} {ev['text']}".strip()})
            y += 8
        elif t == "frame_end" and frames:
            f = frames.pop()
            scene.groups.append(SGroup(S.MARGIN - 8, f["y"], avail + 16, y - f["y"] - 10, f["text"], "", None))
            y += 6
    bottom = y - 10
    for p in parts:  # lifelines behind everything
        x = xs[p["id"]]
        scene.edges.insert(0, SEdge([(x, head_bottom), (x, bottom)], S.ramp("gray")[S.STOP["200"]], True, arrow=False, width=1.0))
    scene.height = bottom + 20
    return scene
