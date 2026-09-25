"""Measure a PDF: text spans, vector drawings, and images per page (PyMuPDF).

Used to extract the template spec from the reference PDF and to check renders against it.

    py -m spark_docs.measure "sample pdfs/ADICC_Proposal_updated (1).pdf" --pages 1-3
    py -m spark_docs.measure out.pdf --json out.json --png out_pages/
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf


@dataclass
class Span:
    text: str
    font: str
    size: float
    color: str
    x0: float
    y0: float
    x1: float
    y1: float
    base: float = 0.0  # baseline y (top-down); comparable across font embeddings

    @property
    def bold(self) -> bool:
        return "Bold" in self.font

    @property
    def italic(self) -> bool:
        return "Italic" in self.font or "Oblique" in self.font


@dataclass
class Shape:
    kind: str  # "rect" | "line" | "path"
    fill: str | None
    stroke: str | None
    width: float | None
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Img:
    x0: float
    y0: float
    x1: float
    y1: float
    px_w: int
    px_h: int


@dataclass
class PageMeasure:
    number: int
    width: float
    height: float
    spans: list[Span] = field(default_factory=list)
    shapes: list[Shape] = field(default_factory=list)
    images: list[Img] = field(default_factory=list)

    def text(self) -> str:
        return " ".join(s.text for s in self.spans)

    def spans_like(self, *, size: float | None = None, color: str | None = None, bold: bool | None = None):
        out = []
        for s in self.spans:
            if size is not None and abs(s.size - size) > 0.15:
                continue
            if color is not None and s.color.upper() != color.upper():
                continue
            if bold is not None and s.bold != bold:
                continue
            out.append(s)
        return out

    def fills(self, color: str) -> list[Shape]:
        return [s for s in self.shapes if s.fill and s.fill.upper() == color.upper()]


def hex_color(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return f"#{value:06X}"
    if isinstance(value, (tuple, list)):
        if len(value) == 1:
            value = (value[0],) * 3
        if len(value) == 4:  # CMYK
            c, m, y, k = value
            value = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
        return "#" + "".join(f"{round(v * 255):02X}" for v in value[:3])
    return None


def measure_page(page: pymupdf.Page) -> PageMeasure:
    pm = PageMeasure(number=page.number + 1, width=page.rect.width, height=page.rect.height)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for s in line["spans"]:
                if not s["text"].strip():
                    continue
                x0, y0, x1, y1 = s["bbox"]
                pm.spans.append(
                    Span(
                        text=s["text"],
                        font=s["font"].replace("MT", ""),
                        size=round(s["size"], 2),
                        color=hex_color(s["color"]) or "#000000",
                        x0=round(x0, 2),
                        y0=round(y0, 2),
                        x1=round(x1, 2),
                        y1=round(y1, 2),
                        base=round(s["origin"][1], 2),
                    )
                )
    for d in page.get_drawings():
        r = d["rect"]
        if r.width >= page.rect.width - 1 and r.height >= page.rect.height - 1 and d.get("fill") and hex_color(d["fill"]) == "#FFFFFF":
            continue  # page background
        items = [it[0] for it in d["items"]]
        if items == ["l"] or (r.height < 0.01 or r.width < 0.01) and "l" in items:
            kind = "line"
        elif items and all(i == "re" for i in items) or items == ["qu"]:
            kind = "rect"
        else:
            kind = "path"
        pm.shapes.append(
            Shape(
                kind=kind,
                fill=hex_color(d.get("fill")),
                stroke=hex_color(d.get("color")),
                width=d.get("width"),
                x0=round(r.x0, 2),
                y0=round(r.y0, 2),
                x1=round(r.x1, 2),
                y1=round(r.y1, 2),
            )
        )
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        pm.images.append(Img(round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2), info["width"], info["height"]))
    return pm


def measure_pdf(path: str | Path, pages: list[int] | None = None) -> list[PageMeasure]:
    doc = pymupdf.open(str(path))
    try:
        numbers = pages or list(range(1, len(doc) + 1))
        return [measure_page(doc[n - 1]) for n in numbers if 1 <= n <= len(doc)]
    finally:
        doc.close()


def render_pages(path: str | Path, out_dir: str | Path, dpi: int = 100, pages: list[int] | None = None) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(path))
    written: list[Path] = []
    try:
        numbers = pages or list(range(1, len(doc) + 1))
        for n in numbers:
            if not 1 <= n <= len(doc):
                continue
            pix = doc[n - 1].get_pixmap(dpi=dpi, alpha=False)
            target = out_dir / f"p{n:02d}.png"
            pix.save(str(target))
            written.append(target)
    finally:
        doc.close()
    return written


def parse_pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def summarize(pm: PageMeasure) -> str:
    lines = [f"=== page {pm.number} ({pm.width:.0f}x{pm.height:.0f})"]
    for s in pm.spans:
        lines.append(
            f"T {s.x0:6.1f} b{s.base:6.1f} {s.x1:6.1f}  {s.font:<16} {s.size:5.1f} {s.color}  {s.text[:60]!r}"
        )
    for d in pm.shapes:
        lines.append(
            f"D {d.kind:<4} {d.x0:6.1f} {d.y0:6.1f} {d.x1:6.1f} {d.y1:6.1f}  fill={d.fill} stroke={d.stroke} w={d.width}"
        )
    for im in pm.images:
        lines.append(f"I {im.x0:6.1f} {im.y0:6.1f} {im.x1:6.1f} {im.y1:6.1f}  {im.px_w}x{im.px_h}px")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="measure", description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="e.g. 1-3,7")
    ap.add_argument("--json", help="write measurements as JSON")
    ap.add_argument("--png", help="render pages as PNG into this folder")
    ap.add_argument("--dpi", type=int, default=100)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    pages = parse_pages(args.pages)
    measured = measure_pdf(args.pdf, pages)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps([asdict(p) for p in measured], indent=1), encoding="utf-8")
    if args.png:
        render_pages(args.pdf, args.png, args.dpi, pages)
    if not args.quiet:
        for pm in measured:
            print(summarize(pm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
