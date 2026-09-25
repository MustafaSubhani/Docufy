"""Two-column Stage | Capabilities workflow diagrams (ADICC-style)."""

from __future__ import annotations

import re

from reportlab.lib.colors import HexColor
from reportlab.platypus import Flowable, Paragraph

from .parser import inline_xml
from .theme import CONTENT_W, FONT, FONT_BOLD, MUTED, SUBTLE

LAYER_RE = re.compile(r"\s*\{+\s*\.?layer=(bot|ai|output|gov|process)\s*\}+\s*$", re.I)

LAYER_FILL = {
    "bot": HexColor("#D6E8FF"),
    "ai": HexColor("#E8E4FF"),
    "output": HexColor("#DFF0E8"),
    "gov": HexColor("#F0E8DC"),
    "process": HexColor("#E8E4FF"),
}
LAYER_STROKE = {
    "bot": HexColor("#4A90E2"),
    "ai": HexColor("#6B4EFF"),
    "output": HexColor("#3D9970"),
    "gov": HexColor("#B8956A"),
    "process": HexColor("#6B4EFF"),
}
LEGEND = [
    ("bot", "Data ingest"),
    ("ai", "Processing"),
    ("output", "Output"),
    ("gov", "Governance"),
]


def strip_layer(text: str) -> tuple[str, str]:
    m = LAYER_RE.search(text)
    layer = m.group(1).lower() if m else "process"
    if m:
        text = LAYER_RE.sub("", text).strip()
    return text, layer


def parse_stageflow_steps(blocks: list[dict]) -> list[dict]:
    from .parser import split_by_heading

    steps: list[dict] = []
    for group in split_by_heading(blocks, 3):
        title, layer = strip_layer(group.get("title") or "")
        detail = ""
        cap_title = ""
        cap_detail = ""
        for child in group.get("blocks") or []:
            kind = child.get("kind")
            if kind == "para" and not detail:
                detail = child.get("text") or ""
            elif kind == "callout":
                cap_title = child.get("title") or ""
                parts = [
                    b.get("text") or ""
                    for b in child.get("blocks") or []
                    if b.get("kind") == "para"
                ]
                cap_detail = " ".join(p for p in parts if p).strip()
                if not cap_title and cap_detail:
                    cap_title = cap_detail
                    cap_detail = ""
            elif kind == "para":
                detail = f"{detail} {child.get('text') or ''}".strip()
        steps.append(
            {
                "title": title,
                "layer": layer,
                "detail": detail,
                "cap_title": cap_title,
                "cap_detail": cap_detail,
            }
        )
    return steps


