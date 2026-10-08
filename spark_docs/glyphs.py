"""Keep PDF text inside the font's cmap so missing glyphs never draw as boxes.

Symbols the body font lacks are rewritten to a character it does have. Anything else is
dropped and reported. Arabic letters are left as-is and flagged, because correct joining
needs arabic-reshaper and python-bidi.
"""

from __future__ import annotations

from reportlab.pdfbase import pdfmetrics

from .fonts import register_fonts
from .theme import FONT

# Only characters confirmed missing from Arial. Arrows, dashes, and bullets are already in the font.
FALLBACKS = {
    "\u2713": "\u221a",  # ✓ -> √
    "\u2714": "\u221a",
    "\u2717": "\u00d7",  # ✗ -> ×
    "\u2718": "\u00d7",
    "\u2715": "\u00d7",
    "\u20c3": "AED",  # dirham sign, when a font has no glyph for it
    # keyboard keys in guides and shortcut tables
    "\u23ce": "Enter",
    "\u21b5": "Enter",
    "\u21e7": "Shift",
    "\u2318": "Cmd",
    "\u2325": "Option",
    "\u2303": "Ctrl",
    "\u232b": "Backspace",
    "\u2326": "Delete",
    "\u238b": "Esc",
    "\u21e5": "Tab",
    "\u229e": "Win",
    "\u2316": "+",
    "\u3008": "\u2039",  # 〈 -> ‹
    "\u3009": "\u203a",  # 〉 -> ›
}

_face = None
_face_ready = False
_arabic = False
_missing: list[str] = []
_seen: set[str] = set()


def reset() -> None:
    global _arabic
    _arabic = False
    _missing.clear()
    _seen.clear()


def problems() -> list[str]:
    out: list[str] = []
    if _arabic:
        out.append("Arabic text is not shaped; install arabic-reshaper and python-bidi for correct joining")
    out.extend(_missing)
    return out


def _cmap():
    global _face, _face_ready
    if _face_ready:
        return _face
    register_fonts()
    font = pdfmetrics.getFont(FONT)
    face = getattr(font, "face", None)
    _face = face if face is not None and hasattr(face, "charToGlyph") else None
    _face_ready = True
    return _face


def _has(ch: str, face) -> bool:
    if face is None:
        return ord(ch) < 256
    return face.charToGlyph.get(ord(ch)) is not None


def safe(text: str) -> str:
    """Return text that the body font can draw."""
    if not text:
        return ""
    global _arabic
    face = _cmap()
    out: list[str] = []
    for ch in text:
        if ch in "\n\t\r" or _has(ch, face):
            if "\u0600" <= ch <= "\u06FF" or "\u0750" <= ch <= "\u077F":
                _arabic = True
            out.append(ch)
            continue
        if "\u0600" <= ch <= "\u06FF" or "\u0750" <= ch <= "\u077F":
            _arabic = True
            out.append(ch)
            continue
        repl = FALLBACKS.get(ch)
        if repl and all(_has(c, face) for c in repl):
            out.append(repl)
            continue
        if ch not in _seen:
            _seen.add(ch)
            _missing.append(f"missing glyph U+{ord(ch):04X}")
    return "".join(out)
