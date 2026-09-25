"""Infer effective line-break widths per text context from the reference line breaks."""

from collections import defaultdict

from reportlab.pdfbase.pdfmetrics import stringWidth

from spark_docs.fonts import register_fonts
from spark_docs.measure import measure_pdf

REF = "sample pdfs/ADICC_Proposal_updated (1).pdf"


def main():
    register_fonts()
    bounds = defaultdict(lambda: [0.0, 1e9, 0])
    for pm in measure_pdf(REF):
        groups = defaultdict(list)
        for s in pm.spans:
            if s.text.strip() in {"•", "●", "•\u200b", "●\u200b"} or not s.text.strip():
                continue
            groups[(round(s.x0, 0), s.size, s.bold)].append(s)
        for (x0, size, bold), spans in groups.items():
            spans.sort(key=lambda s: s.base)
            font = "Arial-Bold" if bold else "Arial"
            pitch = size * 1.15 + 0.6
            for a, b in zip(spans, spans[1:]):
                if not 0 < b.base - a.base <= pitch:
                    continue
                t = a.text.replace("\u200b", "").rstrip()
                words = b.text.split()
                if not t or not words:
                    continue
                w = stringWidth(t, font, size)
                w2 = stringWidth(t + " " + words[0], font, size)
                key = (x0, size, bold)
                lo, hi, n = bounds[key]
                bounds[key] = [max(lo, w), min(hi, w2), n + 1]
    for key in sorted(bounds):
        lo, hi, n = bounds[key]
        if n >= 2:
            flag = "" if lo < hi else "  (inconsistent: mixed widths)"
            print(f"x0={key[0]:6.1f} size={key[1]:4.1f} bold={key[2]!s:5}  {lo:7.2f} <= W < {hi:7.2f}  ({n} breaks){flag}")


if __name__ == "__main__":
    main()
