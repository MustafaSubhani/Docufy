"""Shared layout helpers for images, diagrams, and tables."""

from __future__ import annotations

from .theme import CONTENT_W, MARGIN_BOTTOM, MARGIN_TOP, PAGE_H

# Usable vertical space on an inner page (points).
BODY_H = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM - 12

# ReportLab inner frame is slightly narrower than CONTENT_W; stay inside it.
IMAGE_MAX_W = CONTENT_W - 12

# Diagrams should not consume more than this fraction of a page body.
MAX_DIAGRAM_H = min(BODY_H * 0.48, 380)


def fit_image(
    iw_px: float,
    ih_px: float,
    asset_scale: float = 1.0,
    *,
    diagram: bool = False,
    diagram_wide: bool = False,
    content_w: float = CONTENT_W,
    body_h: float = BODY_H,
) -> tuple[float, float]:
    """Return display width and height in PDF points, always preserving aspect ratio."""
    scale = asset_scale if asset_scale > 0 else 1.0
    lw = iw_px / scale
    lh = ih_px / scale
    if lw <= 0 or lh <= 0:
        return content_w * 0.8, content_w * 0.2

    max_h = MAX_DIAGRAM_H if diagram else min(body_h * 0.55, 420)
    max_w = min(content_w, IMAGE_MAX_W)

    if diagram:
        # Fit entirely inside the frame; scale up sharp mermaid PNGs but never clip width or height.
        shrink = min(max_w / lw, max_h / lh)
        w = min(lw * shrink, max_w)
        h = min(lh * shrink, max_h)
        return w, max(h, 1.0)

    shrink = min(max_w / lw, max_h / lh, 1.0)
    w = lw * shrink
    h = lh * shrink
    return w, max(h, 1.0)


def estimate_table_height(row_count: int) -> float:
    return 34 + max(row_count, 1) * 23


def table_keep_together(row_count: int) -> bool:
    return row_count <= 12
