"""Record where each block landed: page, box, lines, table cells, cards, diagram nodes.

Coordinates are PDF points, origin at the bottom-left of the page. `y_top` is the
top-down distance from the top of the page, matching the measurement tool.
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab import rl_config


def assign_ids(blocks: list[dict]) -> None:
    n = 0

    def visit(b):
        nonlocal n
        if not isinstance(b, dict):
            return
        if b.get("kind") and not b.get("id"):
            n += 1
            b["id"] = f"b{n}"
        for key in ("blocks", "columns", "cards", "steps", "groups", "items"):
            for child in b.get(key) or []:
                visit(child)

    for b in blocks:
        visit(b)


def capture(flowable, frame_x: float, frame_y: float, page: int, page_h: float) -> dict | None:
    sid = getattr(flowable, "spark_id", None)
    if not sid:
        return None
    content_h = getattr(flowable, "_inner_h", None)
    if content_h is None:
        content_h = getattr(flowable, "height", 0) or 0
    inner = getattr(flowable, "inner", flowable)
    detail = describe(inner, frame_x, frame_y)
    return {
        "id": sid,
        "kind": getattr(flowable, "spark_kind", ""),
        "part": getattr(flowable, "spark_part", 0),
        "anchor": getattr(flowable, "spark_anchor", "") or "",
        "page": page,
        "x": round(frame_x, 2),
        "y": round(frame_y, 2),
        "w": round(getattr(flowable, "width", 0) or 0, 2),
        "h": round(content_h, 2),
        "y_top": round(page_h - (frame_y + content_h), 2),
        **detail,
    }


def describe(flow, x: float, y: float) -> dict:
    lines: list[dict] = []
    cells: list[dict] = []
    nodes: list[dict] = []
    cards: list[dict] = []
    _walk(flow, x, y, lines, cells, nodes, cards)
    return {"lines": lines, "cells": cells, "nodes": nodes, "cards": cards}


def write_layout(path: Path, blocks: list[dict], pages: int) -> None:
    path.write_text(json.dumps({"pages": pages, "blocks": blocks}, indent=2), encoding="utf-8")


def _walk(flow, x, y, lines, cells, nodes, cards) -> None:
    if flow is None:
        return
    if hasattr(flow, "blPara") and hasattr(flow, "style"):
        _paragraph(flow, x, y, lines)
        return
    if hasattr(flow, "_rowHeights") and hasattr(flow, "_colWidths"):
        _table(flow, x, y, cells)
        return
    if hasattr(flow, "scene") and hasattr(flow, "s"):
        _diagram(flow, x, y, nodes)
        return
    if hasattr(flow, "cells") and hasattr(flow, "ncols") and hasattr(flow, "row_heights"):
        _grid(flow, x, y, lines, cards)
        return
    if hasattr(flow, "stacks") and hasattr(flow, "col_w"):
        for i, stack in enumerate(flow.stacks):
            _stack(stack, x + flow.inset + i * (flow.col_w + flow.gutter), y, lines)
        return
    if hasattr(flow, "_placed") and hasattr(flow, "items"):
        _stack(flow, x, y, lines)
        return
    if hasattr(flow, "para") and hasattr(flow, "rule_to_base"):
        _paragraph(flow.para, x, y, lines)


def _paragraph(para, x: float, y: float, lines: list[dict]) -> None:
    bl = getattr(para, "blPara", None)
    if not bl or not getattr(bl, "lines", None):
        return
    style = para.style
    size = style.fontSize
    leading = style.leading or size
    height = getattr(para, "height", 0) or 0
    if rl_config.paraFontSizeHeightOffset:
        first = height - size
    else:
        first = height - getattr(bl, "ascent", size)
    left = x + (style.leftIndent or 0)
    for i, line in enumerate(bl.lines):
        text = _line_text(line, bl.kind)
        if not text:
            continue
        baseline = y + first - i * leading
        lines.append({"text": text, "x": round(left, 2), "baseline": round(baseline, 2)})


def _line_text(line, kind: int) -> str:
    if kind == 0:
        words = line[1]
        return " ".join(w if isinstance(w, str) else getattr(w, "text", "") for w in words).strip()
    return "".join(getattr(w, "text", "") for w in getattr(line, "words", [])).strip()


def _table(table, x: float, y: float, cells: list[dict]) -> None:
    rows = list(getattr(table, "_rowHeights", None) or [])
    cols = list(getattr(table, "_colWidths", None) or [])
    yy = y + sum(rows)
    for r, rh in enumerate(rows):
        yy -= rh
        xx = x
        for c, cw in enumerate(cols):
            cells.append({"r": r, "c": c, "x": round(xx, 2), "y": round(yy, 2), "w": round(cw, 2), "h": round(rh, 2)})
            xx += cw


def _grid(grid, x, y, lines, cards) -> None:
    yy = grid.height
    rows = grid.rows()
    heights = grid.row_heights()
    for row, h in zip(rows, heights):
        yy -= h
        for i, cell in enumerate(row):
            cx = x + i * (grid.cell_w + grid.gutter)
            cy = y + yy
            cards.append({"x": round(cx, 2), "y": round(cy, 2), "w": round(grid.cell_w, 2), "h": round(h, 2)})
            _stack(cell, cx, cy, lines)
        yy -= grid.row_gap


def _stack(stack, x: float, y: float, lines: list[dict]) -> None:
    placed = getattr(stack, "_placed", None)
    if not placed:
        return
    for para, top, h in placed:
        _paragraph(para, x + stack.x + stack.pad_x, y + stack.height - top - h, lines)


def _diagram(flow, x: float, y: float, nodes: list[dict]) -> None:
    x0 = x + (flow.width - flow.w) / 2
    s = flow.s
    for n in flow.scene.nodes:
        nodes.append(
            {
                "id": n.id,
                "text": " ".join(ln.text for ln in n.lines),
                "x": round(x0 + n.x * s, 2),
                "y": round(y + flow.h - (n.y + n.h) * s, 2),
                "w": round(n.w * s, 2),
                "h": round(n.h * s, 2),
            }
        )
