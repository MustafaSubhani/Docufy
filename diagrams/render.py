"""Render a Scene: ReportLab vector flowable (PDF), PNG (DOCX / previews), and SVG (editing / markdown)."""

from __future__ import annotations

import hashlib
import io
import json
import math
from html import escape
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable

from . import style as S
from .scene import Scene, build_scene, text_w
from .spec import Diagram

FONT = "SparkDiagram"
FONT_B = "SparkDiagram-Bold"
_registered = False
PNG_CACHE = S.ROOT / "assets" / "diagrams" / "claude"


def register() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(FONT, str(S.FONT_REGULAR)))
    pdfmetrics.registerFont(TTFont(FONT_B, str(S.FONT_BOLD)))
    _registered = True


def draw_scene(c, scene: Scene, s: float, height_pt: float) -> None:
    """Draw scene onto a ReportLab canvas; (0,0) is the bottom-left of the drawing area."""
    register()

    def X(x: float) -> float:
        return x * s

    def Y(y: float) -> float:
        return height_pt - y * s

    c.saveState()
    c.setLineJoin(1)
    c.setLineCap(1)
    # groups
    for g in scene.groups:
        r = S.ramp(g.ramp or "gray")
        c.setStrokeColor(HexColor(r[S.STOP["200"]] if g.ramp else S.ramp("gray")[S.STOP["200"]]))
        c.setLineWidth(0.75 * s)
        c.setDash(4 * s, 3 * s)
        if g.ramp:
            c.setFillColor(HexColor(r[S.STOP["50"]]))
        c.roundRect(X(g.x), Y(g.y + g.h), g.w * s, g.h * s, 12 * s, stroke=1, fill=1 if g.ramp else 0)
        c.setDash()
        if g.label:
            c.setFont(FONT, S.LABEL_SIZE * s)
            c.setFillColor(HexColor(S.TEXT_MUTED))
            c.drawString(X(g.x + 12), Y(g.y + 17), g.label)
            if g.sublabel:
                c.setFont(FONT, (S.LABEL_SIZE - 1) * s)
                c.drawString(X(g.x + 12), Y(g.y + 31), g.sublabel)
    # edges
    for e in scene.edges:
        c.setStrokeColor(HexColor(e.color))
        c.setLineWidth(S.EDGE_W * s)
        if e.dashed:
            c.setDash(5 * s, 4 * s)
        p = c.beginPath()
        (x0, y0), rest = e.points[0], e.points[1:]
        p.moveTo(X(x0), Y(y0))
        for x, y in rest:
            p.lineTo(X(x), Y(y))
        c.drawPath(p, stroke=1, fill=0)
        c.setDash()
        (ax, ay), (bx, by) = e.points[-2], e.points[-1]
        ang = math.atan2(by - ay, bx - ax)
        L, W = S.ARROW, S.ARROW * 0.62
        lx = bx - L * math.cos(ang) + W * math.sin(ang)
        ly = by - L * math.sin(ang) - W * math.cos(ang)
        rx = bx - L * math.cos(ang) - W * math.sin(ang)
        ry = by - L * math.sin(ang) + W * math.cos(ang)
        ah = c.beginPath()
        ah.moveTo(X(lx), Y(ly))
        ah.lineTo(X(bx), Y(by))
        ah.lineTo(X(rx), Y(ry))
        c.drawPath(ah, stroke=1, fill=0)
        if e.label and e.label_xy:
            lw = text_w(e.label, S.LABEL_SIZE) + 8
            cx, cy = e.label_xy
            c.setFillColor(HexColor("#FFFFFF"))
            c.rect(X(cx - lw / 2), Y(cy + 8), lw * s, 16 * s, stroke=0, fill=1)
            c.setFillColor(HexColor(S.TEXT_MUTED))
            c.setFont(FONT, S.LABEL_SIZE * s)
            c.drawCentredString(X(cx), Y(cy + 4.2), e.label)
    # nodes
    for n in scene.nodes:
        r = S.ramp(n.ramp)
        c.setFillColor(HexColor(r[S.STOP["50"]]))
        c.setStrokeColor(HexColor(r[S.STOP["400"]]))
        c.setLineWidth(0.75 * s)
        if n.shape == "diamond":
            cx, cy = n.x + n.w / 2, n.y + n.h / 2
            p = c.beginPath()
            p.moveTo(X(cx), Y(n.y))
            p.lineTo(X(n.x + n.w), Y(cy))
            p.lineTo(X(cx), Y(n.y + n.h))
            p.lineTo(X(n.x), Y(cy))
            p.close()
            c.drawPath(p, stroke=1, fill=1)
        else:
            radius = n.h / 2 if n.shape == "pill" else S.RADIUS
            c.roundRect(X(n.x), Y(n.y + n.h), n.w * s, n.h * s, radius * s, stroke=1, fill=1)
        heights = [18 if ln.size == S.TITLE_SIZE else 16 for ln in n.lines]
        top = n.y + (n.h - sum(heights)) / 2
        for ln, lh in zip(n.lines, heights):
            base = top + (lh + ln.size * 0.72) / 2
            c.setFillColor(HexColor(ln.color))
            c.setFont(FONT_B if ln.bold else FONT, ln.size * s)
            c.drawCentredString(X(n.x + n.w / 2), Y(base), ln.text)
            top += lh
    # legend
    if scene.legend:
        x = scene.legend_x
        for ramp, label in scene.legend:
            r = S.ramp(ramp)
            c.setFillColor(HexColor(r[S.STOP["50"]]))
            c.setStrokeColor(HexColor(r[S.STOP["400"]]))
            c.setLineWidth(0.75 * s)
            c.roundRect(X(x), Y(scene.legend_y + S.LEGEND_SWATCH), S.LEGEND_SWATCH * s, S.LEGEND_SWATCH * s, 3 * s, stroke=1, fill=1)
            c.setFillColor(HexColor(S.TEXT_MUTED))
            c.setFont(FONT, S.LABEL_SIZE * s)
            c.drawString(X(x + S.LEGEND_SWATCH + 6), Y(scene.legend_y + 10), label)
            x += S.LEGEND_SWATCH + 6 + text_w(label, S.LABEL_SIZE) + 22
    c.restoreState()


