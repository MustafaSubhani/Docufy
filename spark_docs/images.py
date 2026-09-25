"""Resolve markdown image paths and rasterize SVG for PDF/DOCX."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
RASTER_CACHE = ROOT / "assets" / "raster_cache"


def resolve_path(path: str, source_dir: Path, asset_roots: list[Path] | None = None) -> str:
    raw = unquote(path.strip().strip('"').strip("'"))
    rel = Path(raw)
    if rel.is_file():
        return str(rel.resolve())

    search_roots: list[Path] = []
    for ar in asset_roots or []:
        try:
            search_roots.append(ar.resolve())
        except OSError:
            continue
    search_roots.extend(
        [
            source_dir,
            source_dir.parent,
            ROOT / "input",
            ROOT,
            ROOT.parent,
        ]
    )
    adicc = ROOT.parent / "Adicc"
    if adicc.is_dir():
        search_roots.extend([adicc / "docs", adicc])
    seen: set[Path] = set()
    for root in search_roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen:
            continue
        seen.add(root)
        candidate = (root / rel).resolve()
        if candidate.is_file():
            return str(candidate)

    name = rel.name
    if name:
        subdirs = ("wall-report", "assets/wall-report", "assets")
        for root in search_roots:
            try:
                root = root.resolve()
            except OSError:
                continue
            for sub in subdirs:
                candidate = (root / sub / name).resolve()
                if candidate.is_file():
                    return str(candidate)

    return str((source_dir / rel).resolve() if not rel.is_absolute() else rel)


def raster_path_for_pdf(path: str | Path) -> str:
    """Return a PNG/JPEG path ReportLab can embed; rasterize SVG via PyMuPDF."""
    p = Path(path)
    if not p.is_file():
        return str(p)
    ext = p.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return str(p.resolve())
    if ext != ".svg":
        return str(p.resolve())

    RASTER_CACHE.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(p.read_bytes()).hexdigest()[:16]
    out = RASTER_CACHE / f"{digest}.png"
    if out.is_file() and out.stat().st_mtime >= p.stat().st_mtime:
        return str(out)

    try:
        import fitz

        doc = fitz.open(str(p))
        page = doc[0]
        rect = page.rect
        scale = max(2.0, min(2400 / max(rect.width, 1), 2400 / max(rect.height, 1)))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pix.save(str(out))
        doc.close()
        return str(out)
    except Exception:
        return str(p)
