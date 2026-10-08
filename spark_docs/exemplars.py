"""Crop component exemplars from the ADICC reference PDF.

The crops live in ``themes/spark/exemplars`` and are what visual checks compare against.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import yaml

from .theme import ROOT

REFERENCE = ROOT / "sample pdfs" / "ADICC_Proposal_updated (1).pdf"
OUT = ROOT / "themes" / "spark" / "exemplars"

SPECS = [
    {"id": "cover", "page": 1, "rect": (36, 28, 560, 800)},
    {"id": "section_start", "page": 2, "queries": ["Executive Summary"], "pad": (42, 56)},
    {"id": "columns", "page": 2, "queries": ["The Challenge", "outdated information"], "pad": (14, 16)},
    {"id": "callout", "page": 2, "queries": ["Our Approach"], "pad": (12, 96)},
    {"id": "cards", "page": 3, "queries": ["SME Outcome as a Service", "Funnel Flow"], "pad": (16, 28)},
    {"id": "steps", "page": 20, "queries": ["How We Move Forward", "ADICC technical teams"], "pad": (20, 14)},
    {"id": "roadmap", "page": 16, "queries": ["Deliverables", "Week 13-14"], "pad": (16, 18)},
    {"id": "commercial", "page": 18, "queries": ["Commercial Framework", "TOTAL INVESTMENT"], "pad": (14, 20)},
    {"id": "kpis", "page": 17, "queries": ["generation time reduced", "manual re-entry"], "pad": (70, 4)},
    {
        "id": "why_choose_us",
        "page": 34,
        "queries": ["Why Choose Us", "Data Security"],
        "pad": (28, 70),
    },
    {"id": "contact", "page": 20, "queries": ["+971 56 956 8061"], "pad": (22, 16), "x0": 55, "x1": 520},
]


def export_exemplars(pdf: Path | None = None, out_dir: Path | None = None, dpi: int = 120) -> Path:
    pdf = Path(pdf or REFERENCE)
    out_dir = Path(out_dir or OUT)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf))
    crops = []
    try:
        for spec in SPECS:
            page = doc[spec["page"] - 1]
            clip = _clip(page, spec)
            dest = out_dir / f"{spec['id']}.png"
            page.get_pixmap(dpi=dpi, clip=clip, alpha=False).save(str(dest))
            crops.append(
                {
                    "id": spec["id"],
                    "file": dest.name,
                    "page": spec["page"],
                    "rect": [round(v, 1) for v in (clip.x0, clip.y0, clip.x1, clip.y1)],
                }
            )
    finally:
        doc.close()
    manifest = {"source": str(pdf.relative_to(ROOT)) if pdf.is_relative_to(ROOT) else str(pdf), "dpi": dpi, "crops": crops}
    (out_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return out_dir


def _clip(page, spec: dict):
    if spec.get("rect"):
        rect = pymupdf.Rect(*spec["rect"])
    else:
        rects = []
        for query in spec["queries"]:
            rects.extend(page.search_for(query))
        if not rects:
            raise RuntimeError(f"no match for {spec['id']} on page {spec['page']}")
        rect = rects[0]
        for other in rects[1:]:
            rect |= other
        top, bottom = spec.get("pad", (8, 8))
        rect = pymupdf.Rect(
            spec.get("x0", 48),
            rect.y0 - top,
            spec.get("x1", page.rect.width - 48),
            rect.y1 + bottom,
        )
    return rect & page.rect
