"""Geometry checks for a laid-out diagram.

Text must sit inside its node, boxes must not overlap, arrows must not cut through
a node, and the type must stay at least 7pt once the figure is placed on the page.
A diagram that is too tall is re-laid out left-to-right by the scene builder.
"""

from __future__ import annotations

from . import style as S
from .scene import text_w

MIN_PRINT_PT = 7.0


def text_overflow(scene) -> list[str]:
    bad = []
    for n in scene.nodes:
        if n.shape == "label":
            continue
        pad = S.NODE_PAD_X - 4
        limit = n.w * (0.72 if n.shape == "diamond" else 1.0) - 2 * pad
        for ln in n.lines:
            if text_w(ln.text, ln.size, ln.bold) > limit + 0.5:
                bad.append(f"{n.id}: {ln.text!r}")
    return bad


def overlaps(scene) -> list[str]:
    boxes = [n for n in scene.nodes if n.shape != "label"]
    bad = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            if a.x < b.x + b.w - 1 and b.x < a.x + a.w - 1 and a.y < b.y + b.h - 1 and b.y < a.y + a.h - 1:
                bad.append(f"{a.id} overlaps {b.id}")
    return bad


def edges_through_nodes(scene) -> list[str]:
    boxes = [n for n in scene.nodes if n.shape not in {"label"}]
    bad = []
    for e in scene.edges:
        pts = e.points
        if len(pts) < 2:
            continue
        for n in boxes:
            if _cuts(pts, n):
                bad.append(f"{getattr(e, 'label', '') or 'arrow'} through {n.id}")
                break
    return bad


def _cuts(pts, node) -> bool:
    inset = 5.0
    x0, y0, x1, y1 = node.x + inset, node.y + inset, node.x + node.w - inset, node.y + node.h - inset
    if x1 <= x0 or y1 <= y0:
        return False
    start, end = pts[0], pts[-1]

    def near_end(x, y) -> bool:
        return min(_dist(x, y, start), _dist(x, y, end)) < 8

    for a, b in zip(pts, pts[1:]):
        for t in (0.25, 0.5, 0.75):
            x = a[0] + (b[0] - a[0]) * t
            y = a[1] + (b[1] - a[1]) * t
            if near_end(x, y):
                continue
            if x0 < x < x1 and y0 < y < y1:
                return True
    return False


def _dist(x, y, p) -> float:
    return ((x - p[0]) ** 2 + (y - p[1]) ** 2) ** 0.5


def issues(scene, placement_pt: float = 475.0) -> list[str]:
    found = [f"text overflow: {m}" for m in text_overflow(scene)]
    found += [f"overlap: {m}" for m in overlaps(scene)]
    found += [f"edge through node: {m}" for m in edges_through_nodes(scene)]
    if scene.width and S.TITLE_SIZE * placement_pt / scene.width < MIN_PRINT_PT:
        found.append("diagram text would print below 7pt")
    return found
