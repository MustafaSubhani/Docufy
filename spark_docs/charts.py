"""Flat charts in the Spark palette.

Bars and lines are solid fills. The page stays white. Colours come from the
theme: navy, purple, and a quiet grid. Pillow draws them, so no extra chart
package is required.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from .fonts import WIN_FONTS

ROOT = Path(__file__).resolve().parent.parent
CHART_DIR = ROOT / "workspace" / "charts"
NAVY = (26, 26, 46)
PURPLE = (107, 78, 255)
GRID = (226, 232, 240)
MUTED = (100, 116, 139)
WHITE = (255, 255, 255)
SERIES = (PURPLE, NAVY, (30, 41, 59))
WIDTH = 680
HEIGHT = 420


class ChartError(ValueError):
    pass


def render_chart(spec: dict, dest: Path | None = None) -> Path:
    kind = str(spec.get("kind") or "bar").lower()
    if kind not in {"bar", "line"}:
        raise ChartError("a chart is either bar or line")
    categories = [str(item) for item in spec.get("categories") or []]
    series = _series(spec, len(categories))
    if not categories or not series:
        raise ChartError("a chart needs categories and at least one value")
    target = dest or _cached_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    font = _font(15)
    small = _font(12)
    title = str(spec.get("title") or "").strip()
    top = 36
    if title:
        draw.text((36, 16), title, fill=NAVY, font=font)
        top = 52
    plot = (48, top, WIDTH - 24, HEIGHT - 46)
    values = [number for item in series for number in item["values"]]
    low = min(0, min(values))
    high = max(0, max(values))
    if high == low:
        high = low + 1
    _grid(draw, plot, low, high, small)
    if kind == "line":
        _lines(draw, plot, categories, series, low, high)
    else:
        _bars(draw, plot, categories, series, low, high)
    _labels(draw, plot, categories, small)
    image.save(target, "PNG")
    return target


def render_chart_source(source: str, dest: Path | None = None) -> Path | None:
    try:
        data = yaml.safe_load(source) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return render_chart(data, dest)
    except ChartError:
        return None


def _series(spec: dict, count: int) -> list[dict]:
    raw = spec.get("series")
    if not raw:
        values = [_number(item) for item in spec.get("values") or []]
        raw = [{"name": spec.get("unit") or "Value", "values": values}]
    series = []
    for item in raw:
        values = [_number(number) for number in item.get("values") or []]
        if count and len(values) != count:
            raise ChartError("each series needs one value per category")
        series.append({"name": str(item.get("name") or "Value"), "values": values})
    return series


def _bars(draw, plot, categories, series, low, high) -> None:
    left, top, right, bottom = plot
    span = right - left
    group = span / max(1, len(categories))
    gap = min(18, group * 0.18)
    inner = group - gap
    width = inner / len(series)
    for index, category_values in enumerate(zip(*(item["values"] for item in series), strict=True)):
        origin = left + index * group + gap / 2
        for series_index, value in enumerate(category_values):
            x0 = origin + series_index * width
            y0 = _y(plot, value, low, high)
            y1 = _y(plot, 0, low, high)
            box = [x0 + 1, min(y0, y1), x0 + width - 2, max(y0, y1)]
            if box[3] - box[1] < 1:
                box[1] -= 1
            draw.rectangle(box, fill=SERIES[series_index % len(SERIES)])


def _lines(draw, plot, categories, series, low, high) -> None:
    left, _top, right, _bottom = plot
    span = right - left
    step = span / max(1, len(categories) - 1)
    for series_index, item in enumerate(series):
        color = SERIES[series_index % len(SERIES)]
        points = []
        for index, value in enumerate(item["values"]):
            x = left if len(categories) == 1 else left + index * step
            points.append((int(x), int(_y(plot, value, low, high))))
        for start, end in zip(points, points[1:], strict=False):
            for x, y in _walk(start, end):
                draw.rectangle([x - 1, y - 1, x + 1, y + 1], fill=color)
        for x, y in points:
            draw.rectangle([x - 2, y - 2, x + 2, y + 2], fill=color)


def _walk(start: tuple[int, int], end: tuple[int, int]):
    x0, y0 = start
    x1, y1 = end
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for step in range(steps + 1):
        yield round(x0 + (x1 - x0) * step / steps), round(y0 + (y1 - y0) * step / steps)


def _grid(draw, plot, low, high, font) -> None:
    left, top, right, bottom = plot
    draw.line([(left, top), (left, bottom), (right, bottom)], fill=NAVY, width=1)
    for step in range(4):
        value = low + (high - low) * step / 3
        y = _y(plot, value, low, high)
        draw.line([(left, y), (right, y)], fill=GRID, width=1)
        draw.text((8, y - 6), _tick(value), fill=MUTED, font=font)


def _labels(draw, plot, categories, font) -> None:
    left, _top, right, bottom = plot
    span = right - left
    group = span / max(1, len(categories))
    for index, label in enumerate(categories):
        x = left + index * group + group / 2
        draw.text((x - 8, bottom + 8), str(label)[:12], fill=NAVY, font=font)


def _y(plot, value, low, high) -> float:
    _left, top, _right, bottom = plot
    return bottom - (value - low) / (high - low) * (bottom - top)


def _tick(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ChartError("chart values must be numbers") from exc


def _font(size: int):
    path = WIN_FONTS / "arial.ttf"
    if path.is_file():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _cached_path(spec: dict) -> Path:
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]
    return CHART_DIR / f"{digest}.png"
