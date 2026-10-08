"""Claude visualizer design language (ramps sampled/verified against the ADICC diagrams).

Units are canvas px on a 680-wide canvas, like Claude's SVG widgets. The PDF scale is applied later.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"

# Claude draws diagrams in the rendering machine's system sans: Segoe UI on Windows (ADICC pages 6-12),
# DejaVu Sans on Linux (pages 14, 24, 27). "auto" prefers Segoe UI when installed.
FONT_CHOICES = {
    "segoe": (Path(r"C:\Windows\Fonts\segoeui.ttf"), Path(r"C:\Windows\Fonts\segoeuib.ttf")),
    "dejavu": (FONT_DIR / "DejaVuSans.ttf", FONT_DIR / "DejaVuSans-Bold.ttf"),
}


def pick_font(choice: str = "auto") -> tuple[Path, Path]:
    if choice in FONT_CHOICES and FONT_CHOICES[choice][0].is_file():
        return FONT_CHOICES[choice]
    for key in ("segoe", "dejavu"):
        if FONT_CHOICES[key][0].is_file():
            return FONT_CHOICES[key]
    raise FileNotFoundError("no diagram font available (run npm install in diagrams/node_worker)")


FONT_REGULAR, FONT_BOLD = pick_font()

# stops: 50 100 200 400 600 800 900
RAMPS: dict[str, list[str]] = {
    "purple": ["#EEEDFE", "#CECBF6", "#AFA9EC", "#7F77DD", "#534AB7", "#3C3489", "#26215C"],
    "teal": ["#E1F5EE", "#9FE1CB", "#5DCAA5", "#1D9E75", "#0F6E56", "#085041", "#04342C"],
    "blue": ["#E6F1FB", "#B5D4F4", "#85B7EB", "#378ADD", "#185FA5", "#0C447C", "#042C53"],
    "gray": ["#F1EFE8", "#D3D1C7", "#B4B2A9", "#888780", "#5F5E5A", "#444441", "#2C2C2A"],
    "amber": ["#FAEEDA", "#FAC775", "#EF9F27", "#BA7517", "#854F0B", "#633806", "#412402"],
    "coral": ["#FAECE7", "#F5C4B3", "#F0997B", "#D85A30", "#993C1D", "#712B13", "#4A1B0C"],
    "green": ["#EAF3DE", "#C0DD97", "#97C459", "#639922", "#3B6D11", "#27500A", "#173404"],
    "red": ["#FCEBEB", "#F7C1C1", "#F09595", "#E24B4A", "#A32D2D", "#791F1F", "#501313"],
    "pink": ["#FBEAF0", "#F4C0D1", "#ED93B1", "#D4537E", "#993556", "#72243E", "#4B1528"],
}
STOP = {"50": 0, "100": 1, "200": 2, "400": 3, "600": 4, "800": 5, "900": 6}

# semantic categories -> ramp (default mapping, matches the ADICC diagrams)
CATEGORY_RAMP = {
    "input": "blue",
    "ingest": "blue",
    "foundation": "blue",
    "source": "blue",
    "ai": "purple",
    "process": "purple",
    "processing": "purple",
    "analysis": "purple",
    "output": "teal",
    "result": "teal",
    "cost": "teal",
    "platform": "gray",
    "neutral": "gray",
    "capability": "gray",
    "human": "amber",
    "manual": "amber",
    "validation": "amber",
    "warning": "amber",
    "risk": "coral",
    "error": "red",
    "approval": "green",
    "success": "green",
}

CANVAS_W = 680
MARGIN = 40
TITLE_SIZE = 14
SUB_SIZE = 12
LABEL_SIZE = 12
NODE_H1 = 40          # single-line node
NODE_H2 = 56          # title + subtitle
NODE_PAD_X = 12
NODE_MIN_W = 96
NODE_MAX_TEXT_W = 190  # wrap titles beyond this
RADIUS = 8
STROKE_W = 0.5
EDGE_W = 2.0
ARROW = 9.0            # arrowhead length
HEADER_SIZE = 13       # lane labels above a column ("Inputs", "Module 1")
HEADER_COLOR = "#A3A29C"
BAND_GAP = 28          # extra air above a full-width band so a merge arrow fits
MAX_SCENE_H = 700      # taller than this, a top-to-bottom diagram is tried left-to-right
GRID_COL_GAP = 30
GRID_ROW_GAP = 14
GRID_MARGIN = 16
LEGEND_SIZE = 13
LEGEND_COLOR = "#444441"
NODE_GAP = 24
LAYER_GAP = 40
GROUP_PAD = 16
GROUP_LABEL_H = 22
LEGEND_GAP = 24
LEGEND_SWATCH = 12
TEXT_DARK = "#2C2C2A"   # gray 900 for group titles
TEXT_MUTED = "#5F5E5A"  # gray 600 for labels and legend


def ramp(name: str) -> list[str]:
    return RAMPS.get(name, RAMPS["gray"])


def resolve_ramp(category: str | None) -> str:
    if not category:
        return "gray"
    c = category.lower().strip()
    if c in RAMPS:
        return c
    return CATEGORY_RAMP.get(c, "gray")


def push_font(choice: str | None):
    """Measure and draw with a specific face. Returns the previous paths, or None if unchanged."""
    global FONT_REGULAR, FONT_BOLD
    if not choice or choice == "auto":
        return None
    paths = pick_font(choice)
    if paths == (FONT_REGULAR, FONT_BOLD):
        return None
    previous = (FONT_REGULAR, FONT_BOLD)
    FONT_REGULAR, FONT_BOLD = paths
    from .scene import font as measure_font

    measure_font.cache_clear()
    return previous


def pop_font(token) -> None:
    global FONT_REGULAR, FONT_BOLD
    if not token:
        return
    FONT_REGULAR, FONT_BOLD = token
    from .scene import font as measure_font

    measure_font.cache_clear()
