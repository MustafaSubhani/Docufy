"""Convert a Spark-template PDF back into Spark markdown.

Heuristic, tuned to the ADICC reference layout (fills, sizes, colours). Used to build the golden
transcription and to import existing Spark PDFs.

    py -m spark_docs.importer "sample pdfs/ADICC_Proposal_updated (1).pdf" -o tests/golden/adicc.md --assets tests/golden/assets
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .measure import PageMeasure, Shape, Span, measure_page

PURPLE = "#6B4EFF"
TABLE_HEADS = {"#0D1B3E", "#1E293B"}
SMALL_WORDS = {"and", "of", "the", "for", "to", "in", "on", "a", "an", "or", "with", "by"}
UPPER_WORDS = {"AI", "UAE", "P6", "ERP", "BOQ", "API", "QSRA", "RBAC"}
SPECIAL_WORDS = {"KPIS": "KPIs", "EOT": "EoT"}


def clean(text: str) -> str:
    return text.replace("\u200b", "").replace("\u00a0", " ").strip()


def title_case(upper: str) -> str:
    out = []
    for i, w in enumerate(upper.split()):
        key = w.upper()
        if key in SPECIAL_WORDS:
            out.append(SPECIAL_WORDS[key])
        elif key in UPPER_WORDS:
            out.append(key)
        elif i > 0 and w.lower() in SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


@dataclass
class Line:
    y0: float
    y1: float
    x0: float
    x1: float
    spans: list[Span]

    @property
    def text(self) -> str:
        parts: list[str] = []
        prev: Span | None = None
        for s in self.spans:
            t = s.text
            if prev is not None and s.x0 - prev.x1 > 1.5 and not parts[-1].endswith(" ") and not t.startswith(" "):
                parts.append(" ")
            parts.append(t)
            prev = s
        return clean("".join(parts))

    @property
    def first(self) -> Span:
        return self.spans[0]

    @property
    def size(self) -> float:
        return max(s.size for s in self.spans)

    def md_text(self) -> str:
        """Inline markdown with bold runs preserved when mixed."""
        weights = {s.bold for s in self.spans if clean(s.text)}
        if len(weights) <= 1:
            return self.text
        out: list[str] = []
        for s in self.spans:
            t = s.text.replace("\u200b", "")
            if not t.strip():
                out.append(t)
                continue
            out.append(f"**{t.strip()}**{' ' if t.endswith(' ') else ''}" if s.bold else t)
        return clean("".join(out))


@dataclass
class Region:
    kind: str
    x0: float
    y0: float
    x1: float
    y1: float
    rects: list[Shape] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)

    def contains(self, s: Span) -> bool:
        cx = (s.x0 + s.x1) / 2
        cy = (s.y0 + s.y1) / 2
        return self.x0 - 2 <= cx <= self.x1 + 2 and self.y0 - 2 <= cy <= self.y1 + 2


def group_lines(spans: list[Span]) -> list[Line]:
    lines: list[Line] = []
    for s in sorted(spans, key=lambda s: (round(s.y0), s.x0)):
        for ln in lines:
            if abs(ln.y0 - s.y0) < 2.2 and s.x0 - ln.x1 < 40 and ln.x0 - s.x1 < 40:
                ln.spans.append(s)
                ln.x0 = min(ln.x0, s.x0)
                ln.x1 = max(ln.x1, s.x1)
                ln.y0 = min(ln.y0, s.y0)
                ln.y1 = max(ln.y1, s.y1)
                break
        else:
            lines.append(Line(s.y0, s.y1, s.x0, s.x1, [s]))
    for ln in lines:
        ln.spans.sort(key=lambda s: s.x0)
    lines.sort(key=lambda ln: (ln.y0, ln.x0))
    return lines


def detect_regions(pm: PageMeasure) -> list[Region]:
    rects = [s for s in pm.shapes if s.kind == "rect" and s.fill]
    regions: list[Region] = []
    used: set[int] = set()

    def union(kind: str, members: list[Shape]) -> Region:
        return Region(
            kind,
            min(r.x0 for r in members),
            min(r.y0 for r in members),
            max(r.x1 for r in members),
            max(r.y1 for r in members),
            members,
        )

    # contact banner
    for i, r in enumerate(rects):
        if r.fill in {"#0D1B3E"} and 430 < r.x1 - r.x0 < 470 and 35 < r.y1 - r.y0 < 60:
            texts = [s.text for s in pm.spans if r.y0 <= s.y0 <= r.y1]
            if any("To proceed" in t for t in texts):
                regions.append(union("contact", [r]))
                used.add(i)

    # tables (header fill), extend downwards over touching rows
    heads = [i for i, r in enumerate(rects) if r.fill in TABLE_HEADS and i not in used]
    head_rows: dict[float, list[int]] = {}
    for i in heads:
        head_rows.setdefault(round(rects[i].y0, 0), []).append(i)
    taken_rows: set[float] = set()
    for y in sorted(head_rows):
        if y in taken_rows:
            continue
        members = [rects[i] for i in head_rows[y]]
        used.update(head_rows[y])
        commercial = members[0].fill == "#1E293B"
        bottom = max(r.y1 for r in members)
        x0 = min(r.x0 for r in members)
        x1 = max(r.x1 for r in members)
        changed = True
        while changed:
            changed = False
            for i, r in enumerate(rects):
                if i in used:
                    continue
                if abs(r.y0 - bottom) < 1.5 and r.x0 >= x0 - 1 and r.x1 <= x1 + 1:
                    members.append(r)
                    used.add(i)
                    changed = True
            new_bottom = max(r.y1 for r in members)
            if new_bottom > bottom:
                bottom = new_bottom
        if commercial:
            # body rows have no fills; the TOTAL row is a second dark band further down
            for other_y in sorted(head_rows):
                if other_y > y + 5 and other_y not in taken_rows:
                    extra = [rects[i] for i in head_rows[other_y]]
                    if abs(min(r.x0 for r in extra) - x0) < 2:
                        members.extend(extra)
                        used.update(head_rows[other_y])
                        taken_rows.add(other_y)
                        break
        regions.append(union("commercial" if commercial else "table", members))

    # tinted blocks
    for i, r in enumerate(rects):
        if i in used:
            continue
        w = r.x1 - r.x0
        if r.fill == "#E5EAF1":
            kind = "callout" if w > 400 else "card"
        elif r.fill == "#EEF2F8":
            kind = "step"
        elif r.fill == "#EEF4FB":
            kind = "tint"
        elif r.fill == "#F8F7FE":
            kind = "coverbox"
        else:
            continue
        regions.append(Region(kind, r.x0, r.y0, r.x1, r.y1, [r]))
        used.add(i)

    # merge tint rects: roadmap (2 cols) or kpis (1 col)
    tints = [g for g in regions if g.kind == "tint"]
    if tints:
        regions = [g for g in regions if g.kind != "tint"]
        xs = sorted({round(g.x0) for g in tints})
        members = [r for g in tints for r in g.rects]
        merged = union("roadmap" if len(xs) > 1 else "kpis", members)
        regions.append(merged)

    # group cards / steps into one grid each
    for kind in ("card", "step"):
        items = [g for g in regions if g.kind == kind]
        if items:
            regions = [g for g in regions if g.kind != kind]
            grid = union("cards" if kind == "card" else "steps", [r for g in items for r in g.rects])
            grid.rects = [r for g in items for r in g.rects]
            regions.append(grid)
    return regions


# ---------------------------------------------------------------------------


@dataclass
class Out:
    y: float
    md: str
    kind: str
    last_x1: float = 0.0
    last_y: float = 0.0


def classify_heading(ln: Line) -> str | None:
    s = ln.first
    t = ln.text
    if s.bold and abs(s.size - 8) < 0.3 and s.color == PURPLE:
        m = re.match(r"^(A?\d+)\s+(.*)$", t)
        if m:
            return f"# {m.group(1)} {title_case(m.group(2))}"
        return f"# {title_case(t)}"
    if s.bold and abs(s.size - 26) < 0.5:
        return f"## {t}"
    if s.bold and abs(s.size - 20) < 0.5:
        return f"## {t} {{.ruled .large}}"
    if s.bold and abs(s.size - 14) < 0.5 and s.color in {"#1A1A2E"}:
        if re.match(r"^Module\s+\d", t):
            return f"### {t}"
        return f"## {t}"
    if s.bold and abs(s.size - 13) < 0.5 and s.color == "#1A1A2E":
        return f"### {t}"
    if s.bold and abs(s.size - 11) < 0.3 and s.color == "#1A1A2E" and not t.endswith(":"):
        return f"#### {t}"
    return None


BULLETS = {"•", "●", "◦", "▪"}


def body_blocks(lines: list[Line]) -> list[Out]:
    """Turn free-flowing lines (outside tinted regions) into markdown blocks."""
    outs: list[Out] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        head = classify_heading(ln)
        if head:
            # merge wrapped heading lines
            j = i + 1
            text = head
            while j < len(lines) and classify_heading(lines[j]) is None and lines[j].first.bold and abs(lines[j].size - ln.size) < 0.3 and lines[j].y0 - lines[j - 1].y0 < ln.size * 1.4:
                text += " " + lines[j].text
                j += 1
            outs.append(Out(ln.y0, text, "heading", ln.x1, ln.y0))
            i = j
            continue
        first = clean(ln.first.text)
        if first in BULLETS:
            items: list[str] = []
            text_x = ln.spans[1].x0 if len(ln.spans) > 1 else ln.x0 + 14
            cur = Line(ln.y0, ln.y1, ln.x0, ln.x1, ln.spans[1:]).md_text()
            last = ln
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                nfirst = clean(nxt.first.text)
                gap = nxt.y0 - last.y0
                if nfirst in BULLETS and gap < 22:
                    items.append(cur)
                    cur = Line(nxt.y0, nxt.y1, nxt.x0, nxt.x1, nxt.spans[1:]).md_text()
                    last = nxt
                    j += 1
                    continue
                if abs(nxt.x0 - text_x) < 3 and gap < 16 and classify_heading(nxt) is None:
                    cur += " " + nxt.md_text()
                    last = nxt
                    j += 1
                    continue
                break
            items.append(cur)
            outs.append(Out(ln.y0, "\n".join(f"- {it}" for it in items), "list", last.x1, last.y0))
            i = j
            continue
        # paragraph
        text = ln.md_text()
        last = ln
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if classify_heading(nxt) or clean(nxt.first.text) in BULLETS:
                break
            if abs(nxt.x0 - ln.x0) > 3 or nxt.y0 - last.y0 > ln.size * 1.45:
                break
            text += " " + nxt.md_text()
            last = nxt
            j += 1
        bold_label = all(s.bold for s in ln.spans if clean(s.text)) and text.endswith(":")
        outs.append(Out(ln.y0, f"**{text}**" if bold_label and not text.startswith("**") else text, "label" if bold_label else "para", last.x1, last.y0))
        i = j
    return group_tight(outs)


def group_tight(outs: list[Out]) -> list[Out]:
    """Paragraph runs separated by ~16.5pt (instead of ~31) become a :::tight group."""
    result: list[Out] = []
    run: list[Out] = []

    def flush():
        if len(run) >= 2:
            body = "\n\n".join(o.md for o in run)
            result.append(Out(run[0].y, f":::tight\n{body}\n:::", "tight", run[-1].last_x1, run[-1].last_y))
        else:
            result.extend(run)
        run.clear()

    for o in outs:
        if o.kind == "para" and run and o.y - run[-1].last_y < 20:
            run.append(o)
            continue
        if o.kind == "para" and not run:
            run.append(o)
            continue
        if o.kind == "para":
            flush()
            run.append(o)
            continue
        flush()
        result.append(o)
    flush()
    return result


def region_block(reg: Region, pm: PageMeasure) -> Out | None:
    lines = group_lines([s for s in pm.spans if reg.contains(s)])
    if reg.kind == "contact":
        return Out(reg.y0, ":::contact\n:::", "contact")
    if reg.kind == "callout":
        title, body = split_title(lines, 11)
        return Out(reg.y0, f":::callout\n### {title}\n{body}\n:::", "callout")
    if reg.kind == "cards":
        cards = []
        for r in sorted(reg.rects, key=lambda r: (round(r.y0), r.x0)):
            sub = Region("card", r.x0, r.y0, r.x1, r.y1)
            title, body = split_title(group_lines([s for s in pm.spans if sub.contains(s)]), 10)
            cards.append(f"### {title}\n{body}")
        return Out(reg.y0, ":::cards\n" + "\n\n".join(cards) + "\n:::", "cards")
    if reg.kind == "steps":
        steps = []
        for r in sorted(reg.rects, key=lambda r: r.x0):
            sub = Region("step", r.x0, r.y0, r.x1, r.y1)
            sl = group_lines([s for s in pm.spans if sub.contains(s)])
            title = " ".join(ln.text for ln in sl if ln.first.bold and abs(ln.size - 11) < 0.3)
            body = " ".join(ln.text for ln in sl if not ln.first.bold)
            steps.append(f"### {title}\n{body}")
        return Out(reg.y0, ":::steps\n" + "\n\n".join(steps) + "\n:::", "steps")
    if reg.kind in {"table", "commercial", "roadmap"}:
        return Out(reg.y0, table_md(reg, pm), reg.kind)
    if reg.kind == "kpis":
        groups: list[tuple[str, list[str]]] = []
        for ln in lines:
            first = clean(ln.first.text)
            if ln.first.bold and first not in BULLETS:
                groups.append((ln.text, []))
            elif first in BULLETS:
                if not groups:
                    groups.append(("", []))
                groups[-1][1].append(Line(ln.y0, ln.y1, ln.x0, ln.x1, ln.spans[1:]).text)
            elif groups and groups[-1][1]:
                groups[-1][1][-1] += " " + ln.text
        parts = []
        for title, items in groups:
            block = (f"### {title}\n" if title else "") + "\n".join(f"- {it}" for it in items)
            parts.append(block)
        return Out(reg.y0, ":::kpis\n" + "\n\n".join(parts) + "\n:::", "kpis")
    return None


def split_title(lines: list[Line], size: float) -> tuple[str, str]:
    title_parts, body_parts = [], []
    for ln in lines:
        if ln.first.bold and abs(ln.size - size) < 0.4 and not body_parts:
            title_parts.append(ln.text)
        else:
            body_parts.append(ln.md_text())
    return " ".join(title_parts), " ".join(body_parts)


def table_md(reg: Region, pm: PageMeasure) -> str:
    rects = sorted(reg.rects, key=lambda r: (round(r.y0), r.x0))
    cols = sorted({round(r.x0, 1) for r in rects})
    col_edges = cols + [max(r.x1 for r in rects)]
    if reg.kind == "commercial":
        # body rows have no fills: use text rows between header bottom and total row top
        heads = [r for r in rects if r.fill == "#1E293B"]
        top_band = min(r.y0 for r in heads)
        header_bottom = max(r.y1 for r in heads if abs(r.y0 - top_band) < 1)
        total_top = max(r.y0 for r in heads)
        row_edges = [top_band, header_bottom]
        ys = sorted({round(s.y0, 1) for s in pm.spans if header_bottom < s.y0 < total_top})
        starts = [ys[0]] if ys else []
        for prev, cur in zip(ys, ys[1:]):
            if cur - prev > 14:
                starts.append(cur)
        row_edges += [y - 7 for y in starts[1:]] + [total_top, max(r.y1 for r in heads)]
    else:
        row_edges = sorted({round(r.y0, 1) for r in rects}) + [max(r.y1 for r in rects)]
    grid: list[list[str]] = []
    for r_i in range(len(row_edges) - 1):
        y0, y1 = row_edges[r_i], row_edges[r_i + 1]
        row = []
        for c_i in range(len(col_edges) - 1):
            x0, x1 = col_edges[c_i], col_edges[c_i + 1]
            cell = [s for s in pm.spans if x0 - 1 <= (s.x0 + s.x1) / 2 <= x1 + 1 and y0 - 1 <= (s.y0 + s.y1) / 2 <= y1 + 1]
            lines = group_lines(cell)
            row.append(" ".join(ln.md_text() for ln in lines).replace("|", "\\|"))
        if any(c.strip() for c in row):
            grid.append(row)
    if not grid:
        return ""
    widths = [round(col_edges[i + 1] - col_edges[i], 1) for i in range(len(col_edges) - 1)]
    bold_first = reg.kind == "table" and any(
        s.bold and s.color != "#FFFFFF" for s in pm.spans if col_edges[0] <= s.x0 < col_edges[1] and row_edges[1] <= s.y0 <= row_edges[-1]
    )
    head = "| " + " | ".join(grid[0]) + " |"
    sep = "| " + " | ".join("---" for _ in grid[0]) + " |"
    body = ["| " + " | ".join(r) + " |" for r in grid[1:]]
    attrs = f"widths={','.join(str(w) for w in widths)}"
    if bold_first:
        attrs += " bold_first"
    name = {"table": "table", "commercial": "commercial", "roadmap": "roadmap"}[reg.kind]
    return f":::{name} {attrs}\n" + "\n".join([head, sep, *body]) + "\n:::"


# ---------------------------------------------------------------------------


def cover_frontmatter(pm: PageMeasure) -> str:
    lines = group_lines(pm.spans)
    data: dict[str, str] = {}
    note: list[str] = []
    for ln in lines:
        s = ln.first
        t = ln.text
        if s.color == PURPLE and abs(s.size - 9) < 0.3:
            data["doc_type"] = t
        elif abs(s.size - 36) < 0.5:
            data["title"] = t
        elif abs(s.size - 18) < 0.5:
            data["subtitle"] = t
        elif t.startswith("Prepared for:"):
            data["prepared_for"] = t.split(":", 1)[1].strip()
        elif t.startswith("Prepared by:"):
            data["prepared_by"] = t.split(":", 1)[1].strip()
        elif t.startswith("Date:"):
            data["date"] = t.split(":", 1)[1].strip()
        elif s.italic:
            note.append(t)
    data["note"] = " ".join(note)
    return data


def import_pdf(path: Path, assets_dir: Path, md_dir: Path) -> str:
    doc = pymupdf.open(str(path))
    assets_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    prev_bottom = 0.0
    prev_kind = ""
    for page in doc:
        pm = measure_page(page)
        n = pm.number
        rule_lines = [s for s in pm.shapes if s.kind == "line" and (s.x1 - s.x0) > 400 and s.stroke in {"#C8C8C8", "#E2E8F0"}]
        if n == 1:
            data = cover_frontmatter(pm)
            fm = [
                "---",
                f"title: {data.get('title', '')}",
                f"subtitle: {data.get('subtitle', '')}",
                f"doc_type: {data.get('doc_type', 'PROPOSAL')}",
                f"prepared_for: {data.get('prepared_for', '')}",
                f"prepared_by: {data.get('prepared_by', 'Spark Technologies')}",
                f"date: {data.get('date', '')}",
                "confidential: true",
                "section_break: true",
                "show_contact: false",
                "include_spark_content: false",
                "---",
            ]
            chunks.append("\n".join(fm))
            prev_bottom = 900
            continue
        has_kicker = any(s.bold and abs(s.size - 8) < 0.3 and s.color == PURPLE for s in pm.spans)
        if any(abs(s.size - 36) < 0.5 for s in pm.spans):
            data = cover_frontmatter(pm)
            chunks.append(
                ":::addendum\n"
                + "\n".join(
                    f"{k}: {data[k]}"
                    for k in ("doc_type", "title", "subtitle", "prepared_for", "prepared_by", "date", "note")
                    if data.get(k)
                ).replace("doc_type:", "label:")
                + "\n:::"
            )
            prev_bottom = 900
            continue

        regions = detect_regions(pm)
        region_spans = [s for s in pm.spans if any(r.contains(s) for r in regions)]
        free = [s for s in pm.spans if s not in region_spans]
        outs: list[Out] = []

        # two-column text (e.g. Challenge / Opportunity)
        right = [s for s in free if s.x0 > 280 and not s.bold or (s.x0 > 280 and s.bold and abs(s.size - 11) < 0.3)]
        if right and not any(r.kind in {"table", "commercial", "roadmap"} for r in regions):
            y_top = min(s.y0 for s in right) - 2
            y_bot = max(s.y1 for s in right) + 2
            left_below = sorted(s.y0 for s in free if s.x0 < 280 and s.y0 >= y_top)
            for prev, cur in zip(left_below, left_below[1:]):
                if cur - prev > 20:
                    break
                y_bot = max(y_bot, cur + 12)
            col_spans = [s for s in free if y_top <= s.y0 <= y_bot]
            left_l = group_lines([s for s in col_spans if s.x0 < 280])
            right_l = group_lines([s for s in col_spans if s.x0 >= 280])
            lt, lb = split_title(left_l, 11)
            rt, rb = split_title(right_l, 11)
            outs.append(Out(y_top, f":::columns\n### {lt}\n{lb}\n\n### {rt}\n{rb}\n:::", "columns"))
            free = [s for s in free if s not in col_spans]

        outs.extend(body_blocks(group_lines(free)))
        for reg in regions:
            if reg.kind == "coverbox":
                continue
            blk = region_block(reg, pm)
            if blk:
                outs.append(blk)
        for i, im in enumerate(pm.images):
            name = next(assets_dir.glob(f"p{n:02d}_{i + 1}.*"), None)
            if name is None:
                continue
            rel = Path(Path(name).resolve()).relative_to(md_dir.resolve()) if Path(name).resolve().is_relative_to(md_dir.resolve()) else name
            width = round(im.x1 - im.x0, 1)
            outs.append(Out(im.y0, f"![Figure]({rel.as_posix()}){{width={width}}}", "image"))

        # ruled headings: a grey rule right above a heading (continuation pages)
        kicker_tops = [s.y0 for s in pm.spans if s.bold and abs(s.size - 8) < 0.3 and s.color == PURPLE]
        for rule in rule_lines:
            if any(0 < rule.y0 - k < 35 for k in kicker_tops):
                continue
            for o in outs:
                if o.kind == "heading" and 0 < o.y - rule.y0 < 25 and "{.ruled" not in o.md:
                    o.md += " {.ruled}"

        outs.sort(key=lambda o: o.y)
        if not outs:
            continue
        first = outs[0]
        if not has_kicker and prev_bottom < 640 and first.kind in {"heading"}:
            chunks.append("<!-- pagebreak -->")
        elif chunks and not has_kicker and first.kind == "para" and prev_kind == "para":
            # paragraph continued from the previous page
            chunks[-1] = chunks[-1] + " " + first.md
            outs = outs[1:]
        elif chunks and not has_kicker and first.kind == "list" and prev_kind == "list":
            chunks[-1] = chunks[-1] + "\n" + first.md
            outs = outs[1:]
        chunks.extend(o.md for o in outs)
        last_bottom = max([s.y1 for s in pm.spans] + [im.y1 for im in pm.images] + [r.y1 for r in regions] + [0])
        prev_bottom = last_bottom
        prev_kind = outs[-1].kind if outs else prev_kind
    doc.close()
    return "\n\n".join(chunks).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="importer", description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--assets", required=True)
    args = ap.parse_args(argv)
    out = Path(args.output)
    md = import_pdf(Path(args.pdf), Path(args.assets), out.parent)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"wrote {out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
