"""Write a new document from a short brief.

The planner outlines the sections, then one writer agent drafts each section.
Cloud profiles draft sections in parallel; the local profile drafts one at a
time. Details the brief does not give are marked to be confirmed. The company
pages come from the brand pack, so the draft leaves them out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from spark_docs.brand import archetype_prompt, load_archetypes

from .diagramfix import repair_fences
from .governor import cloud_thresholds, run_items, workers_for
from .llm import LLMError, Router

ARCHETYPES = load_archetypes()
_PIECES = ("prose", "table", "diagram", "steps", "cards", "callout")
_DEPTHS = {
    "short": {"low": 150, "high": 220, "sections": 5, "diagrams": 0, "tables": 1, "tokens": 1100},
    "standard": {"low": 280, "high": 400, "sections": 8, "diagrams": 2, "tables": 2, "tokens": 1600},
    "full": {"low": 400, "high": 550, "sections": 8, "diagrams": 3, "tables": 3, "tokens": 2000},
}
_DESIGN = re.compile(r"\b(design|architecture|specification|system design|technical design)\b", re.I)
_DETAIL = re.compile(r"\b(detailed|comprehensive|in depth|in-depth|thorough|full detail|rich content)\b", re.I)
_PIECE_FOR = {
    "diagram": re.compile(r"\b(flow|architecture|pipeline|process|approach|how it works|system)\b", re.I),
    "table": re.compile(r"\b(scope|comparison|phases|roadmap|commercial|pricing|options)\b", re.I),
    "steps": re.compile(r"\b(rollout|implementation|next steps|delivery|plan of work)\b", re.I),
    "cards": re.compile(r"\b(options|capabilities|workstreams)\b", re.I),
    "callout": re.compile(r"\b(risks|assumptions|decision)\b", re.I),
}
OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "archetype": {"type": "string", "enum": sorted({str(item.get("id")) for item in ARCHETYPES.values()})},
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "prepared_for": {"type": "string"},
        "depth": {"type": "string", "enum": ["short", "standard", "full"]},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "points": {"type": "array", "items": {"type": "string"}},
                    "pieces": {"type": "array", "items": {"type": "string", "enum": list(_PIECES)}},
                },
                "required": ["heading"],
            },
        },
    },
    "required": ["title", "sections"],
}
OUTLINE_PROMPT = """You plan a new Spark Technologies document from a brief. Reply with JSON only.

Choose the archetype that fits the brief:
{archetypes}

