"""Compare a render against the reference PDF: text lines, baselines, boxes, and pixels.

    py -m spark_docs.conformance "sample pdfs/ADICC_Proposal_updated (1).pdf" tests/golden/out/adicc.pdf --out tests/golden/out/report
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .measure import PageMeasure, measure_pdf, render_pages


@dataclass
class TextLine:
    text: str
    x0: float
    base: float
    size: float
    font: str
    color: str


def norm(text: str) -> str:
    t = text.replace("\u200b", "").replace("\u00a0", " ").replace("’", "'")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def page_lines(pm: PageMeasure) -> list[TextLine]:
    lines: list[list] = []
    for s in sorted(pm.spans, key=lambda s: (round(s.base, 0), s.x0)):
        for ln in lines:
            if abs(ln[0].base - s.base) < 1.2 and (s.x0 - ln[-1].x1) < 12 and s.x0 >= ln[0].x0 - 2:
                ln.append(s)
                break
        else:
            lines.append([s])
    out = []
    for ln in lines:
        ln.sort(key=lambda s: s.x0)
        text = ""
        for i, s in enumerate(ln):
            if i and s.x0 - ln[i - 1].x1 > 1.5 and not text.endswith(" "):
                text += " "
            text += s.text
        t = norm(text)
        if t in {"•", "●"} or not t:
            continue
        first = next((s for s in ln if norm(s.text) not in {"•", "●", ""}), ln[0])
        out.append(TextLine(t.lstrip("•● ").strip(), round(first.x0, 1), round(first.base, 2), first.size, first.font, first.color))
    return out


@dataclass
class PageReport:
    ref_page: int
    cand_page: int | None
    ref_lines: int
    matched: int
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    max_dy: float = 0.0
    mean_dy: float = 0.0
    max_dx: float = 0.0
    style_diffs: list[str] = field(default_factory=list)
    ssim: float | None = None

    @property
    def match_ratio(self) -> float:
        return self.matched / self.ref_lines if self.ref_lines else 1.0


def overlap(a: list[TextLine], b: list[TextLine]) -> int:
    sa = {ln.text for ln in a}
    return sum(1 for ln in b if ln.text in sa)


def align_pages(ref: list[list[TextLine]], cand: list[list[TextLine]]) -> list[int | None]:
    mapping: list[int | None] = []
    j0 = 0
    for i, rl in enumerate(ref):
        best, best_j = 0, None
        for j in range(max(0, j0 - 1), min(len(cand), j0 + 4)):
            ov = overlap(rl, cand[j])
            if ov > best:
                best, best_j = ov, j
        if best_j is None and i < len(cand):
            best_j = i if not rl else None
        mapping.append(best_j)
        if best_j is not None:
            j0 = best_j + 1
    return mapping


def compare_page(ri: int, rl: list[TextLine], ci: int | None, cl: list[TextLine]) -> PageReport:
    rep = PageReport(ri + 1, None if ci is None else ci + 1, len(rl), 0)
    pool = list(cl)
    dys, dxs = [], []
    for ln in rl:
        hit = next((c for c in pool if c.text == ln.text), None)
        if hit is None:
            rep.missing.append(ln.text[:90])
            continue
        pool.remove(hit)
        rep.matched += 1
        dys.append(abs(hit.base - ln.base))
        dxs.append(abs(hit.x0 - ln.x0))
        if abs(hit.size - ln.size) > 0.2 or hit.color.upper() != ln.color.upper() or ("Bold" in hit.font) != ("Bold" in ln.font):
            rep.style_diffs.append(f"{ln.text[:40]!r}: ref {ln.size}/{ln.color}/{ln.font} vs {hit.size}/{hit.color}/{hit.font}")
    rep.extra = [c.text[:90] for c in pool]
    if dys:
        rep.max_dy = round(max(dys), 2)
        rep.mean_dy = round(sum(dys) / len(dys), 2)
        rep.max_dx = round(max(dxs), 2)
    return rep


def ssim_pages(ref_png: Path, cand_png: Path) -> float | None:
    try:
        import numpy as np
        from PIL import Image
        from skimage.metrics import structural_similarity
    except ImportError:
        return None
    a = np.asarray(Image.open(ref_png).convert("L"), dtype=float)
    b = np.asarray(Image.open(cand_png).convert("L").resize((a.shape[1], a.shape[0])), dtype=float)
    return round(float(structural_similarity(a, b, data_range=255)), 4)


def side_by_side(ref_png: Path, cand_png: Path, out: Path) -> None:
    from PIL import Image, ImageChops, ImageDraw

    a = Image.open(ref_png).convert("RGB")
    b = Image.open(cand_png).convert("RGB").resize(a.size)
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 40 else 0)
    overlay = a.copy()
    red = Image.new("RGB", a.size, (230, 40, 40))
    overlay.paste(red, mask=diff)
    canvas = Image.new("RGB", (a.width * 3 + 20, a.height + 24), "white")
    canvas.paste(a, (0, 24))
    canvas.paste(b, (a.width + 10, 24))
    canvas.paste(overlay, (2 * a.width + 20, 24))
    d = ImageDraw.Draw(canvas)
    d.text((6, 4), "reference", fill=(0, 0, 0))
    d.text((a.width + 16, 4), "ours", fill=(0, 0, 0))
    d.text((2 * a.width + 26, 4), "differences (red)", fill=(0, 0, 0))
    canvas.save(out)


def compare(ref_pdf: str | Path, cand_pdf: str | Path, out_dir: str | Path | None = None, dpi: int = 72) -> list[PageReport]:
    ref = measure_pdf(ref_pdf)
    cand = measure_pdf(cand_pdf)
    rl = [page_lines(p) for p in ref]
    cl = [page_lines(p) for p in cand]
    mapping = align_pages(rl, cl)
    reports = [compare_page(i, rl[i], mapping[i], cl[mapping[i]] if mapping[i] is not None else []) for i in range(len(rl))]
    if out_dir:
        out = Path(out_dir)
        rp = out / "ref"
        cp = out / "ours"
        render_pages(ref_pdf, rp, dpi)
        render_pages(cand_pdf, cp, dpi)
        for rep in reports:
            if rep.cand_page is None:
                continue
            a = rp / f"p{rep.ref_page:02d}.png"
            b = cp / f"p{rep.cand_page:02d}.png"
            rep.ssim = ssim_pages(a, b)
            side_by_side(a, b, out / f"compare_p{rep.ref_page:02d}.png")
        (out / "report.json").write_text(json.dumps([asdict(r) for r in reports], indent=1), encoding="utf-8")
    return reports


def summary(reports: list[PageReport], cand_pages: int | None = None) -> str:
    lines = []
    total = sum(r.ref_lines for r in reports)
    matched = sum(r.matched for r in reports)
    lines.append(f"text lines matched: {matched}/{total} ({100 * matched / max(total, 1):.1f}%)")
    if cand_pages is not None:
        lines.append(f"pages: reference {len(reports)}, ours {cand_pages}")
    lines.append(" page  ours  lines  match   max_dy  mean_dy  max_dx   ssim")
    for r in reports:
        lines.append(
            f" {r.ref_page:4d}  {r.cand_page if r.cand_page else '-':>4}  {r.ref_lines:5d}  {100 * r.match_ratio:5.1f}%  {r.max_dy:7.2f}  {r.mean_dy:7.2f}  {r.max_dx:6.2f}  {r.ssim if r.ssim is not None else '':>6}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="conformance", description=__doc__)
    ap.add_argument("reference")
    ap.add_argument("candidate")
    ap.add_argument("--out", help="folder for side-by-side PNGs and report.json")
    ap.add_argument("--details", action="store_true", help="print missing/extra lines per page")
    args = ap.parse_args(argv)
    reports = compare(args.reference, args.candidate, args.out)
    import pymupdf

    with pymupdf.open(args.candidate) as d:
        n = len(d)
    print(summary(reports, n))
    if args.details:
        for r in reports:
            if r.missing or r.extra or r.style_diffs:
                print(f"\n--- page {r.ref_page} (ours {r.cand_page})")
                for t in r.missing[:12]:
                    print(f"  - ref only: {t}")
                for t in r.extra[:12]:
                    print(f"  + ours only: {t}")
                for t in r.style_diffs[:6]:
                    print(f"  ~ style: {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
