"""Print element transitions (style changes, boxes, images) with baseline distances for reference pages."""

import sys

from spark_docs.measure import measure_pdf

REF = "sample pdfs/ADICC_Proposal_updated (1).pdf"


def style(s):
    return f"{'B' if s.bold else 'R'}{s.size:g}{s.color}"


def main(pages):
    for pm in measure_pdf(REF, pages):
        items = []
        for s in pm.spans:
            items.append(("T", s.base, s.base, style(s), s.text[:28], s.x0))
        for d in pm.shapes:
            if d.kind == "rect" and d.fill and d.fill != "#FFFFFF":
                items.append(("R", d.y0, d.y1, d.fill, f"{d.x0:.0f}-{d.x1:.0f}", d.x0))
            if d.kind == "line" and d.x1 - d.x0 > 300:
                items.append(("L", d.y0, d.y1, d.stroke, "rule", d.x0))
        for im in pm.images:
            items.append(("I", im.y0, im.y1, "img", f"{im.x0:.0f}-{im.x1:.0f}", im.x0))
        items.sort(key=lambda t: (t[1], t[5]))
        print(f"=== page {pm.number}")
        prev = None
        for it in items:
            if prev is None or it[3] != prev[3] or it[0] != "T":
                delta = it[1] - prev[2] if prev else 0
                print(f"  {it[0]} {it[1]:7.2f}-{it[2]:7.2f} d={delta:6.2f}  {it[3]:<14} x={it[5]:5.1f} {it[4]!r}")
            prev = it


if __name__ == "__main__":
    main([int(p) for p in sys.argv[1:]])