class StageFlowDiagram(Flowable):
    """Stage column (left) with capability boxes aligned per row (right)."""

    def __init__(self, steps: list[dict], styles):
        super().__init__()
        self.steps = steps
        self.styles = styles
        self._w = CONTENT_W
        self._h = 0
        self._rows: list[dict] = []

    def wrap(self, aw, ah):
        self._w = aw
        stage_w = aw * 0.42
        cap_w = aw * 0.52
        cap_x = aw - cap_w
        pad = 8
        arrow_h = 10
        row_gap = 5

        title_style = self.styles["sf_title"]
        detail_style = self.styles["sf_detail"]
        cap_title_style = self.styles["sf_cap_title"]
        cap_detail_style = self.styles["sf_cap_detail"]

        self._rows = []
        for step in self.steps:
            stage_title = Paragraph(inline_xml(step.get("title") or ""), title_style)
            stage_detail = Paragraph(inline_xml(step.get("detail") or " "), detail_style)
            cap_title = Paragraph(inline_xml(step.get("cap_title") or " "), cap_title_style)
            cap_detail = Paragraph(inline_xml(step.get("cap_detail") or " "), cap_detail_style)

            inner_w = max(stage_w - 2 * pad, 20)
            cap_inner_w = max(cap_w - 2 * pad, 20)
            _, st_h = stage_title.wrap(inner_w, ah)
            _, sd_h = stage_detail.wrap(inner_w, ah)
            stage_h = st_h + sd_h + 2 * pad + 2

            _, ct_h = cap_title.wrap(cap_inner_w, ah)
            _, cd_h = cap_detail.wrap(cap_inner_w, ah)
            cap_h = ct_h + cd_h + 2 * pad + 2

            row_h = max(stage_h, cap_h, 34)
            self._rows.append(
                {
                    "step": step,
                    "stage_title": stage_title,
                    "stage_detail": stage_detail,
                    "cap_title": cap_title,
                    "cap_detail": cap_detail,
                    "stage_h": stage_h,
                    "cap_h": cap_h,
                    "row_h": row_h,
                    "stage_w": stage_w,
                    "cap_w": cap_w,
                    "cap_x": cap_x,
                    "pad": pad,
                }
            )

        header_h = 18
        input_h = 14
        legend_h = 26
        body_h = sum(r["row_h"] for r in self._rows)
        arrows = arrow_h * max(len(self._rows) - 1, 0)
        gaps = row_gap * max(len(self._rows) - 1, 0)
        self._h = header_h + input_h + body_h + arrows + gaps + legend_h + 12
        return aw, self._h

    def draw(self):
        c = self.canv
        aw = self._w
        y = self._h

        # Column headers
        y -= 14
        c.setFont(FONT_BOLD, 8.5)
        c.setFillColor(SUBTLE)
        c.drawString(0, y, "Stage")
        c.drawString(aw * 0.46, y, "Capabilities")
        y -= 8

        # Input label
        c.setFont(FONT_BOLD, 7.5)
        c.setFillColor(SUBTLE)
        c.drawString(0, y, "Input")
        y -= 14

        arrow_h = 10
        row_gap = 5
        stage_cx = aw * 0.21

        for idx, row in enumerate(self._rows):
            step = row["step"]
            layer = step.get("layer") or "process"
            fill = LAYER_FILL.get(layer, LAYER_FILL["process"])
            stroke = LAYER_STROKE.get(layer, LAYER_STROKE["process"])

            y -= row["row_h"]
            stage_y = y + (row["row_h"] - row["stage_h"]) / 2
            cap_y = y + (row["row_h"] - row["cap_h"]) / 2

            # Stage box
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(0.6)
            c.roundRect(0, stage_y, row["stage_w"], row["stage_h"], 3, fill=1, stroke=1)

            pad = row["pad"]
            inner_w = row["stage_w"] - 2 * pad
            ty = stage_y + row["stage_h"] - pad
            _, th = row["stage_title"].wrap(inner_w, row["stage_h"])
            ty -= th
            row["stage_title"].drawOn(c, pad, ty)
            _, dh = row["stage_detail"].wrap(inner_w, row["stage_h"])
            ty -= dh
            row["stage_detail"].drawOn(c, pad, ty)

            # Capability box
            c.setFillColor(HexColor("#F4F6F9"))
            c.setStrokeColor(HexColor("#C8C8C8"))
            c.roundRect(row["cap_x"], cap_y, row["cap_w"], row["cap_h"], 3, fill=1, stroke=1)

            cap_inner_w = row["cap_w"] - 2 * pad
            ty = cap_y + row["cap_h"] - pad
            _, th = row["cap_title"].wrap(cap_inner_w, row["cap_h"])
            ty -= th
            row["cap_title"].drawOn(c, row["cap_x"] + pad, ty)
            _, dh = row["cap_detail"].wrap(cap_inner_w, row["cap_h"])
            ty -= dh
            row["cap_detail"].drawOn(c, row["cap_x"] + pad, ty)

            # Arrow between stages
            if idx < len(self._rows) - 1:
                ay = y - row_gap / 2 - arrow_h / 2
                c.setStrokeColor(SUBTLE)
                c.setLineWidth(0.8)
                c.line(stage_cx, y - row_gap / 2, stage_cx, y - row_gap / 2 - arrow_h)
                c.setFillColor(SUBTLE)
                path = c.beginPath()
                path.moveTo(stage_cx - 3, ay)
                path.lineTo(stage_cx + 3, ay)
                path.lineTo(stage_cx, ay - 4)
                path.close()
                c.drawPath(path, fill=1, stroke=0)
                y -= row_gap + arrow_h

        c.setFont(FONT_BOLD, 7.5)
        c.setFillColor(SUBTLE)
        c.drawString(0, max(y - 2, 30), "Output")

        # Legend
        ly = 6
        lx = 0
        c.setFont(FONT, 7)
        for key, label in LEGEND:
            c.setFillColor(LAYER_FILL[key])
            c.setStrokeColor(LAYER_STROKE[key])
            c.setLineWidth(0.5)
            c.roundRect(lx, ly, 10, 10, 1.5, fill=1, stroke=1)
            c.setFillColor(MUTED)
            c.drawString(lx + 14, ly + 2, label)
            lx += 88
