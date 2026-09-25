"""Graph layout via ELK (persistent Node worker) with a small pure-Python layered fallback."""

from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import subprocess
import threading
from pathlib import Path

WORKER = Path(__file__).resolve().parent / "node_worker" / "layout.js"
_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_cache: dict[str, dict] = {}
_counter = 0


def _start() -> subprocess.Popen | None:
    global _proc
    if _proc and _proc.poll() is None:
        return _proc
    node = shutil.which("node") or shutil.which("node.exe")
    if not node or not WORKER.is_file() or not (WORKER.parent / "node_modules" / "elkjs").is_dir():
        return None
    _proc = subprocess.Popen(
        [node, str(WORKER)],
        cwd=str(WORKER.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    atexit.register(_stop)
    return _proc


def _stop() -> None:
    global _proc
    if _proc and _proc.poll() is None:
        try:
            _proc.stdin.close()
            _proc.wait(timeout=2)
        except Exception:
            _proc.kill()
    _proc = None


def elk_layout(graph: dict) -> dict | None:
    """Run ELK on an ELK-JSON graph. Returns the laid-out graph or None if ELK is unavailable."""
    global _counter
    key = hashlib.sha1(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    with _lock:
        proc = _start()
        if proc is None:
            return None
        _counter += 1
        req = {"id": _counter, "graph": graph}
        try:
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
        except (OSError, ValueError):
            _stop()
            return None
    if not line:
        return None
    resp = json.loads(line)
    if resp.get("error"):
        raise RuntimeError(f"ELK layout failed: {resp['error']}")
    _cache[key] = resp["graph"]
    return resp["graph"]


def simple_layout(nodes: list[dict], edges: list[dict], direction: str, node_gap: float, layer_gap: float) -> dict:
    """Longest-path layering + barycentre ordering + straight edges (used when Node/ELK is missing)."""
    ids = [n["id"] for n in nodes]
    size = {n["id"]: (n["width"], n["height"]) for n in nodes}
    preds: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        if e["sources"][0] in preds and e["targets"][0] in preds:
            preds[e["targets"][0]].append(e["sources"][0])
    layer: dict[str, int] = {}

    def depth(i: str, seen: set[str]) -> int:
        if i in layer:
            return layer[i]
        if i in seen:
            return 0
        seen.add(i)
        layer[i] = 1 + max((depth(p, seen) for p in preds[i]), default=-1)
        return layer[i]

    for i in ids:
        depth(i, set())
    layers: dict[int, list[str]] = {}
    for i in ids:
        layers.setdefault(layer[i], []).append(i)
    pos: dict[str, tuple[float, float]] = {}
    horizontal = direction == "LR"
    cursor = 0.0
    for li in sorted(layers):
        members = layers[li]
        if li > 0:
            order_of = {m: k for k, m in enumerate(layers[li - 1])}
            members.sort(key=lambda m: sum(order_of.get(p, 0) for p in preds[m]) / max(len(preds[m]), 1))
        span = sum(size[m][1 if horizontal else 0] for m in members) + node_gap * (len(members) - 1)
        offset = -span / 2
        thick = max(size[m][0 if horizontal else 1] for m in members)
        for m in members:
            w, h = size[m]
            if horizontal:
                pos[m] = (cursor + (thick - w) / 2, offset)
                offset += h + node_gap
            else:
                pos[m] = (offset, cursor + (thick - h) / 2)
                offset += w + node_gap
        cursor += thick + layer_gap
    minx = min(p[0] for p in pos.values())
    miny = min(p[1] for p in pos.values())
    children = [{"id": i, "x": pos[i][0] - minx, "y": pos[i][1] - miny, "width": size[i][0], "height": size[i][1]} for i in ids]
    by_id = {c["id"]: c for c in children}
    out_edges = []
    for e in edges:
        a, b = by_id.get(e["sources"][0]), by_id.get(e["targets"][0])
        if not a or not b:
            continue
        if horizontal:
            start = (a["x"] + a["width"], a["y"] + a["height"] / 2)
            end = (b["x"], b["y"] + b["height"] / 2)
        else:
            start = (a["x"] + a["width"] / 2, a["y"] + a["height"])
            end = (b["x"] + b["width"] / 2, b["y"])
        out_edges.append({**e, "sections": [{"startPoint": {"x": start[0], "y": start[1]}, "endPoint": {"x": end[0], "y": end[1]}}]})
    width = max(c["x"] + c["width"] for c in children)
    height = max(c["y"] + c["height"] for c in children)
    return {"id": "root", "children": children, "edges": out_edges, "width": width, "height": height}