Choose a depth from the brief. short is a thin note: 4 or 5 sections. standard is a normal proposal or technical note: 6 to 8 sections. full is a design, a requirements list, or a brief that is already long: 8 sections. Match the brief: many bullets, tables, or an ask for detail means standard or full, not short.
Each section has a heading, 2 to 5 points, and the pieces it needs. Pieces are prose, table, diagram, steps, cards, and callout. Use a diagram only for a flow or an architecture. Use a table for scope, phases, or a comparison. Most sections are prose.
Use only facts from the brief. Do not invent prices, rates, dates, names, or figures. A point that needs a missing detail should say it is to be confirmed.
Leave out Who We Are, Why Choose Us, About Spark Technologies, and contact sections. The template adds them.
Write in UK English."""
SECTION_PROMPT = """You write one section of a Spark Technologies document in Markdown.
Write the body only. Do not repeat the section heading and do not write any other section.
Aim for {low} to {high} words, then stop.
Use only these pieces: {pieces}.
prose is paragraphs and bullets. A table is one markdown table, and any price or figure missing from the brief is "to be confirmed".
A diagram is one mermaid fence, flowchart LR, with at most 6 nodes:
```mermaid
flowchart LR
  A[First] --> B[Next]
```
steps is a :::steps block with ### titles and one sentence each. cards is a :::cards block with ### titles. A callout is one :::callout block.
Do not add a piece that is not in the list.
Cover every fact the brief gives for this section. If a piece is in the list, include it. Do not pad with generic filler.
Explain the work the brief asks for. Do not invent prices, rates, dates, client names, or figures.
Write in UK English with a confident, plain tone. Do not use em dashes or emoji."""
_SPARK = re.compile(r"\b(who we are|why choose|about spark|solution areas|contact us|contact details)\b", re.I)


class Quiet:
    def start(self, key: str, label: str, kind: str = "step", parent: str = "", detail: str = "") -> None:
        pass

    def finish(self, key: str, detail: str = "", status: str = "done") -> None:
        pass

    def check(self) -> None:
        pass


@dataclass
class Draft:
    markdown: str
    title: str
    sections: list[str]
    missing: list[str] = field(default_factory=list)


def outline(router: Router, brief: str) -> dict:
    system = OUTLINE_PROMPT.replace("{archetypes}", archetype_prompt())
    user = f"[[task]]\nPlan the document for this brief.\n[[target]]\n{brief}"
    data = router.complete("planner", system, user, schema=OUTLINE_SCHEMA, max_tokens=1500).data or {}
    return clean_outline(data, brief)


def clean_outline(data: dict, brief: str) -> dict:
    archetype = str(data.get("archetype") or "")
    if archetype not in ARCHETYPES:
        archetype = "proposal" if re.search(r"\bproposal\b", brief, re.I) else "technical_document"
    record = ARCHETYPES[archetype]
    sections = []
    for item in data.get("sections") or []:
        heading = _title(str((item or {}).get("heading") or ""))
        if not heading or _SPARK.search(heading) or heading.casefold() in {s["heading"].casefold() for s in sections}:
            continue
        points = [" ".join(str(point).split()) for point in (item.get("points") or []) if str(point).strip()][:5]
        asked = [str(piece) for piece in (item.get("pieces") or []) if str(piece) in _PIECES]
        sections.append({"heading": heading, "points": points, "pieces": asked})
    if len(sections) < 2:
        sections = [
            {"heading": "Overview", "points": [], "pieces": []},
            {"heading": "Scope", "points": [], "pieces": []},
            {"heading": "Next Steps", "points": [], "pieces": []},
        ]
    depth = choose_depth(brief, str(data.get("depth") or ""))
    band = _DEPTHS[depth]
    if depth == "short" and "|" not in brief:
        band = {**band, "tables": 0}
    sections = _assign_pieces(sections[: band["sections"]], band)
    title = _title(str(data.get("title") or "")) or _title(brief.splitlines()[0] if brief.strip() else "") or "New document"
    return {
        "archetype": str(record.get("id") or archetype),
        "doc_type": str(record.get("doc_type") or "DOCUMENTATION"),
        "title": title[:90],
        "subtitle": _title(str(data.get("subtitle") or ""))[:120],
        "prepared_for": " ".join(str(data.get("prepared_for") or "").split())[:80],
        "depth": depth,
        "sections": sections,
    }


def _brief_stats(text: str) -> dict:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    bullets = sum(1 for line in lines if re.match(r"\s*(?:[-*+]|\d+[.)])\s+", line))
    words = len(re.findall(r"[A-Za-z0-9']+", text or ""))
    return {
        "words": words,
        "bullets": bullets,
        "has_table": "|" in (text or "") and "---" in (text or ""),
        "has_fence": "```" in (text or ""),
        "lines": len(lines),
    }


def choose_depth(brief: str, suggested: str) -> str:
    """A thin note stays short. A design or a long brief can be full. A proposal stays in the middle."""
    text = brief or ""
    stats = _brief_stats(text)
    design = bool(_DESIGN.search(text))
    proposal = bool(re.search(r"\bproposal\b", text, re.I))
    rich = bool(_DETAIL.search(text)) or stats["bullets"] >= 4 or stats["has_table"] or stats["has_fence"]
    long = stats["words"] > 900 or len(text) > 1200 or design
    thin = stats["words"] < 220 and stats["bullets"] < 2 and not rich and not design and not proposal
    if suggested not in _DEPTHS:
        suggested = ""
    if thin:
        return "short"
    if long and (design or stats["words"] > 1400 or suggested == "full"):
        return "full"
    if suggested == "full" and not thin:
        return "full" if (design or rich or stats["words"] > 700) else "standard"
    if suggested == "short" and (proposal or stats["words"] > 500 or rich):
        return "standard"
    if suggested:
        return suggested
    if rich or stats["words"] > 650:
        return "standard"
    return "standard" if proposal or stats["words"] > 400 else "short"


def section_band(band: dict, section: dict) -> dict:
    """Scale the word and token budget when the planner loaded a section with more points or pieces."""
    points = len(section.get("points") or [])
    pieces = section.get("pieces") or []
    extra = max(0, points - 2) * 40
    if "diagram" in pieces:
        extra += 50
    if "table" in pieces:
        extra += 45
    if "steps" in pieces or "cards" in pieces:
        extra += 30
    low = band["low"] + extra // 3
    high = min(band["high"] + extra, band["high"] + 200)
    tokens = min(band["tokens"] + extra * 5, band["tokens"] + 700)
    return {**band, "low": low, "high": high, "tokens": tokens}


def _assign_pieces(sections: list[dict], band: dict) -> list[dict]:
    diagrams = int(band["diagrams"])
    tables = int(band["tables"])
    assigned = []
    for section in sections:
        pieces = ["prose"]
        asked = set(section.get("pieces") or [])
        heading = section["heading"]
        for name, pattern in _PIECE_FOR.items():
            wanted = name in asked or bool(pattern.search(heading))
            if not wanted:
                continue
            if name == "diagram" and diagrams <= 0:
                continue
            if name == "table" and tables <= 0:
                continue
            if name not in pieces:
                pieces.append(name)
            if name == "diagram":
                diagrams -= 1
            if name == "table":
                tables -= 1
        assigned.append({**section, "pieces": pieces})
    return assigned


def write_section(router: Router, brief: str, plan: dict, section: dict) -> str:
    band = section_band(_DEPTHS[plan.get("depth") or "standard"], section)
    listing = "\n".join(f"- {item['heading']}" for item in plan["sections"])
    points = "; ".join(section["points"]) or "what a reader needs from this section"
    pieces = ", ".join(section.get("pieces") or ["prose"])
    user = (
        f"[[task]]\nWrite the section \"{section['heading']}\" of the document \"{plan['title']}\". Cover: {points}.\n"
        f"Use these pieces: {pieces}.\n"
        f"[[target]]\nBrief:\n{brief}\n"
        f"[[brief]]\nAll sections in order:\n{listing}"
    )
    system = SECTION_PROMPT.format(low=band["low"], high=band["high"], pieces=pieces)
    text = router.complete("writer", system, user, max_tokens=band["tokens"], temperature=0.4).text
    return clean_section(text, section["heading"], words=band["high"])


def clean_section(text: str, heading: str, words: int | None = None) -> str:
    body = (text or "").strip()
    if re.match(r"^```(?:markdown|md)\s*\n", body):
        body = re.sub(r"^```(?:markdown|md)\s*\n", "", body, count=1)
        body = re.sub(r"\n```\s*$", "", body, count=1)
    lines = body.splitlines()
    while lines and (not lines[0].strip() or re.match(r"^#{1,6}\s+", lines[0]) or lines[0].strip().strip("*").strip().casefold() == heading.casefold()):
        lines.pop(0)
    body = "\n".join(lines).strip()
    body = re.sub(r"(?m)^#{1,2}\s+", "### ", body)
    body = re.sub(r"(\d)\s*\u2013\s*(\d)", r"\1 to \2", body)
    body = re.sub(r"\s*[\u2014\u2013]\s*", ", ", body)
    body = repair_fences(body)
    if words:
        body = _cap_words(body, words + 40)
    return body


def _cap_words(text: str, limit: int) -> str:
    if len(text.split()) <= limit:
        return text
    parts = re.split(r"(\n?```[\s\S]*?\n```)", text)
    kept: list[str] = []
    count = 0
    for part in parts:
        fenced = part.lstrip("\n").startswith("```")
        words = part.split()
        if fenced:
            kept.append(part)
            count += len(words)
            continue
        if count >= limit:
            continue
        if count + len(words) <= limit:
            kept.append(part)
            count += len(words)
            continue
        chunk: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", part.strip()):
            if count + len(sentence.split()) > limit and chunk:
                break
            chunk.append(sentence)
            count += len(sentence.split())
        if chunk:
            kept.append(" ".join(chunk))
        break
    return "\n".join(kept).strip()


def draft_document(router: Router, brief: str, *, spark: bool, events=None) -> Draft:
    events = events or Quiet()
    events.start("outline", "Outlining the document")
    plan = outline(router, brief)
    kind = str(plan["archetype"]).replace("_", " ")
    events.finish("outline", f"{len(plan['sections'])} sections for a {kind}")
    events.start("write", "Writing the sections")
    items = list(enumerate(plan["sections"]))

    def one(item) -> str:
        index, section = item
        key = f"write-{index}"
        events.start(key, section["heading"], kind="agent", parent="write")
        try:
            body = write_section(router, brief, plan, section)
        except LLMError:
            events.finish(key, "left to be confirmed", status="failed")
            raise
        events.finish(key, f"{len(body.split())} words")
        return body

    bodies: list[str | None] = []
    workers = workers_for(router)
    if workers > 1:
        limits = router.config.get("limits") or {}
        report = run_items(
            items,
            one,
            workers=workers,
            lane="cloud",
            provider=router.profile,
            timeout_s=float(limits.get("request_timeout_s") or 300),
            retries=0,
            run_id=router.run_id,
            **cloud_thresholds(router),
        )
        bodies = [result.value if result and result.ok else None for result in report.results]
    else:
        for item in items:
            try:
                bodies.append(one(item))
            except LLMError:
                bodies.append(None)
    events.check()
    missing = [section["heading"] for section, body in zip(plan["sections"], bodies) if not body]
    if len(missing) == len(bodies):
        events.finish("write", "no section was written", status="failed")
        raise LLMError("the writer did not return any section")
    done = len(bodies) - len(missing)
    events.finish("write", f"{done} of {len(bodies)} sections written")
    events.start("assemble", "Assembling the draft")
    markdown = assemble(plan, bodies, spark)
    events.finish("assemble", f"{len(markdown.split())} words")
    return Draft(markdown, plan["title"], [section["heading"] for section in plan["sections"]], missing)


def assemble(plan: dict, bodies: list[str | None], spark: bool) -> str:
    front = {"title": plan["title"], "doc_type": plan["doc_type"], "archetype": plan["archetype"]}
    if plan.get("subtitle"):
        front["subtitle"] = plan["subtitle"]
    if plan.get("prepared_for"):
        front["prepared_for"] = plan["prepared_for"]
        front["client"] = plan["prepared_for"]
    front["include_spark_content"] = bool(spark)
    parts = [f"# {plan['title']}", ""]
    for section, body in zip(plan["sections"], bodies):
        parts += [f"## {section['heading']}", "", body or "This section is to be confirmed.", ""]
    head = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=10000).strip()
    return f"---\n{head}\n---\n\n" + "\n".join(parts).rstrip() + "\n"


def _title(text: str) -> str:
    clean = re.sub(r"^#+\s*", "", " ".join(str(text).split())).strip().strip("*").strip()
    clean = re.sub(r"^(?:\d{1,2}[.)]\s+)", "", clean)
    return clean.rstrip(":.")
