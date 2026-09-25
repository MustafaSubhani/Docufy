"""Spec -> measured nodes -> ELK layout -> scene in 680-wide canvas units."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from PIL import ImageFont

from . import style as S
from .layout import elk_layout, simple_layout
from .spec import Diagram, Node

DEFAULT_LEGEND = {
    "blue": "Input",
    "purple": "Processing",
    "teal": "Output",
    "gray": "Platform",
    "amber": "Validation",
    "coral": "Risk",
    "green": "Approval",
    "red": "Error",
    "pink": "Other",
}


@lru_cache(maxsize=8)
def font(size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(S.FONT_BOLD if bold else S.FONT_REGULAR), size=int(round(size * 16)) or 1)


def text_w(text: str, size: float, bold: bool = False) -> float:
    return font(size, bold).getlength(text) / 16.0


def wrap(text: str, size: float, max_w: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_w(trial, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


@dataclass
class TextLine:
    text: str
    size: float
    color: str
    bold: bool = False


@dataclass
class SNode:
    id: str
    x: float
    y: float
    w: float
    h: float
    ramp: str
    shape: str
    lines: list[TextLine]


@dataclass
class SEdge:
    points: list[tuple[float, float]]
    color: str
    dashed: bool
    label: str = ""
    label_xy: tuple[float, float] | None = None


@dataclass
class SGroup:
    x: float
    y: float
    w: float
    h: float
    label: str
    sublabel: str
    ramp: str | None


@dataclass
class Scene:
    width: float
    height: float
    nodes: list[SNode] = field(default_factory=list)
    edges: list[SEdge] = field(default_factory=list)
    groups: list[SGroup] = field(default_factory=list)
    legend: list[tuple[str, str]] = field(default_factory=list)
    legend_x: float = S.MARGIN
    legend_y: float = 0.0
    scale: float = 1.0  # content scale applied to fit the canvas


def node_text(n: Node, ramp: str, max_text_w: float = S.NODE_MAX_TEXT_W) -> tuple[list[TextLine], float, float]:
    r = S.ramp(ramp)
    title_lines = wrap(n.title, S.TITLE_SIZE, max_text_w)
    lines = [TextLine(t, S.TITLE_SIZE, r[S.STOP["800"]]) for t in title_lines]
    if n.subtitle:
        lines += [TextLine(t, S.SUB_SIZE, r[S.STOP["600"]]) for t in wrap(n.subtitle, S.SUB_SIZE, max_text_w)]
    width = max(text_w(ln.text, ln.size) for ln in lines) + 2 * S.NODE_PAD_X
    height = 12 + sum(18 if ln.size == S.TITLE_SIZE else 16 for ln in lines) + 10
    if n.shape == "diamond":
        width, height = width * 1.45, height * 1.6
    return lines, max(width, S.NODE_MIN_W), max(height, S.NODE_H1)


SNAKE_MIN = 5    # linear chains this long wrap into rows
SNAKE_ROW = 4    # nodes per row


def linear_chain(d: Diagram) -> list[str] | None:
    """Return node ids in order if the diagram is one simple path of SNAKE_MIN+ nodes."""
    if len(d.nodes) < SNAKE_MIN or len(d.edges) != len(d.nodes) - 1:
        return None
    nxt: dict[str, str] = {}
    indeg: dict[str, int] = {n.id: 0 for n in d.nodes}
    for e in d.edges:
        if e.src in nxt or e.dst not in indeg:
            return None
        nxt[e.src] = e.dst
        indeg[e.dst] += 1
    starts = [n for n, k in indeg.items() if k == 0]
    if len(starts) != 1 or any(k > 1 for k in indeg.values()):
        return None
    order = [starts[0]]
    while order[-1] in nxt:
        order.append(nxt[order[-1]])
    return order if len(order) == len(d.nodes) else None


def snake_layout(d: Diagram, chain: list[str], measured) -> dict:
    """Rows of SNAKE_ROW nodes flowing left-to-right, then right-to-left (boustrophedon)."""
    per_row = min(SNAKE_ROW, len(chain))
    gap_x = 34.0
    gap_y = 30.0
    w = (S.CANVAS_W - 2 * S.MARGIN - gap_x * (per_row - 1)) / per_row
    for nid in chain:  # re-wrap text to the fixed column width
        n = d.node(nid)
        lines, _w, h_n = node_text(n, measured[nid][3], w - 2 * S.NODE_PAD_X)
        measured[nid] = (lines, w, h_n, measured[nid][3])
    h = max(measured[i][2] for i in chain)
    for nid in chain:
        measured[nid] = (measured[nid][0], w, h, measured[nid][3])
    children, pos = [], {}
    for k, nid in enumerate(chain):
        row, col = divmod(k, per_row)
        if row % 2 == 1:
            col = per_row - 1 - col
        x, y = col * (w + gap_x), row * (h + gap_y)
        pos[nid] = (x, y)
        children.append({"id": nid, "x": x, "y": y, "width": w, "height": h})
    edges = []
    for i, e in enumerate(d.edges):
        (ax, ay), (bx, by) = pos[e.src], pos[e.dst]
        if abs(ay - by) < 1:  # same row
            if bx > ax:
                s, t = (ax + w, ay + h / 2), (bx, by + h / 2)
            else:
                s, t = (ax, ay + h / 2), (bx + w, by + h / 2)
        else:  # drop to the next row
            s, t = (ax + w / 2, ay + h), (bx + w / 2, by)
        edges.append({"id": f"e{i}", "sections": [{"startPoint": {"x": s[0], "y": s[1]}, "endPoint": {"x": t[0], "y": t[1]}}]})
    width = max(c["x"] + c["width"] for c in children)
    height = max(c["y"] + c["height"] for c in children)
    return {"id": "root", "children": children, "edges": edges, "width": width, "height": height}


def build_scene(d: Diagram) -> Scene:
    measured: dict[str, tuple[list[TextLine], float, float, str]] = {}
    for n in d.nodes:
        ramp = S.resolve_ramp(n.category)
        lines, w, h = node_text(n, ramp)
        measured[n.id] = (lines, w, h, ramp)

    # tidy sizes: equal widths when they are close, equal heights for LR pipelines
    widths = sorted(m[1] for m in measured.values())
    if widths and widths[-1] <= 1.6 * widths[len(widths) // 2]:
        target = widths[-1]
        measured = {k: (v[0], target if d.node(k).shape != "diamond" else v[1], v[2], v[3]) for k, v in measured.items()}
    if d.direction == "LR" and measured:
        hmax = max(v[2] for k, v in measured.items() if d.node(k).shape != "diamond")
        measured = {k: (v[0], v[1], hmax if d.node(k).shape != "diamond" else v[2], v[3]) for k, v in measured.items()}

    group_ids = {g.id for g in d.groups}
    children: dict[str | None, list[dict]] = {}
    for n in d.nodes:
        lines, w, h, ramp = measured[n.id]
        parent = n.group if n.group in group_ids else None
        children.setdefault(parent, []).append({"id": n.id, "width": w, "height": h})
    root_children = list(children.get(None, []))
    for g in d.groups:
        if children.get(g.id):
            top = S.GROUP_LABEL_H + (14 if g.sublabel else 6)
            root_children.append(
                {
                    "id": f"group::{g.id}",
                    "layoutOptions": {"elk.padding": f"[top={top},left={S.GROUP_PAD},bottom={S.GROUP_PAD},right={S.GROUP_PAD}]"},
                    "children": children[g.id],
                }
            )
    edges = []
    for i, e in enumerate(d.edges):
        if d.node(e.src) is None or d.node(e.dst) is None:
            continue
        item = {"id": f"e{i}", "sources": [e.src], "targets": [e.dst]}
        if e.label:
            item["labels"] = [{"text": e.label, "width": text_w(e.label, S.LABEL_SIZE) + 6, "height": 16}]
        edges.append(item)
    graph = {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT" if d.direction == "LR" else "DOWN",
            "elk.hierarchyHandling": "INCLUDE_CHILDREN",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.spacing.nodeNode": str(S.NODE_GAP),
            "elk.layered.spacing.nodeNodeBetweenLayers": str(S.LAYER_GAP),
            "elk.spacing.edgeNode": "14",
            "elk.spacing.edgeEdge": "10",
            "elk.layered.spacing.edgeNodeBetweenLayers": "14",
            "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
            "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
            "elk.edgeLabels.placement": "CENTER",
            "elk.json.edgeCoords": "ROOT",
            "elk.json.shapeCoords": "ROOT",
        },
        "children": root_children,
        "edges": edges,
    }
    chain = linear_chain(d)
    laid = snake_layout(d, chain, measured) if chain and not d.groups else elk_layout(graph)
    if laid is None:
        flat = [c for group in children.values() for c in group]
        laid = simple_layout(flat, edges, d.direction, S.NODE_GAP, S.LAYER_GAP)

    pos: dict[str, dict] = {}
    groups_pos: dict[str, dict] = {}

    def walk(items: list[dict]):
        for c in items:
            if str(c["id"]).startswith("group::"):
                groups_pos[c["id"][7:]] = c
                walk(c.get("children") or [])
            else:
                pos[c["id"]] = c

    walk(laid.get("children") or [])
    all_edges: list[dict] = list(laid.get("edges") or [])
    for g in groups_pos.values():
        all_edges += g.get("edges") or []

    content_w = max([p["x"] + p["width"] for p in pos.values()] + [g["x"] + g["width"] for g in groups_pos.values()] + [1])
    content_h = max([p["y"] + p["height"] for p in pos.values()] + [g["y"] + g["height"] for g in groups_pos.values()] + [1])
    avail = S.CANVAS_W - 2 * S.MARGIN
    if d.direction == "LR" and content_w > 1.35 * avail and not getattr(d, "_retried", False):
        # too wide to stay legible on a page: lay it out top-to-bottom instead
        import copy

        alt = copy.deepcopy(d)
        alt.direction = "TB"
        alt._retried = True
        return build_scene(alt)
    # never shrink geometry without text: widen the canvas and let the renderer scale uniformly
    canvas_w = max(S.CANVAS_W, content_w + 2 * S.MARGIN)
    scale = 1.0
    ox = (canvas_w - content_w) / 2
    oy = 20.0

    def tx(x: float) -> float:
        return ox + x * scale

    def ty(y: float) -> float:
        return oy + y * scale

    scene = Scene(width=canvas_w, height=0, scale=scale)
    for g in d.groups:
        gp = groups_pos.get(g.id)
        if gp:
            scene.groups.append(SGroup(tx(gp["x"]), ty(gp["y"]), gp["width"] * scale, gp["height"] * scale, g.label, g.sublabel, S.resolve_ramp(g.category) if g.category else None))
    for n in d.nodes:
        p = pos.get(n.id)
        if not p:
            continue
        lines, _w, _h, ramp = measured[n.id]
        scene.nodes.append(SNode(n.id, tx(p["x"]), ty(p["y"]), p["width"] * scale, p["height"] * scale, ramp, n.shape, lines))
    ramp_of = {sn.id: sn.ramp for sn in scene.nodes}
    spec_edges = {f"e{i}": e for i, e in enumerate(d.edges)}
    for le in all_edges:
        e = spec_edges.get(le["id"])
        if e is None:
            continue
        pts: list[tuple[float, float]] = []
        for sec in le.get("sections") or []:
            pts.append((tx(sec["startPoint"]["x"]), ty(sec["startPoint"]["y"])))
            for bp in sec.get("bendPoints") or []:
                pts.append((tx(bp["x"]), ty(bp["y"])))
            pts.append((tx(sec["endPoint"]["x"]), ty(sec["endPoint"]["y"])))
        if len(pts) < 2:
            continue
        ramp = S.resolve_ramp(e.category) if e.category else ramp_of.get(e.dst, "gray")
        label_xy = None
        if e.label and le.get("labels"):
            lb = le["labels"][0]
            label_xy = (tx(lb.get("x", 0) + lb.get("width", 0) / 2), ty(lb.get("y", 0) + lb.get("height", 0) / 2))
        scene.edges.append(SEdge(pts, S.ramp(ramp)[S.STOP["600"]], e.dashed, e.label, label_xy))

    bottom = oy + content_h * scale
    used = []
    for sn in scene.nodes:
        if sn.ramp not in used:
            used.append(sn.ramp)
    if d.legend is None and len(used) >= 2:
        scene.legend = [(r, DEFAULT_LEGEND.get(r, r.title())) for r in used]
    elif d.legend:
        scene.legend = [(S.resolve_ramp(c), label) for c, label in d.legend]
    if scene.legend:
        scene.legend_x = ox
        scene.legend_y = bottom + S.LEGEND_GAP
        bottom = scene.legend_y + S.LEGEND_SWATCH
    scene.height = bottom + 20
    return scene
