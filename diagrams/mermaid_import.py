"""Parse the Mermaid flowchart subset into a Diagram spec.

Supported: flowchart/graph TD|TB|LR|RL|BT, node shapes ([] () ([]) (()) {} [[]] [()] >]),
edges (--> --- -.-> ==> with |label| or -- label -->), chains (a --> b --> c), `&` fan-out,
`:::class`, `class a,b name`, classDef (names only), subgraph ... end. Other lines are ignored.
"""

from __future__ import annotations

import re

from .spec import Diagram, Edge, Group, Node

HEADER_RE = re.compile(r"^\s*(?:flowchart|graph)\s+(TD|TB|LR|RL|BT)\b", re.I)
SHAPES = [
    (r"\(\[", r"\]\)", "pill"),
    (r"\(\(", r"\)\)", "pill"),
    (r"\[\[", r"\]\]", "rect"),
    (r"\[\(", r"\)\]", "rect"),
    (r"\[", r"\]", "rect"),
    (r"\(", r"\)", "pill"),
    (r"\{", r"\}", "diamond"),
    (r">", r"\]", "rect"),
]
NODE_RE = re.compile(
    r"^(?P<id>[A-Za-z0-9_][\w.-]*)\s*"
    r"(?P<shape>\(\[.*?\]\)|\(\(.*?\)\)|\[\[.*?\]\]|\[\(.*?\)\]|\[.*?\]|\(.*?\)|\{.*?\}|>.*?\])?"
    r"(?::::(?P<cls>[\w-]+))?\s*$"
)
EDGE_SPLIT_RE = re.compile(
    r"\s*(?P<op>-->|---|-\.->|-\.-|==>|===|--o|--x|<-->)\s*(?:\|(?P<label>[^|]*)\|)?\s*"
    r"|\s*--\s+(?P<label2>[^-]+?)\s+-->\s*|\s*-\.\s+(?P<label3>[^.]+?)\s+\.->\s*"
)


def clean_label(raw: str) -> tuple[str, str]:
    text = raw.strip().strip('"').strip("'")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I).replace("\\n", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&quot;", '"')
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if not parts:
        return "", ""
    return parts[0], " · ".join(parts[1:])


def parse_node_token(token: str) -> tuple[str, str | None, str, str, str] | None:
    """Return (id, title, subtitle, shape, class) for a node token like A["Label"]:::ai."""
    token = token.strip()
    m = NODE_RE.match(token)
    if not m:
        return None
    node_id = m.group("id")
    raw = m.group("shape")
    title, subtitle, shape = None, "", "rect"
    if raw:
        for open_, close, sh in SHAPES:
            mm = re.match(rf"^{open_}(.*){close}$", raw, re.S)
            if mm:
                title, subtitle = clean_label(mm.group(1))
                shape = sh
                break
    return node_id, title, subtitle, shape, m.group("cls") or ""


def parse(source: str) -> Diagram | None:
    lines = [ln.rstrip() for ln in source.strip().splitlines()]
    if not lines:
        return None
    head = HEADER_RE.match(lines[0])
    if not head:
        return None
    direction = head.group(1).upper()
    d = Diagram(direction="LR" if direction in {"LR", "RL"} else "TB")
    classes: dict[str, str] = {}
    group_stack: list[str] = []
    order: list[str] = []

    def ensure(node_id: str, title: str | None = None, subtitle: str = "", shape: str = "rect", cls: str = ""):
        n = d.node(node_id)
        if n is None:
            n = Node(id=node_id, title=title or node_id, subtitle=subtitle, shape=shape)
            d.nodes.append(n)
            order.append(node_id)
            if group_stack:
                n.group = group_stack[-1]
        else:
            if title is not None:
                n.title, n.subtitle, n.shape = title, subtitle or n.subtitle, shape
            if group_stack and n.group is None:
                n.group = group_stack[-1]
        if cls:
            classes[node_id] = cls
        return n

    for raw in lines[1:]:
        line = raw.strip().rstrip(";")
        if not line or line.startswith("%%"):
            continue
        low = line.lower()
        if low.startswith(("classdef ", "style ", "linkstyle ", "click ", "direction ")):
            continue
        if low.startswith("class "):
            parts = line.split()
            if len(parts) >= 3:
                for nid in parts[1].split(","):
                    classes[nid.strip()] = parts[2]
            continue
        if low.startswith("subgraph"):
            rest = line[8:].strip()
            m = re.match(r'^([\w-]+)\s*\[(.*)\]\s*$', rest)
            if m:
                gid, label = m.group(1), m.group(2).strip().strip('"')
            else:
                label = rest.strip().strip('"')
                gid = re.sub(r"\W+", "_", label) or f"g{len(d.groups)}"
            title, sub = clean_label(label)
            d.groups.append(Group(id=gid, label=title, sublabel=sub))
            group_stack.append(gid)
            continue
        if low == "end":
            if group_stack:
                group_stack.pop()
            continue

        # split into node tokens and edge operators
        pieces: list[tuple[str, dict]] = []
        pos = 0
        for m in EDGE_SPLIT_RE.finditer(line):
            pieces.append(("nodes", {"text": line[pos : m.start()]}))
            label = m.group("label") or m.group("label2") or m.group("label3") or ""
            op = m.group("op") or ("-.->" if m.group("label3") else "-->")
            pieces.append(("edge", {"label": label.strip().strip('"'), "dashed": "." in op, "arrow": op not in {"---", "-.-", "==="}}))
            pos = m.end()
        pieces.append(("nodes", {"text": line[pos:]}))
        groups_of_nodes: list[list[str]] = []
        edges_meta: list[dict] = []
        for kind, data in pieces:
            if kind == "nodes":
                ids = []
                for tok in data["text"].split("&"):
                    parsed = parse_node_token(tok)
                    if parsed:
                        nid, title, sub, shape, cls = parsed
                        ensure(nid, title, sub, shape, cls)
                        ids.append(nid)
                groups_of_nodes.append(ids)
            else:
                edges_meta.append(data)
        for i, meta in enumerate(edges_meta):
            if i + 1 >= len(groups_of_nodes):
                break
            for a in groups_of_nodes[i]:
                for b in groups_of_nodes[i + 1]:
                    d.edges.append(Edge(src=a, dst=b, label=meta["label"], dashed=meta["dashed"]))

    # categories: explicit classes win, otherwise infer from graph position and shape
    incoming = {e.dst for e in d.edges}
    outgoing = {e.src for e in d.edges}
    for n in d.nodes:
        cls = classes.get(n.id, "")
        if cls:
            n.category = cls
        elif n.shape == "diamond":
            n.category = "validation"
        elif n.id not in incoming and n.id in outgoing:
            n.category = "input"
        elif n.id in incoming and n.id not in outgoing:
            n.category = "output"
        else:
            n.category = "ai"
    if not any(classes.values()):
        d.legend = []  # inferred categories: no legend unless the author defined classes
    return d
