"""Register Arial (and fallbacks) with ReportLab."""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

from .theme import FONT, FONT_BOLD, FONT_BOLD_ITALIC, FONT_ITALIC

_REGISTERED = False

WIN_FONTS = Path(r"C:\Windows\Fonts")
CANDIDATES = {
    FONT: ["arial.ttf", "Arial.ttf", "arialuni.ttf"],
    FONT_BOLD: ["arialbd.ttf", "Arialbd.ttf", "Arial-Bold.ttf"],
    FONT_ITALIC: ["ariali.ttf", "Ariali.ttf", "Arial-Italic.ttf"],
    FONT_BOLD_ITALIC: ["arialbi.ttf", "Arialbi.ttf", "Arial-BoldItalic.ttf"],
}


def register_fonts() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    found: dict[str, str] = {}
    for family, names in CANDIDATES.items():
        for name in names:
            path = WIN_FONTS / name
            if path.exists():
                pdfmetrics.registerFont(TTFont(family, str(path)))
                found[family] = family
                break
    if FONT not in found:
        # Helvetica is always available in ReportLab
        found[FONT] = "Helvetica"
        found[FONT_BOLD] = "Helvetica-Bold"
        found[FONT_ITALIC] = "Helvetica-Oblique"
        found[FONT_BOLD_ITALIC] = "Helvetica-BoldOblique"
    else:
        registerFontFamily(
            FONT,
            normal=found.get(FONT, FONT),
            bold=found.get(FONT_BOLD, FONT_BOLD),
            italic=found.get(FONT_ITALIC, FONT_ITALIC),
            boldItalic=found.get(FONT_BOLD_ITALIC, FONT_BOLD_ITALIC),
        )
    _REGISTERED = True
