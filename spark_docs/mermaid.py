"""Render ```mermaid fences to Spark-themed PNG diagrams via mermaid-cli."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .theme import CONTENT_W

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "assets" / "diagrams"

# Render so 1px == 1/ASSET_SCALE pt in the PDF (no upscaling blur at full content width).
RENDER_VERSION = "v5"
ASSET_SCALE = 5
SCALE = str(ASSET_SCALE)

CONFIG = {
    "theme": "base",
    "themeVariables": {
        "fontFamily": "Arial, Helvetica, sans-serif",
        "fontSize": "15px",
        "primaryColor": "#E5EAF1",
        "primaryTextColor": "#1A1A2E",
        "primaryBorderColor": "#6B4EFF",
        "secondaryColor": "#F4F6FA",
        "secondaryTextColor": "#1A1A2E",
        "secondaryBorderColor": "#C8CFDA",
        "tertiaryColor": "#FFFFFF",
        "tertiaryTextColor": "#1A1A2E",
        "tertiaryBorderColor": "#C8CFDA",
        "mainBkg": "#E5EAF1",
        "nodeBorder": "#6B4EFF",
        "textColor": "#1A1A2E",
        "lineColor": "#6B4EFF",
        "edgeLabelBackground": "#FFFFFF",
        "clusterBkg": "#F4F6FA",
        "clusterBorder": "#C8CFDA",
    },
    "flowchart": {
        "curve": "basis",
        "htmlLabels": True,
        "padding": 14,
        "nodeSpacing": 40,
        "rankSpacing": 52,
        "useMaxWidth": True,
    },
}

PUPPETEER = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}

_missing_tool = False


def render(source: str) -> str | None:
    """Return a PNG path for the diagram, or None when it cannot be rendered."""
    global _missing_tool
    text = (source or "").strip()
    if not text:
        return None

    orientation = _orientation(text)
    render_width = _render_width(orientation)
    digest = hashlib.sha1(
        (
            RENDER_VERSION
            + orientation
            + json.dumps(CONFIG, sort_keys=True)
            + SCALE
            + render_width
            + text
        ).encode("utf-8")
    ).hexdigest()[:16]
    out_path = CACHE_DIR / f"{digest}.png"
    if out_path.is_file():
        return str(out_path)
    if _missing_tool:
        return None

    command = _command()
    if command is None:
        _missing_tool = True
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / "diagram.mmd"
        cfg = tmp_dir / "config.json"
        pup = tmp_dir / "puppeteer.json"
        src.write_text(text + "\n", encoding="utf-8")
        cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
        pup.write_text(json.dumps(PUPPETEER), encoding="utf-8")
        args = command + [
            "-i",
            str(src),
            "-o",
            str(out_path),
            "-c",
            str(cfg),
            "-p",
            str(pup),
            "-b",
            "white",
            "-s",
            SCALE,
            "-w",
            render_width,
            "--quiet",
        ]
        env = dict(os.environ, PUPPETEER_SKIP_DOWNLOAD="")
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=300, env=env)
        except (OSError, subprocess.SubprocessError):
            _missing_tool = True
            return None

    if proc.returncode != 0 or not out_path.is_file():
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        if detail:
            print(f"mermaid: {detail[-1]}")
        return None
    return str(out_path)


def diagram_orientation(source: str) -> str:
    return _orientation(source)


def _orientation(source: str) -> str:
    """wide = LR pipelines; tall = TB trees and vertical flows."""
    head = source.strip().split("\n", 1)[0]
    m = re.search(r"(?:flowchart|graph)\s+(LR|RL|TB|TD|BT)\b", head, re.I)
    if m:
        return "wide" if m.group(1).upper() in {"LR", "RL"} else "tall"
    if re.search(r"^sequenceDiagram\b", head, re.I):
        return "wide"
    # Heuristic: many same-rank arrows → likely wide
    if source.count("-->") >= 3 and "subgraph" not in source.lower():
        lrish = len(re.findall(r"\s[A-Z]\[.*?\]\s*-->", source))
        if lrish >= 2:
            return "wide"
    return "tall"


def _render_width(orientation: str) -> str:
    """Canvas width in CSS pixels; paired with -s scale for sharp PDF placement."""
    if orientation == "wide":
        # Full content width at 72dpi after dividing by ASSET_SCALE.
        return str(int(CONTENT_W * ASSET_SCALE))
    # Tall diagrams: narrower canvas → correct aspect, not a wide squashed strip.
    return str(int(CONTENT_W * 0.72 * ASSET_SCALE))


def _command() -> list[str] | None:
    for name in ("mmdc", "mmdc.cmd"):
        found = shutil.which(name)
        if found:
            return [found]
    local = ROOT / "node_modules" / ".bin"
    for name in ("mmdc.cmd", "mmdc"):
        candidate = local / name
        if candidate.is_file():
            return [str(candidate)]
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx:
        return [npx, "-y", "@mermaid-js/mermaid-cli@11"]
    return None
