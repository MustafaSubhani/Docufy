"""Tool registry for the document agent.

calc uses a safe arithmetic parser (no Python eval). Side-effect tools are render, inspect, chart, and image.
Web search stays in the research commands.
"""

from __future__ import annotations

import ast
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from spark_docs.brand import boilerplate_text, facts_contact, load_archetypes, load_facts

_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")


def now() -> dict:
    from .llm import now_line

    return {"text": now_line()}


def brand_lookup(key: str) -> dict:
    facts = load_facts()
    contact = facts_contact()
    if key == "contact":
        return dict(contact)
    if key in contact:
        return {key: contact[key]}
    if key == "products":
        return {"products": list(facts.get("products") or [])}
    if key.startswith("archetype:"):
        name = key.split(":", 1)[1]
        arch = load_archetypes().get(name)
        if not arch:
            return {"error": f"unknown archetype {name}"}
        return {
            "id": arch["id"],
            "doc_type": arch.get("doc_type"),
            "spark_content": bool(arch.get("spark_content")),
            "description": arch.get("description") or "",
        }
    if key.startswith("boilerplate:"):
        name = key.split(":", 1)[1]
        return {"name": name, "text": boilerplate_text(name)}
    return {"error": f"unknown key {key}"}


def calc_eval(expr: str, places: int | None = None) -> dict:
    """Evaluate arithmetic. Money rounds half up when places is set."""
    tree = ast.parse(str(expr), mode="eval")
    result = _eval_node(tree.body)
    if places is not None:
        result = result.quantize(Decimal("1").scaleb(-int(places)), rounding=ROUND_HALF_UP)
    return {"expr": expr, "result": format(result, "f")}


def calc_total(values: list, expected) -> dict:
    got = sum((Decimal(str(v)) for v in values), Decimal(0))
    exp = Decimal(str(expected))
    return {"sum": format(got, "f"), "expected": format(exp, "f"), "ok": got == exp}


def business_days(start: str, end: str) -> dict:
    """Weekdays from start through end. The UAE weekend is Saturday and Sunday."""
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise ValueError("end is before start")
    count = 0
    day = first
    while day <= last:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return {"days": count, "weekend": "Saturday-Sunday"}


def audit_numbers(source: str, draft: str) -> dict:
    known = set(_NUM.findall(source))
    invented = []
    for number in _NUM.findall(draft):
        if number in known or re.fullmatch(r"0?\d", number):
            continue
        if number not in invented:
            invented.append(number)
    return {"invented": invented}


def diagram_spec(source: str, lang: str = "diagram") -> dict:
    from spark_docs.parser import claude_diagram_spec

    spec = claude_diagram_spec(source, lang)
    if not spec:
        return {"ok": False, "spec": None}
    count = len(spec.get("nodes") or spec.get("participants") or [])
    return {"ok": True, "nodes": count, "spec": spec}


def render_markdown(markdown: str, out: str) -> dict:
    from spark_docs.parser import parse_markdown
    from spark_docs.pdf import build_pdf
    from spark_docs.sections import apply_company_sections

    meta, blocks = parse_markdown(markdown)
    blocks = apply_company_sections(meta, blocks)
    path = build_pdf(meta, blocks, Path(out))
    return {"pdf": str(path)}


def chart_create(spec: dict, dest: str = "") -> dict:
    from spark_docs.charts import render_chart

    path = render_chart(spec, Path(dest) if dest else None)
    return {"png": str(path)}


def image_generate(prompt: str, provider: str = "openai", seed: int = 1, preset: str = "spark") -> dict:
    from .imagegen import generate_image

    asset = generate_image(prompt, provider=provider, seed=seed, preset=preset)
    return asset.model_dump()


def inspect_pdf(pdf: str) -> dict:
    import pymupdf

    doc = pymupdf.open(pdf)
    try:
        return {"file": pdf, "pages": doc.page_count}
    finally:
        doc.close()


def _eval_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_node(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    raise ValueError("only arithmetic expressions are allowed")


TOOLS = {
    "now": now,
    "brand.lookup": brand_lookup,
    "calc.eval": calc_eval,
    "calc.total": calc_total,
    "calc.business_days": business_days,
    "calc.audit": audit_numbers,
    "diagram.spec": diagram_spec,
    "chart.create": chart_create,
    "image.generate": image_generate,
    "render": render_markdown,
    "inspect": inspect_pdf,
}


def call_tool(name: str, **kwargs) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        raise KeyError(f"unknown tool {name}")
    return fn(**kwargs)