class DiagramFlowable(Flowable):
    def __init__(self, scene: Scene, width_pt: float, max_height: float | None = None):
        super().__init__()
        self.scene = scene
        s = width_pt / scene.width
        if max_height and scene.height * s > max_height:
            s = max_height / scene.height
        self.s = s
        self.w = scene.width * s
        self.h = scene.height * s

    def wrap(self, aw, ah):
        self.width, self.height = aw, self.h
        return aw, self.h

    def draw(self):
        x = (self.width - self.w) / 2
        self.canv.saveState()
        self.canv.translate(x, 0)
        draw_scene(self.canv, self.scene, self.s, self.h)
        self.canv.restoreState()


def spec_key(d: Diagram) -> str:
    return hashlib.sha1(json.dumps(d.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


def render_png(d: Diagram, zoom: float = 3.0) -> Path:
    """High-resolution PNG (for DOCX and previews), cached by spec hash."""
    import pymupdf
    from reportlab.pdfgen.canvas import Canvas

    out = PNG_CACHE / f"{spec_key(d)}_{int(zoom * 100)}.png"
    if out.is_file():
        return out
    scene = build_scene(d)
    buf = io.BytesIO()
    cv = Canvas(buf, pagesize=(scene.width, scene.height))
    draw_scene(cv, scene, 1.0, scene.height)
    cv.showPage()
    cv.save()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out))
    doc.close()
    return out


def render_svg(d: Diagram) -> str:
    scene = build_scene(d)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {scene.width:.0f} {scene.height:.0f}" width="100%" font-family="DejaVu Sans, sans-serif">']
    for g in scene.groups:
        parts.append(f'<rect x="{g.x:.1f}" y="{g.y:.1f}" width="{g.w:.1f}" height="{g.h:.1f}" rx="12" fill="{S.ramp(g.ramp)[0] if g.ramp else "none"}" stroke="{S.ramp(g.ramp or "gray")[2]}" stroke-dasharray="4 3" stroke-width="0.75"/>')
        if g.label:
            parts.append(f'<text x="{g.x + 12:.1f}" y="{g.y + 17:.1f}" font-size="12" fill="{S.TEXT_MUTED}">{escape(g.label)}</text>')
    for e in scene.edges:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in e.points)
        dash = ' stroke-dasharray="5 4"' if e.dashed else ""
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{e.color}" stroke-width="{S.EDGE_W}" stroke-linejoin="round" stroke-linecap="round"{dash} marker-end="url(#a{e.color[1:]})"/>')
    colors = {e.color for e in scene.edges}
    defs = "".join(
        f'<marker id="a{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse"><path d="M1 1L9 5L1 9" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>'
        for c in colors
    )
    parts.insert(1, f"<defs>{defs}</defs>")
    for n in scene.nodes:
        r = S.ramp(n.ramp)
        rx = n.h / 2 if n.shape == "pill" else S.RADIUS
        parts.append(f'<rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" rx="{rx}" fill="{r[0]}" stroke="{r[3]}" stroke-width="0.75"/>')
        heights = [18 if ln.size == S.TITLE_SIZE else 16 for ln in n.lines]
        top = n.y + (n.h - sum(heights)) / 2
        for ln, lh in zip(n.lines, heights):
            base = top + (lh + ln.size * 0.72) / 2
            parts.append(f'<text x="{n.x + n.w / 2:.1f}" y="{base:.1f}" font-size="{ln.size}" fill="{ln.color}" text-anchor="middle">{escape(ln.text)}</text>')
            top += lh
    x = S.MARGIN
    for ramp, label in scene.legend:
        r = S.ramp(ramp)
        parts.append(f'<rect x="{x:.1f}" y="{scene.legend_y:.1f}" width="12" height="12" rx="3" fill="{r[0]}" stroke="{r[3]}" stroke-width="0.75"/>')
        parts.append(f'<text x="{x + 18:.1f}" y="{scene.legend_y + 10:.1f}" font-size="12" fill="{S.TEXT_MUTED}">{escape(label)}</text>')
        x += 18 + text_w(label, S.LABEL_SIZE) + 22
    parts.append("</svg>")
    return "\n".join(parts)
