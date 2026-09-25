"""Render a Spark-branded PDF from the parsed document model.

Layout is token-driven (themes/spark/tokens.yaml). Every block becomes a *unit* with a kind and
reference lines (first/last baseline for text, edges for boxes). The gap before a unit is the
measured reference-line distance for the (previous kind, next kind) pair, so vertical rhythm
matches the ADICC reference instead of relying on generic spaceBefore/spaceAfter values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

from reportlab import rl_config
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Table,
    TableStyle,
)
from reportlab.platypus import Image as RLImage

from . import tokens as tokmod
from .fonts import register_fonts
from .images import raster_path_for_pdf
from .layout import fit_image
from .parser import format_kicker, inline_xml
from .stageflow import StageFlowDiagram
from .theme import ASSETS, Meta

T = tokmod.load("spark")
ParagraphStyle.defaults["spaceShrinkage"] = 0  # Google Docs never squeezes spaces to fit a word
rl_config.useA85 = 0  # binary image streams: ASCII85 in pure Python dominated render time
PAGE_W = T.page.width
PAGE_H = T.page.height
LEFT = T.page.margin_left
TEXT_W = T.page.text_width
FRAME_TOP = T.page.frame_top
FRAME_H = PAGE_H - FRAME_TOP - T.page.bottom
ASC = T.page.gdocs_ascent

FONT = T.fonts.regular
BOLD = T.fonts.bold
ITALIC = T.fonts.italic
MONO = T.fonts.mono

LOGO = ASSETS / "spark-logo-source.png"
if not LOGO.exists():
    LOGO = ASSETS / "spark-logo.png"


def C(name: str) -> HexColor:
    return HexColor(tokmod.color(name))


def text_top_adjust(size: float) -> float:
    """Extra space at a frame top so the first baseline lands where Google Docs puts it."""
    return (T.page.top + ASC * size) - FRAME_TOP - size


BOX_TOP_ADJUST = T.page.top - FRAME_TOP


# ---------------------------------------------------------------------------
# paragraph styles


def pstyle(name: str, spec: str, *, font: str = FONT, align=TA_LEFT, color: str | None = None, **kw) -> ParagraphStyle:
    t = T.type[spec]
    return ParagraphStyle(
        name,
        fontName=font,
        fontSize=t.size,
        leading=t.get("leading", t.size * 1.15),
        textColor=C(color or t.color),
        alignment=align,
        spaceBefore=0,
        spaceAfter=0,
        allowWidows=1,
        allowOrphans=1,
        spaceShrinkage=0,
        **kw,
    )


FULL_RIGHT = TEXT_W - T.page.break_width  # right indent that gives the measured break width


def make_styles() -> dict[str, ParagraphStyle]:
    s = {
        "body": pstyle("SparkBody", "body", align=TA_JUSTIFY, rightIndent=FULL_RIGHT),
        "body_left": pstyle("SparkBodyLeft", "body"),
        "label": pstyle("SparkLabel", "label", font=BOLD, rightIndent=FULL_RIGHT),
        "page_title": pstyle("SparkPageTitle", "page_title", font=BOLD, rightIndent=FULL_RIGHT),
        "sub_title": pstyle("SparkSubTitle", "sub_title", font=BOLD, rightIndent=FULL_RIGHT),
        "large_title": pstyle("SparkLargeTitle", "large_title", font=BOLD, rightIndent=FULL_RIGHT),
        "h3": pstyle("SparkH3", "h3", font=BOLD, rightIndent=FULL_RIGHT),
        "h3_accent": pstyle("SparkH3Accent", "h3", font=BOLD, color="purple", rightIndent=FULL_RIGHT),
        "h4": pstyle("SparkH4", "h4", font=BOLD, rightIndent=FULL_RIGHT),
        "box_title": pstyle("SparkBoxTitle", "box_title", font=BOLD),
        "box_title_accent": pstyle("SparkBoxTitleAccent", "box_title", font=BOLD, color="purple"),
        "card_title": pstyle("SparkCardTitle", "card_title", font=BOLD),
        "card_body": pstyle("SparkCardBody", "card_body"),
        "th": pstyle("SparkTH", "table_head", font=BOLD),
        "td": pstyle("SparkTD", "table_body"),
        "td_bold": pstyle("SparkTDBold", "table_body", font=BOLD),
        "code": ParagraphStyle("SparkCode", fontName=MONO, fontSize=8, leading=11, textColor=C("navy")),
    }
    # legacy keys used by stageflow
    s.update(
        sf_title=ParagraphStyle("SparkSFTitle", fontName=BOLD, fontSize=9.5, leading=12, textColor=C("navy")),
        sf_detail=ParagraphStyle("SparkSFDetail", fontName=FONT, fontSize=8.5, leading=11, textColor=C("subtitle")),
        sf_cap_title=ParagraphStyle("SparkSFCapTitle", fontName=BOLD, fontSize=9, leading=12, textColor=C("navy")),
        sf_cap_detail=ParagraphStyle("SparkSFCapDetail", fontName=FONT, fontSize=8.5, leading=11, textColor=C("subtitle")),
    )
    return s


# ---------------------------------------------------------------------------
# document template


class SparkDoc(BaseDocTemplate):
    def __init__(self, filename: str, meta: Meta, **kwargs):
        super().__init__(
            filename,
            pagesize=(PAGE_W, PAGE_H),
            leftMargin=LEFT,
            rightMargin=PAGE_W - LEFT - TEXT_W,
            topMargin=FRAME_TOP,
            bottomMargin=T.page.bottom,
            title=meta.title or "Spark Document",
            author=meta.prepared_by,
            **kwargs,
        )
        self.meta = meta
        frame = dict(leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, showBoundary=0)
        cover = PageTemplate(id="cover", frames=[Frame(LEFT, T.page.bottom, TEXT_W, FRAME_H, id="c", **frame)], onPage=self._draw_cover)
        inner = PageTemplate(id="inner", frames=[Frame(LEFT, T.page.bottom, TEXT_W, FRAME_H, id="i", **frame)])
        self.addPageTemplates([cover, inner] if meta.has_cover() else [inner, cover])

    def _draw_cover(self, canvas, doc):
        draw_cover_page(canvas, self.meta)


def td(y: float) -> float:
    """Top-down y (as measured) -> ReportLab canvas y."""
    return PAGE_H - y


def wrap_words(canvas, text: str, font: str, size: float, width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if canvas.stringWidth(trial, font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def draw_title_block(canvas, cfg, base: float, kicker: str, title: str, subtitle: str, rule_color: str) -> float:
    """Kicker, title, subtitle, rule. Returns the rule y (top-down)."""
    cv = T.cover
    canvas.setFont(BOLD, cv.kicker_size)
    canvas.setFillColor(C("purple"))
    canvas.drawString(LEFT, td(base), kicker.upper())
    y = base + cfg.kicker_to_title
    canvas.setFont(BOLD, cv.title_size)
    canvas.setFillColor(C("navy"))
    lines = wrap_words(canvas, title, BOLD, cv.title_size, T.page.rule_right - LEFT)
    for i, ln in enumerate(lines):
        canvas.drawString(LEFT, td(y + i * cv.title_leading), ln)
    y += (len(lines) - 1) * cv.title_leading
    if subtitle:
        y += cfg.title_to_subtitle
        canvas.setFont(FONT, cv.subtitle_size)
        canvas.setFillColor(C("subtitle"))
        sub = wrap_words(canvas, subtitle, FONT, cv.subtitle_size, T.page.rule_right - LEFT)
        for i, ln in enumerate(sub):
            canvas.drawString(LEFT, td(y + i * cv.subtitle_leading), ln)
        y += (len(sub) - 1) * cv.subtitle_leading
    rule_y = y + cfg.subtitle_to_rule
    canvas.setStrokeColor(C(rule_color))
    canvas.setLineWidth(1)
    canvas.line(LEFT, td(rule_y), T.page.rule_right, td(rule_y))
    return rule_y


def draw_meta_lines(canvas, first_base: float, rows: list[tuple[str, str]]) -> float:
    y = first_base
    last = first_base
    for label, value in rows:
        if not value:
            continue
        prefix = f"{label}:  "
        canvas.setFont(BOLD, 10)
        canvas.setFillColor(C("navy"))
        canvas.drawString(LEFT, td(y), prefix)
        canvas.setFont(FONT, 10)
        canvas.setFillColor(C("body"))
        canvas.drawString(LEFT + canvas.stringWidth(prefix, BOLD, 10), td(y), value)
        last = y
        y += T.cover.meta_pitch
    return last


def draw_note_box(canvas, top: float, text: str, cfg, fill: str, color: str) -> None:
    cv = T.cover
    width = T.page.box_width
    inner_w = width - 2 * cv.note_inset
    lines = wrap_words(canvas, text, ITALIC, cv.note_size, inner_w)
    first = top + cfg.box_to_text
    last = first + (len(lines) - 1) * cv.note_leading
    bottom = last + cfg.text_to_box_bottom
    canvas.setFillColor(C(fill))
    canvas.setStrokeColor(C("box_border"))
    canvas.setLineWidth(1)
    canvas.rect(LEFT, td(bottom), width + 0.5, bottom - top, fill=1, stroke=1)
    canvas.setFont(ITALIC, cv.note_size)
    canvas.setFillColor(C(color))
    for i, ln in enumerate(lines):
        canvas.drawString(LEFT + cv.note_inset, td(first + i * cv.note_leading), ln)


def draw_cover_page(canvas, meta: Meta) -> None:
    cv = T.cover
    canvas.saveState()
    if LOGO.exists():
        lg = cv.logo
        canvas.drawImage(str(LOGO), lg.x, td(lg.y + lg.h), width=lg.w, height=lg.h, mask="auto")
    rule_y = draw_title_block(canvas, cv, cv.kicker_base, meta.doc_type or "", meta.title, meta.subtitle, "purple_rule")
    last = draw_meta_lines(
        canvas,
        rule_y + cv.rule_to_meta,
        [("Prepared for", meta.prepared_for), ("Prepared by", meta.prepared_by), ("Date", meta.date)],
    )
    if meta.confidential:
        draw_note_box(canvas, last + cv.meta_to_box, meta.confidential_text, cv, "cover_box", "subtle")
    canvas.restoreState()


class AddendumCover(Flowable):
    """Full-page addendum cover drawn at measured absolute positions."""

    def __init__(self, fields: dict[str, str], meta: Meta):
        super().__init__()
        self.fields = fields
        self.meta = meta

    def wrap(self, aw, ah):
        self.width, self.height = aw, ah
        return aw, ah

    def draw(self):
        c = self.canv
        f = self.fields
        ac = T.addendum_cover
        c.saveState()
        # flowable origin is the frame's bottom-left; translate to page coordinates
        c.translate(-LEFT, -T.page.bottom)
        rule_y = draw_title_block(
            c, ac, ac.kicker_base, f.get("label", "ADDENDUM"), f.get("title", self.meta.title), f.get("subtitle", ""), "rule"
        )
        last = draw_meta_lines(
            c,
            rule_y + ac.rule_to_meta,
            [
                ("Prepared for", f.get("prepared_for", self.meta.prepared_for)),
                ("Prepared by", f.get("prepared_by", self.meta.prepared_by)),
                ("Date", f.get("date", self.meta.date)),
            ],
        )
        if f.get("note"):
            draw_note_box(c, last + ac.meta_to_box, f["note"], ac, "white", "note")
        c.restoreState()


# ---------------------------------------------------------------------------
# spacing machinery


class Spaced(Flowable):
    """Wraps a flowable with a gap above it; the gap becomes `top_adjust` at a frame top."""

    def __init__(self, inner: Flowable, gap: float, top_adjust: float, keep_with_next: bool = False, keep_together: bool = False):
        super().__init__()
        self.inner = inner
        self.gap = max(gap, 0.0)
        self.top_adjust = top_adjust
        self.keepWithNext = keep_with_next
        self.keep_together = keep_together
        self._g = self.gap

    def _gap_for(self, ah: float) -> float:
        # Exactly a full frame means "at the top of a page". KeepTogether measures children with a
        # huge height, which must count as mid-page so group heights are not underestimated.
        return self.top_adjust if abs(ah - FRAME_H) < 0.01 else self.gap

    def wrap(self, aw, ah):
        self._g = self._gap_for(ah)
        w, h = self.inner.wrap(aw, max(ah - self._g, 0))
        self.width, self.height = w, h + self._g
        self._inner_h = h
        return w, self.height

    def split(self, aw, ah):
        g = self._gap_for(ah)
        if ah - g <= 1:
            return []
        if self.keep_together and ah < FRAME_H - 0.01:
            _, full_h = self.inner.wrap(aw, FRAME_H)
            if full_h <= FRAME_H - self.top_adjust:
                return []  # fits on a fresh page: move it whole rather than split it
        parts = self.inner.split(aw, ah - g)
        if not parts:
            return []
        out: list[Flowable] = [Spaced(parts[0], g, g)]
        for p in parts[1:]:
            out.append(Spaced(p, self.top_adjust, self.top_adjust))
        return out

    def getKeepWithNext(self):
        return self.keepWithNext

    def draw(self):
        self.inner.drawOn(self.canv, 0, 0)


@dataclass
class Unit:
    kind: str
    flowable: Flowable
    top_ref: float = 0.0  # flowable top -> first reference line
    bottom_ref: float = 0.0  # last reference line -> flowable bottom
    top_adjust: float = BOX_TOP_ADJUST
    keep: bool = False


def text_unit(kind: str, flowable: Flowable, style: ParagraphStyle, keep: bool = False) -> Unit:
    return Unit(kind, flowable, style.fontSize, style.leading - style.fontSize, text_top_adjust(style.fontSize), keep)


def spacing(prev: str | None, nxt: str) -> float:
    if prev is None:
        return 0.0
    table = T.spacing
    for a in (prev, family(prev)):
        row = table.get(a)
        if isinstance(row, dict):
            for b in (nxt, family(nxt)):
                if b in row:
                    return float(row[b])
    return float(table.default)


FAMILIES = {
    "bullets_large": "bullets",
    "compact_title": "sub_title",
    "module_title": "sub_title",
    "kicker": "kicker",
    "roadmap": "table",
    "commercial": "table",
    "kpis": "table",
    "code": "table",
    "stageflow": "figure",
}


def family(kind: str) -> str:
    return FAMILIES.get(kind, kind)


KEEP_TOGETHER = {"table", "callout", "cards", "steps", "columns", "figure", "contact"}


# ---------------------------------------------------------------------------
# flowables


class Kicker(Flowable):
    def __init__(self, text: str):
        super().__init__()
        self.text = text
        self.size = T.type.kicker.size
        self.rule_gap = T.kicker.rule_y - T.kicker.base

    def wrap(self, aw, ah):
        self.width = aw
        self.height = self.size + self.rule_gap
        return aw, self.height

    def draw(self):
        c = self.canv
        c.setFont(BOLD, self.size)
        c.setFillColor(C("purple"))
        c.drawString(0, self.height - self.size, self.text)
        c.setStrokeColor(C("rule"))
        c.setLineWidth(T.kicker.rule_width)
        c.line(0, 0, T.page.rule_right - LEFT, 0)


class RuledTitle(Flowable):
    """Grey rule with a title under it (continuation pages such as KPIs)."""

    def __init__(self, text: str, style: ParagraphStyle, rule_to_base: float, rule_color: str):
        super().__init__()
        self.para = Paragraph(inline_xml(text), style)
        self.style = style
        self.rule_to_base = rule_to_base
        self.rule_color = rule_color

    def wrap(self, aw, ah):
        _, ph = self.para.wrap(aw, ah)
        self._ph = ph
        self.width = aw
        self.height = self.rule_to_base - self.style.fontSize + ph
        return aw, self.height

    def draw(self):
        c = self.canv
        c.setStrokeColor(C(self.rule_color))
        c.setLineWidth(1)
        c.line(0, self.height, T.page.rule_right - LEFT, self.height)
        self.para.drawOn(c, 0, 0)


class Stack(Flowable):
    """Vertical stack placed by baselines, optionally on a filled background.

    items: (paragraph, delta) where delta = distance from the previous reference line (box top or
    the previous item's last baseline) to this item's first baseline.
    """

    def __init__(self, items, width: float, pad_x: float, bottom: float, fill: str | None = None, inner_w: float | None = None, x: float = 0.0):
        super().__init__()
        self.items = items
        self.box_w = width
        self.pad_x = pad_x
        self.bottom = bottom
        self.fill = fill
        self.inner_w = inner_w if inner_w is not None else width - 2 * pad_x
        self.x = x
        self.min_height = 0.0

    def layout(self, ah: float = 10_000):
        placed = []
        ref = 0.0
        for para, delta in self.items:
            _, h = para.wrap(self.inner_w, ah)
            size = para.style.fontSize
            lead = para.style.leading
            first_base = ref + delta
            top = first_base - size
            placed.append((para, top, h))
            ref = top + h - (lead - size)
        return placed, ref + self.bottom

    def wrap(self, aw, ah):
        self._placed, h = self.layout()
        self.width = aw
        self.height = max(h, self.min_height)
        return aw, self.height

    def natural_height(self) -> float:
        return self.layout()[1]

    def draw(self):
        c = self.canv
        if self.fill:
            c.setFillColor(C(self.fill))
            c.rect(self.x, 0, self.box_w, self.height, fill=1, stroke=0)
        for para, top, h in self._placed:
            para.drawOn(c, self.x + self.pad_x, self.height - top - h)


class Grid(Flowable):
    """Rows of equal-height boxes (cards and steps)."""

    def __init__(self, cells: list[Stack], ncols: int, width: float, gutter: float, row_gap: float):
        super().__init__()
        self.cells = cells
        self.ncols = ncols
        self.cell_w = width
        self.gutter = gutter
        self.row_gap = row_gap

    def rows(self):
        return [self.cells[i : i + self.ncols] for i in range(0, len(self.cells), self.ncols)]

    def row_heights(self):
        return [max(c.natural_height() for c in row) for row in self.rows()]

    def wrap(self, aw, ah):
        hs = self.row_heights()
        self.width = aw
        self.height = sum(hs) + self.row_gap * max(len(hs) - 1, 0)
        return aw, self.height

    def split(self, aw, ah):
        hs = self.row_heights()
        used, take = 0.0, 0
        for i, h in enumerate(hs):
            need = h + (self.row_gap if i else 0)
            if used + need > ah:
                break
            used += need
            take += 1
        if take == 0 or take == len(hs):
            return []
        n = take * self.ncols
        return [
            Grid(self.cells[:n], self.ncols, self.cell_w, self.gutter, self.row_gap),
            Grid(self.cells[n:], self.ncols, self.cell_w, self.gutter, self.row_gap),
        ]

    def draw(self):
        y = self.height
        for row, h in zip(self.rows(), self.row_heights()):
            y -= h
            for i, cell in enumerate(row):
                cell.min_height = h
                cell.wrap(self.cell_w, h)
                cell.drawOn(self.canv, i * (self.cell_w + self.gutter), y)
            y -= self.row_gap


class ContactBanner(Flowable):
    def __init__(self, meta: Meta):
        super().__init__()
        self.meta = meta
        cb = T.contact
        self.w = cb.width
        self.h = cb.height

    def wrap(self, aw, ah):
        self.width, self.height = aw, self.h
        return aw, self.h

    def draw(self):
        cb = T.contact
        c = self.canv
        c.setFillColor(C("navy_deep"))
        c.rect(0, 0, self.w, self.h, fill=1, stroke=0)
        cx = self.w / 2
        c.setFont(BOLD, cb.head_size)
        c.setFillColor(C("white"))
        c.drawCentredString(cx, self.h - cb.top_to_head, "To proceed, please contact us:")
        info = self.meta.contact or {}
        line = cb.separator.join(v for v in (info.get("email"), info.get("phone"), info.get("website")) if v)
        c.setFont(FONT, cb.line_size)
        c.setFillColor(C("banner_text"))
        c.drawCentredString(cx, self.h - cb.top_to_head - cb.head_to_line, line)


class BoxCode(Flowable):
    def __init__(self, text: str, style: ParagraphStyle):
        super().__init__()
        self.para = Paragraph(escape(text).replace("\n", "<br/>").replace("  ", "&nbsp; "), style)

    def wrap(self, aw, ah):
        _, h = self.para.wrap(T.page.box_width - 20, ah)
        self.width, self.height = aw, h + 20
        return aw, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(C("table_alt"))
        c.rect(0, 0, T.page.box_width, self.height, fill=1, stroke=0)
        self.para.drawOn(c, 10, 10)


# ---------------------------------------------------------------------------
# builder


class Builder:
    def __init__(self, meta: Meta):
        self.meta = meta
        self.s = make_styles()
        self.units: list[Unit | str] = []
        self.after_kicker = False
        self.sections_seen = 0

    # helpers -------------------------------------------------------------
    def add(self, unit: Unit):
        self.units.append(unit)
        self.after_kicker = unit.kind == "kicker"

    def para(self, kind: str, text: str, style: ParagraphStyle, keep: bool = False, **extra) -> None:
        if extra:
            style = ParagraphStyle(f"{style.name}_{abs(hash(tuple(extra.items()))) % 99999}", parent=style, **extra)
        self.add(text_unit(kind, Paragraph(inline_xml(text), style), style, keep))

    def page_break(self):
        if self.units and self.units[-1] != "pagebreak":
            self.units.append("pagebreak")

    # blocks ----------------------------------------------------------------
    def block(self, b: dict, nxt: dict | None):
        k = b.get("kind")
        handler = getattr(self, f"b_{k}", None)
        if handler:
            handler(b, nxt)

    def b_heading(self, b, nxt):
        level = b.get("level", 1)
        text = b.get("text") or ""
        if level == 1:
            if self.sections_seen and self.meta.section_break:
                self.page_break()
            self.sections_seen += 1
            self.add(Unit("kicker", Kicker(format_kicker(text)), T.type.kicker.size, 0.0, text_top_adjust(T.type.kicker.size), True))
            if not (nxt and nxt.get("kind") == "heading" and nxt.get("level") == 2):
                self.para("page_title", strip_number(text), self.s["page_title"], keep=True)
            return
        if level == 2:
            if b.get("ruled"):
                style = self.s["large_title"] if b.get("large") else self.s["sub_title"]
                rt = T.ruled_title
                base = rt.rule_to_base if b.get("large") else rt.compact_rule_to_base
                kind = "large_title" if b.get("large") else "compact_title"
                self.add(Unit(kind, RuledTitle(text, style, base, "rule_soft" if b.get("large") else "rule"), 0.0, style.leading - style.fontSize, BOX_TOP_ADJUST + rt.rule_offset, True))
                return
            if self.after_kicker and not b.get("compact"):
                self.para("page_title", text, self.s["page_title"], keep=True)
            elif self.after_kicker:
                self.para("compact_title", text, self.s["sub_title"], keep=True)
            else:
                self.para("sub_title", text, self.s["sub_title"], keep=True)
            return
        if level == 3:
            if b.get("module"):
                self.para("module_title", text, self.s["sub_title"], keep=True)
            else:
                self.para("h3", text, self.s["h3_accent" if b.get("accent") else "h3"], keep=True)
            return
        self.para("h4", text, self.s["h4"], keep=True)

    def b_para(self, b, nxt):
        self.para("body", b.get("text") or "", self.s["body"])

    def b_label(self, b, nxt):
        self.para("label", (b.get("text") or "").strip("*"), self.s["label"], keep=True)

    def b_tight(self, b, nxt):
        for child in b.get("blocks") or []:
            if child.get("kind") in {"para", "label"}:
                self.para("tight", child.get("text") or "", self.s["body"])
            else:
                self.block(child, None)

    def b_list(self, b, nxt, large: bool = False):
        cfg = T.bullets_large if large else T.bullets
        kind = "bullets_large" if large else "bullets"
        base = pstyle("SparkLi", "body", color=cfg.color)
        base.fontSize = cfg.size
        base.leading = cfg.leading
        ordered = bool(b.get("ordered"))
        for n, item in enumerate(b.get("items") or [], 1):
            indent = 18 if item.get("indent") else 0
            st = ParagraphStyle(
                f"SparkLi{n}_{indent}_{kind}",
                parent=base,
                leftIndent=cfg.text_x + indent,
                rightIndent=TEXT_W - cfg.text_x - cfg.text_width,
                bulletIndent=cfg.glyph_x + indent,
                bulletFontName=FONT,
                bulletFontSize=cfg.size,
                bulletColor=C(cfg.color),
            )
            bullet = f"{n}." if ordered else cfg.get("glyph", "•")
            p = Paragraph(inline_xml(item.get("text") or ""), st, bulletText=bullet)
            self.add(text_unit(kind, p, st))

    def b_table(self, b, nxt):
        self.add(Unit("table", data_table(b, self.s)))

    def b_roadmap(self, b, nxt):
        self.add(Unit("roadmap", roadmap_table(b)))

    def b_commercial(self, b, nxt):
        self.add(Unit("commercial", commercial_table(b)))

    def b_callout(self, b, nxt):
        cfg = T.callout
        items = []
        title = b.get("title") or ""
        ref_delta = cfg.top_to_title
        if title:
            items.append((Paragraph(inline_xml(title), self.s["box_title"]), cfg.top_to_title))
            ref_delta = cfg.title_to_body
        first = True
        for child in b.get("blocks") or []:
            for p in simple_paras(child, self.s):
                items.append((p, ref_delta if first else T.spacing.body.body))
                first = False
        inner_w = cfg.get("inner_width", 434.0)
        self.add(Unit("callout", Stack(items, cfg.width, cfg.pad_x, cfg.body_to_bottom, "callout_bg", inner_w=inner_w)))

    def b_columns(self, b, nxt):
        cfg = T.columns
        cols = b.get("columns") or []
        stacks = []
        first_size = T.type.box_title.size
        for i, col in enumerate(cols[:2]):
            items = []
            delta = first_size
            if col.get("title"):
                style = self.s["box_title_accent"] if (col.get("accent") or i == 1) else self.s["box_title"]
                items.append((Paragraph(inline_xml(col["title"]), style), first_size))
                delta = cfg.title_to_body
            for child in col.get("blocks") or []:
                for p in simple_paras(child, self.s):
                    items.append((p, delta))
                    delta = T.spacing.body.body
            stacks.append(Stack(items, cfg.width, 0, 0, None, inner_w=cfg.width))
        self.add(Unit("columns", Columns(stacks, cfg.inset, cfg.width, cfg.gutter), first_size, 0.0, text_top_adjust(first_size)))

    def b_cards(self, b, nxt):
        cfg = T.cards
        cells = []
        for card in b.get("cards") or []:
            items = [(Paragraph(card_title_markup(card.get("title") or ""), self.s["card_title"]), cfg.top_to_title)]
            first = True
            for child in card.get("blocks") or []:
                for p in simple_paras(child, self.s, body="card_body"):
                    items.append((p, cfg.title_to_body if first else T.type.card_body.leading + 4))
                    first = False
            cells.append(Stack(items, cfg.width, cfg.pad_x, cfg.body_to_bottom, "callout_bg"))
        ncols = 4 if len(cells) == 4 and b.get("four") else 2
        self.add(Unit("cards", Grid(cells, ncols, cfg.width, cfg.gutter, cfg.row_gap)))

    def b_steps(self, b, nxt):
        cfg = T.steps
        steps = b.get("steps") or []
        n = max(len(steps), 1)
        w = (cfg.total_width - cfg.gutter * (n - 1)) / n
        pad = cfg.pad_x
        num_style = ParagraphStyle("SparkStepNum", fontName=BOLD, fontSize=cfg.number_size, leading=cfg.number_size * 1.15, textColor=C("purple"), alignment=TA_CENTER)
        title_style = ParagraphStyle("SparkStepTitle", parent=self.s["box_title"], alignment=TA_CENTER)
        body_style = ParagraphStyle("SparkStepBody", parent=self.s["card_body"], alignment=TA_CENTER)
        cells = []
        for i, st in enumerate(steps, 1):
            title = re.sub(r"^\d{1,2}\s+", "", st.get("title") or "")
            items = [
                (Paragraph(f"{i:02d}", num_style), cfg.top_to_number),
                (Paragraph(inline_xml(title), title_style), cfg.number_to_title),
            ]
            if st.get("body"):
                items.append((Paragraph(inline_xml(st["body"]), body_style), cfg.title_to_body))
            cells.append(Stack(items, w, pad, cfg.body_to_bottom, "step_bg"))
        self.add(Unit("steps", Grid(cells, n, w, cfg.gutter, 0)))

    def b_kpis(self, b, nxt):
        cfg = T.kpis
        label_first = ParagraphStyle("SparkKpiLabel1", fontName=BOLD, fontSize=cfg.first_label_size, leading=cfg.first_label_size * 1.15, textColor=C("body_alt"))
        label = ParagraphStyle("SparkKpiLabel", fontName=BOLD, fontSize=cfg.label_size, leading=cfg.label_size * 1.15, textColor=C("body_alt"))
        item = ParagraphStyle(
            "SparkKpiItem",
            fontName=FONT,
            fontSize=11,
            leading=12.65,
            textColor=C("body_alt"),
            leftIndent=cfg.text_x - cfg.pad_x,
            bulletIndent=cfg.glyph_x - cfg.pad_x,
            bulletFontName=FONT,
            bulletFontSize=10,
            bulletColor=C("body"),
        )
        items = []
        delta = cfg.top_to_label
        for gi, group in enumerate(b.get("groups") or []):
            if group.get("title"):
                items.append((Paragraph(inline_xml(group["title"]), label_first if gi == 0 else label), delta))
                delta = cfg.first_label_to_item if gi == 0 else cfg.label_to_item
            for it in group.get("items") or []:
                items.append((Paragraph(inline_xml(it), item, bulletText=cfg.glyph), delta))
                delta = cfg.item_pitch
            delta = cfg.item_to_label
        inner_w = (cfg.text_x - cfg.pad_x) + cfg.text_width
        self.add(Unit("kpis", Stack(items, cfg.width, cfg.pad_x, cfg.bottom_pad, "tint_bg", inner_w=inner_w)))

    def b_contact(self, b, nxt):
        self.add(Unit("contact", ContactBanner(self.meta)))

    def b_image(self, b, nxt):
        fig = figure_flowable(b)
        if fig is not None:
            self.add(Unit("figure", fig))

    def b_code(self, b, nxt):
        self.add(Unit("code", BoxCode(b.get("text") or "", self.s["code"])))

    def b_stageflow(self, b, nxt):
        self.add(Unit("stageflow", StageFlowDiagram(b.get("steps") or [], self.s)))

    def b_hr(self, b, nxt):
        self.add(Unit("hr", HRule()))

    def b_pagebreak(self, b, nxt):
        self.page_break()
        self.after_kicker = False

    def b_addendum(self, b, nxt):
        self.page_break()
        self.units.append(Unit("addendum", AddendumCover(b.get("fields") or {}, self.meta)))
        self.units.append("pagebreak")
        self.sections_seen = 0  # the next H1 starts on the page right after the addendum cover

    def b_large(self, b, nxt):
        for child in b.get("blocks") or []:
            if child.get("kind") == "list":
                self.b_list(child, None, large=True)
            else:
                self.block(child, None)


class Columns(Flowable):
    def __init__(self, stacks: list[Stack], inset: float, width: float, gutter: float):
        super().__init__()
        self.stacks = stacks
        self.inset = inset
        self.col_w = width
        self.gutter = gutter

    def wrap(self, aw, ah):
        self.width = aw
        self.height = max((s.natural_height() for s in self.stacks), default=0)
        return aw, self.height

    def draw(self):
        for i, s in enumerate(self.stacks):
            s.min_height = self.height
            s.wrap(self.col_w, self.height)
            s.drawOn(self.canv, self.inset + i * (self.col_w + self.gutter), 0)


class HRule(Flowable):
    def wrap(self, aw, ah):
        self.width, self.height = aw, 1
        return aw, 1

    def draw(self):
        self.canv.setStrokeColor(C("rule"))
        self.canv.setLineWidth(0.7)
        self.canv.line(0, 0.5, T.page.rule_right - LEFT, 0.5)


def strip_number(text: str) -> str:
    m = re.match(r"^(?:A?\d+(?:\.\d+)?)[\.\):\-–—]?\s+(.*)$", text.strip(), re.I)
    return m.group(1) if m else text


CARD_NUM_RE = re.compile(r"^(\d{2})\s+(.+)$")


def card_title_markup(title: str) -> str:
    m = CARD_NUM_RE.match(title.strip())
    if m:
        return f'<font color="{tokmod.color("purple")}">{m.group(1)}</font>&nbsp;&nbsp;{inline_xml(m.group(2))}'
    return inline_xml(title)


def simple_paras(block: dict, s, body: str = "body_left") -> list[Paragraph]:
    k = block.get("kind")
    if k in {"para", "label"}:
        style = s["label"] if k == "label" else s[body]
        return [Paragraph(inline_xml((block.get("text") or "").strip("*") if k == "label" else block.get("text") or ""), style)]
    if k == "list":
        out = []
        for item in block.get("items") or []:
            st = ParagraphStyle("SparkInnerLi", parent=s[body], leftIndent=14, bulletIndent=2)
            out.append(Paragraph(inline_xml(item.get("text") or ""), st, bulletText="•"))
        return out
    if k == "heading":
        return [Paragraph(inline_xml(block.get("text") or ""), s["h4"])]
    return []


# ---------------------------------------------------------------------------
# tables


def scaled_widths(widths: list[float] | None, ncols: int, total: float, content: list[list[str]] | None = None) -> list[float]:
    if widths and len(widths) == ncols:
        s = sum(widths)
        if abs(s - total) < 1.5:
            return widths
        return [w * total / s for w in widths]
    if content:
        lengths = [max(len(re.sub(r"[*_`]", "", row[i])) if i < len(row) else 0 for row in content) for i in range(ncols)]
        lengths = [min(max(n, 8), 60) for n in lengths]
        s = sum(lengths) or 1
        return [total * n / s for n in lengths]
    return [total / ncols] * ncols


def data_table(b: dict, s) -> Table:
    cfg = T.table
    headers = b.get("headers") or []
    rows = b.get("rows") or []
    ncols = max(len(headers), max((len(r) for r in rows), default=0), 1)
    widths = scaled_widths(b.get("widths"), ncols, cfg.width, [headers, *rows])
    data = [[Paragraph(inline_xml(h), s["th"]) for h in headers]]
    for row in rows:
        data.append([
            Paragraph(inline_xml(c), s["td_bold"] if (b.get("bold_first") and i == 0) else s["td"])
            for i, c in enumerate(row + [""] * (ncols - len(row)))
        ])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), C("navy_deep")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), cfg.pad_x),
        ("RIGHTPADDING", (0, 0), (-1, -1), cfg.get("pad_right", cfg.pad_x)),
        ("TOPPADDING", (0, 0), (-1, -1), cfg.pad_top),
        ("BOTTOMPADDING", (0, 0), (-1, 0), cfg.pad_bottom),
        ("BOTTOMPADDING", (0, 1), (-1, -1), cfg.pad_bottom),
        ("GRID", (0, 0), (-1, -1), cfg.grid_width, C("grid")),
    ]
    for r in range(1, len(data)):
        cmds.append(("BACKGROUND", (0, r), (-1, r), C("table_alt") if r % 2 == 0 else C("white")))
    t.setStyle(TableStyle(cmds))
    return t


def roadmap_table(b: dict) -> Table:
    cfg = T.roadmap
    headers = b.get("headers") or ["Deliverables", "Duration"]
    rows = b.get("rows") or []
    widths = scaled_widths(b.get("widths") or cfg.widths, 2, sum(cfg.widths))
    head = ParagraphStyle("SparkRmHead", fontName=BOLD, fontSize=cfg.head_size, leading=cfg.head_size * 1.15, textColor=C(cfg.head_color))
    body = ParagraphStyle("SparkRmBody", fontName=FONT, fontSize=cfg.body_size, leading=cfg.body_leading, textColor=C(cfg.body_color))
    dur = ParagraphStyle("SparkRmDur", fontName=BOLD, fontSize=cfg.duration_size, leading=cfg.duration_size * 1.15, textColor=C(cfg.duration_color))
    data = [[Paragraph(inline_xml(h), head) for h in headers[:2]]]
    for row in rows:
        row = (row + ["", ""])[:2]
        data.append([Paragraph(inline_xml(row[0]), body), Paragraph(inline_xml(row[1]), dur)])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    head_bottom = cfg.head_height - cfg.head_pad_top - head.leading
    cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), C("tint_bg")),
        ("GRID", (0, 0), (-1, -1), 1, C("white")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), cfg.pad_x),
        ("RIGHTPADDING", (0, 0), (-1, -1), cfg.pad_x),
        ("TOPPADDING", (0, 0), (-1, 0), cfg.head_pad_top),
        ("BOTTOMPADDING", (0, 0), (-1, 0), head_bottom),
        ("TOPPADDING", (0, 1), (0, -1), cfg.body_pad_top),
        ("TOPPADDING", (1, 1), (1, -1), cfg.body_pad_top + cfg.duration_offset + (cfg.body_size - cfg.duration_size)),
        ("BOTTOMPADDING", (0, 1), (-1, -1), cfg.body_pad_bottom - (cfg.body_leading - cfg.body_size)),
    ]
    t.setStyle(TableStyle(cmds))
    return t


def commercial_table(b: dict) -> Table:
    cfg = T.commercial
    headers = b.get("headers") or []
    rows = b.get("rows") or []
    total = b.get("total")
    ncols = max(len(headers), 1)
    widths = scaled_widths(b.get("widths") or cfg.widths, ncols, sum(cfg.widths))
    head = ParagraphStyle("SparkCmHead", fontName=BOLD, fontSize=cfg.size, leading=cfg.leading, textColor=C("white"), alignment=TA_CENTER)
    body = ParagraphStyle("SparkCmBody", fontName=FONT, fontSize=cfg.size, leading=cfg.leading, textColor=C("body"))
    data = [[Paragraph(inline_xml(h), head) for h in headers]]
    for row in rows:
        data.append([Paragraph(inline_xml(c), body) for c in (row + [""] * ncols)[:ncols]])
    if total:
        data.append([Paragraph(inline_xml(c), head) for c in (total + [""] * ncols)[:ncols]])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    lead_gap = cfg.leading - cfg.size
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), C("slate")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), cfg.pad_left),
        ("RIGHTPADDING", (0, 0), (-1, -1), cfg.pad_right),
        ("TOPPADDING", (0, 0), (-1, -1), cfg.pad_top),
        ("BOTTOMPADDING", (0, 0), (-1, 0), cfg.head_pad_bottom - lead_gap),
        ("BOTTOMPADDING", (0, 1), (-1, -1), cfg.pad_bottom - lead_gap),
    ]
    if total:
        cmds += [
            ("BACKGROUND", (0, -1), (-1, -1), C("slate")),
            ("BOTTOMPADDING", (0, -1), (-1, -1), cfg.total_pad_bottom - lead_gap),
        ]
    t.setStyle(TableStyle(cmds))
    return t


# ---------------------------------------------------------------------------
# figures


WARNINGS: list[str] = []


def figure_flowable(block: dict) -> Flowable | None:
    path = Path(block.get("path") or "")
    if not path.is_file():
        msg = f"Image not found: {block.get('path')}"
        WARNINGS.append(msg)
        print(f"warning: {msg}")
        return Paragraph(escape(msg), ParagraphStyle("SparkMissing", fontName=ITALIC, fontSize=9, textColor=C("subtle")))
    embed = raster_path_for_pdf(path)
    iw, ih = ImageReader(embed).getSize()
    scale_w = 1.0
    if block.get("trim", True):
        trimmed, (tw, th) = trim_whitespace(embed)
        scale_w = tw / iw
        embed, iw, ih = trimmed, tw, th
    if block.get("width"):
        w = min(float(block["width"]) * scale_w, T.figure.full_width)
        h = ih * w / iw
    else:
        w, h = fit_image(
            iw,
            ih,
            float(block.get("asset_scale") or 1),
            diagram=bool(block.get("diagram")),
            diagram_wide=bool(block.get("diagram_wide")),
        )
    max_h = FRAME_H - 4
    if h > max_h:
        w, h = w * max_h / h, max_h
    img = RLImage(embed, width=w, height=h)
    img.hAlign = "CENTER"
    return CenteredImage(img, w, h)


TRIM_CACHE = ASSETS / "trim_cache"


def trim_whitespace(path: str, threshold: int = 245, pad_frac: float = 0.012) -> tuple[str, tuple[int, int]]:
    """Crop near-white margins (keeps scale); cached by content hash."""
    import hashlib

    from PIL import Image, ImageChops

    src = Path(path)
    digest = hashlib.sha1(src.read_bytes()).hexdigest()[:16]
    is_jpeg = src.suffix.lower() in {".jpg", ".jpeg"}
    out = TRIM_CACHE / f"{digest}.{'jpg' if is_jpeg else 'png'}"
    if out.is_file():
        with Image.open(out) as im:
            return str(out), im.size
    with Image.open(src) as im:
        rgb = im.convert("RGB")
        bg = Image.new("RGB", rgb.size, (255, 255, 255))
        diff = ImageChops.difference(rgb, bg).convert("L").point(lambda v: 255 if v > 255 - threshold else 0)
        box = diff.getbbox()
        if not box:
            return str(src), rgb.size
        pad = int(max(rgb.size) * pad_frac)
        box = (max(box[0] - pad, 0), max(box[1] - pad, 0), min(box[2] + pad, rgb.width), min(box[3] + pad, rgb.height))
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.97 * rgb.width * rgb.height:
            return str(src), rgb.size
        TRIM_CACHE.mkdir(parents=True, exist_ok=True)
        cropped = rgb.crop(box)
        if is_jpeg:
            cropped.save(out, quality=92, optimize=True)
        else:
            cropped.save(out, optimize=True)
        return str(out), cropped.size


class CenteredImage(Flowable):
    def __init__(self, img, w, h):
        super().__init__()
        self.img = img
        self.w = w
        self.h = h

    def wrap(self, aw, ah):
        self.width, self.height = aw, self.h
        return aw, self.h

    def draw(self):
        x = (T.page.text_width - self.w) / 2 + (T.figure.full_x - LEFT - (T.page.text_width - T.figure.full_width) / 2)
        self.img.drawOn(self.canv, x, 0)


# ---------------------------------------------------------------------------


def assemble(units: list[Unit | str]) -> list[Flowable]:
    story: list[Flowable] = []
    prev: Unit | None = None
    for u in units:
        if u == "pagebreak":
            story.append(PageBreak())
            prev = None
            continue
        assert isinstance(u, Unit)
        if u.kind == "addendum":
            story.append(u.flowable)
            prev = None
            continue
        gap = 0.0 if prev is None else spacing(prev.kind, u.kind) - prev.bottom_ref - u.top_ref
        story.append(Spaced(u.flowable, gap, u.top_adjust, keep_with_next=u.keep, keep_together=family(u.kind) in KEEP_TOGETHER))
        prev = u
    return story


def build_pdf(meta: Meta, blocks: list[dict], output: Path) -> Path:
    register_fonts()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SparkDoc(str(output), meta=meta)
    builder = Builder(meta)
    for i, block in enumerate(blocks):
        builder.block(block, blocks[i + 1] if i + 1 < len(blocks) else None)
    story: list[Flowable] = []
    if meta.has_cover():
        story += [NextPageTemplate("inner"), PageBreak()]
    story += assemble(builder.units)
    doc.build(story)
    return output
