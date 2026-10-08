"""Diagram specification (what the agent or the Mermaid importer produces)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    id: str
    title: str
    subtitle: str = ""
    category: str = ""        # semantic category or ramp name (blue, purple, ai, output, ...)
    shape: str = "rect"       # rect | pill | diamond | label | band (full-width bar)
    group: str | None = None
    row: int | None = None    # grid layout position
    col: int | None = None
    colspan: int = 1
    rowspan: int = 1
    bold: bool = False
    pin_x: float | None = None  # tweak: keep this node where it was
    pin_y: float | None = None


@dataclass
class Edge:
    src: str
    dst: str
    label: str = ""
    dashed: bool = False
    category: str = ""        # defaults to the target node's category
    route: str = "auto"       # grid layout: auto (straight/diagonal) | elbow


@dataclass
class Group:
    id: str
    label: str = ""
    sublabel: str = ""
    category: str = ""        # empty = unboxed column header (Claude style); set to draw a tinted box


@dataclass
class Diagram:
    direction: str = "TB"     # TB | LR
    layout: str = "auto"      # auto (ELK) | grid (row/col placement, Claude hand-layout style)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    legend: list[tuple[str, str]] | None = None  # [(category, label)]; None = auto
    title: str = ""
    font: str = ""            # auto | segoe | dejavu (ADICC pages 14/24/27 were drawn in DejaVu)

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Diagram:
        d = cls(
            direction=str(data.get("direction", "TB")).upper(),
            title=data.get("title", ""),
            layout=str(data.get("layout", "auto")),
            font=str(data.get("font") or ""),
        )
        for n in data.get("nodes") or []:
            if isinstance(n, dict):
                d.nodes.append(Node(**{k: v for k, v in n.items() if k in Node.__dataclass_fields__}))
        for e in data.get("edges") or []:
            if isinstance(e, dict):
                e = dict(e)
                e.setdefault("src", e.pop("from", None))
                e.setdefault("dst", e.pop("to", None))
                d.edges.append(Edge(**{k: v for k, v in e.items() if k in Edge.__dataclass_fields__}))
        for g in data.get("groups") or []:
            if isinstance(g, dict):
                d.groups.append(Group(**{k: v for k, v in g.items() if k in Group.__dataclass_fields__}))
        if data.get("legend") is not None:
            d.legend = [(item.get("category", ""), item.get("label", "")) if isinstance(item, dict) else tuple(item) for item in data["legend"]]
        return d


def elk_node(node_id: str, width: float, height: float, pin_x: float | None, pin_y: float | None) -> dict:
    """ELK child. A pin is a position hint so a tweak does not move that node."""
    child = {"id": node_id, "width": width, "height": height}
    if pin_x is not None and pin_y is not None:
        child["layoutOptions"] = {"elk.position": f"({float(pin_x):.1f},{float(pin_y):.1f})"}
    return child


def pin_graph(graph: dict, nodes: list[Node]) -> dict:
    """Ask ELK to respect existing positions when any node is pinned. Unpinned graphs stay as they are."""
    if any(node.pin_x is not None and node.pin_y is not None for node in nodes):
        options = graph.setdefault("layoutOptions", {})
        options["elk.layered.crossingMinimization.semiInteractive"] = "true"
    return graph
