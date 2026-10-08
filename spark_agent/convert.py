"""Smart Convert: any markdown -> template-correct Spark markdown -> PDF/DOCX, verified.

The model only plans structure (sections and components over block ids). Assembly is deterministic and
copies source text verbatim, so numbers, names, links, images and diagrams cannot be altered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from spark_docs.brand import archetype_prompt, load_archetypes

from . import store
from .blocks import HEADING, SrcBlock, segment, split_frontmatter
from .governor import harmonize, run_items, workers_for
from .llm import LLMError, Router

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = Path(__file__).resolve().parent / "prompts"
ARCHETYPES = load_archetypes()

DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "archetype": {"type": "string", "enum": list(ARCHETYPES)},
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "doc_type": {"type": "string"},
        "prepared_for": {"type": "string"},
        "title_block": {"type": ["string", "null"]},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kicker": {"type": "string"}, "title": {"type": "string"}, "blocks": {"type": "array", "items": {"type": "string"}}},
                "required": ["kicker", "title", "blocks"],
            },
        },
    },
    "required": ["archetype", "title", "doc_type", "sections"],
}

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "component": {"type": "string", "enum": ["text", "callout", "columns", "cards", "steps"]},
                    "title": {"type": "string"},
                    "blocks": {"type": "array", "items": {"type": "string"}},
                    "parts": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"title": {"type": "string"}, "blocks": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "blocks"]},
                    },
                },
                "required": ["component"],
            },
        }
    },
    "required": ["items"],
}


@dataclass
class ConvertResult:
    markdown: str
    plan: dict
    issues: list[str] = field(default_factory=list)
    run_id: str = ""
    pdf: Path | None = None
    docx: Path | None = None


# ---------------------------------------------------------------------------
# planning


def outline(blocks: list[SrcBlock], full: bool = False) -> str:
    rows = []
    for b in blocks:
        label = f"{b.kind}{b.level}" if b.kind == "heading" else b.kind
        rows.append(f"{b.id} [{label}] {b.text if full else b.preview()}")
    return "\n".join(rows)


def plan_document(router: Router, blocks: list[SrcBlock], meta: dict, feedback: str = "") -> dict:
    system = (PROMPTS / "plan_document.md").read_text(encoding="utf-8").replace("{archetypes}", archetype_prompt())
    known = {k: meta[k] for k in ("title", "subtitle", "prepared_for", "doc_type") if meta.get(k)}
    user = f"Known cover fields from the source frontmatter: {known or 'none'}\n\nSource outline:\n{outline(blocks)}"
    if feedback:
        user += f"\n\nYour previous plan had problems, fix them:\n{feedback}"
    from .memory import watchout

    user += watchout(
        archetype=str(meta.get("archetype") or ""),
        client=str(meta.get("prepared_for") or meta.get("client") or ""),
        stage="plan",
    )
    return router.complete("planner", system, user, schema=DOC_SCHEMA, max_tokens=4096).data


DEV_BLOCK_UNIT = 4


def dev_units(section: dict, router: Router) -> list[dict]:
    """The local profile plans a few blocks at a time. Cloud profiles keep the whole section."""
    blocks = list(section.get("blocks") or [])
    if router.profile != "dev" or len(blocks) <= DEV_BLOCK_UNIT:
        return [section]
    return [{**section, "blocks": blocks[index : index + DEV_BLOCK_UNIT]} for index in range(0, len(blocks), DEV_BLOCK_UNIT)]


def plan_section(router: Router, section: dict, by_id: dict[str, SrcBlock]) -> dict:
    system = (PROMPTS / "plan_section.md").read_text(encoding="utf-8")
    blocks = [by_id[i] for i in section["blocks"] if i in by_id]
    text = outline(blocks, full=sum(len(b.text) for b in blocks) < 6000)
    user = f"Section: {section['kicker']} / {section['title']}\n\nBlocks:\n{text}"
    from .memory import watchout

    user += watchout(section_type=str(section.get("kicker") or ""), stage="plan")
    return router.complete("planner", system, user, schema=SECTION_SCHEMA, max_tokens=2048).data


def validate_doc_plan(plan: dict, blocks: list[SrcBlock]) -> tuple[dict, list[str]]:
    """Repair the plan so every block is used once and in order; report what was fixed."""
    issues: list[str] = []
    order = {b.id: i for i, b in enumerate(blocks)}
    title_block = plan.get("title_block") if plan.get("title_block") in order else None
    skip = {b.id for b in blocks if b.kind == "hr"} | ({title_block} if title_block else set())
    seen: set[str] = set()
    sections = []
    for s in plan.get("sections") or []:
        ids = []
        for i in s.get("blocks") or []:
            if i in order and i not in seen and i not in skip:
                ids.append(i)
                seen.add(i)
            elif i not in order:
                issues.append(f"unknown block id {i}")
        if ids:
            sections.append({"kicker": clean_title(s.get("kicker") or ""), "title": clean_title(s.get("title") or ""), "blocks": sorted(ids, key=order.get)})
    missing = [b.id for b in blocks if b.id not in seen and b.id not in skip]
    for mid in missing:  # attach to the section whose blocks are nearest before it
        target = None
        for s in sections:
            if order[s["blocks"][0]] < order[mid]:
                target = s
        if target is None and sections:
            target = sections[0]
        if target is None:
            sections.append({"kicker": "Overview", "title": "Overview", "blocks": []})
            target = sections[-1]
        target["blocks"] = sorted(target["blocks"] + [mid], key=order.get)
    if missing:
        issues.append(f"{len(missing)} blocks were not placed by the planner and were attached in source order")
    sections.sort(key=lambda s: order[s["blocks"][0]])
    plan = dict(plan, sections=sections, title_block=title_block)
    return plan, issues


def resolve_title_block(blocks: list[SrcBlock], chosen: str | None) -> str | None:
    """A lone top heading above 2+ subsections is the document title, not a section."""
    by_id = {b.id: b for b in blocks}
    h1 = [b for b in blocks if b.kind == "heading" and b.level == 1]
    h2 = [b for b in blocks if b.kind == "heading" and b.level == 2]
    if len(h1) == 1 and len(h2) >= 2:
        return h1[0].id
    if chosen in by_id and by_id[chosen].kind == "heading":
        return chosen
    return None


def natural_sections(blocks: list[SrcBlock], title_block: str | None) -> list[dict] | None:
    """If the source already has 2+ top-level sections, use them as boundaries (no model guesswork)."""
    heads = [b for b in blocks if b.kind == "heading" and b.id != title_block]
    if not heads:
        return None
    top = min(b.level for b in heads)
    tops = [b for b in heads if b.level == top]
    if len(tops) < 2:
        deeper = [b for b in heads if b.level == top + 1]
        if len(tops) == 1 and len(deeper) >= 2:
            top = top + 1
            tops = deeper
        else:
            return None
    sections: list[dict] = []
    cur: dict | None = None
    for b in blocks:
        if b.id == title_block or b.kind == "hr":
            continue
        if b.kind == "heading" and b.level == top:
            name = clean_title(b.heading_text)
            cur = {"kicker": name, "title": name, "blocks": [b.id]}
            sections.append(cur)
        else:
            if cur is None:
                cur = {"kicker": "Overview", "title": "Overview", "blocks": []}
                sections.append(cur)
            cur["blocks"].append(b.id)
    return [s for s in sections if s["blocks"]]


def clean_title(t: str) -> str:
    t = re.sub(r"^#+\s*", "", t.strip())
    t = re.sub(r"^(?:\d{1,2}|A\d+)[.)\s]+", "", t)  # numbering is added by the renderer
    return t.strip().strip("*").strip()


# design limits: a component is only used when its content suits it (guards weak planners)
PART_LIMITS = {"columns": 900, "cards": 360, "steps": 220}
CALLOUT_LIMIT = 900


def part_chars(ids: list[str], by_id: dict[str, SrcBlock]) -> int:
    return sum(len(by_id[i].text) for i in ids if by_id[i].kind != "heading")


def validate_section_plan(sp: dict, section: dict, by_id: dict[str, SrcBlock]) -> list[dict]:
    allowed = [i for i in section["blocks"]]
    pos = {i: k for k, i in enumerate(allowed)}
    used: set[str] = set()
    items: list[dict] = []
    for it in sp.get("items") or []:
        comp = it.get("component", "text")
        if comp in {"columns", "cards", "steps"}:
            parts = []
            for p in it.get("parts") or []:
                ids = [i for i in p.get("blocks") or [] if i in pos and i not in used and by_id[i].kind in {"para", "list", "heading"}]
                if ids:
                    used.update(ids)
                    parts.append({"title": clean_title(p.get("title") or ""), "blocks": ids})
            ok = (comp == "columns" and len(parts) == 2) or (comp == "cards" and 2 <= len(parts) <= 8) or (comp == "steps" and 3 <= len(parts) <= 5)
            ok = ok and all(part_chars(p["blocks"], by_id) <= PART_LIMITS[comp] for p in parts)
            ok = ok and all(by_id[i].kind != "heading" or i == p["blocks"][0] for p in parts for i in p["blocks"])
            if ok:
                items.append({"component": comp, "parts": parts})
            else:
                for p in parts:
                    used.difference_update(p["blocks"])
        elif comp == "callout":
            ids = [i for i in it.get("blocks") or [] if i in pos and i not in used and by_id[i].kind in {"para", "list", "heading"}]
            if ids and by_id[ids[0]].kind == "heading":
                ids = ids if len(ids) > 1 else []
            if ids and (part_chars(ids, by_id) > CALLOUT_LIMIT or any(by_id[i].kind == "heading" for i in ids[1:])):
                ids = []
            if ids:
                used.update(ids)
                items.append({"component": "callout", "title": clean_title(it.get("title") or ""), "blocks": ids})
    # everything else stays text, and all items are ordered by their first block
    for i in allowed:
        if i not in used:
            items.append({"component": "text", "blocks": [i]})
    def first(it):
        ids = it.get("blocks") or [b for p in it.get("parts", []) for b in p["blocks"]]
        return min(pos[i] for i in ids)
    items.sort(key=first)
    merged: list[dict] = []
    for it in items:  # merge consecutive text items
        if it["component"] == "text" and merged and merged[-1]["component"] == "text":
            merged[-1]["blocks"] += it["blocks"]
        else:
            merged.append(it)
    return merged


# ---------------------------------------------------------------------------
# assembly (deterministic)


MODULE_RE = re.compile(r"^(module|phase|step|stage|part)\s+\d+", re.I)


def heading_md(b: SrcBlock, section_title_ids: set[str]) -> str:
    if b.id in section_title_ids:
        return ""
    text = clean_title(b.heading_text)
    if MODULE_RE.match(text) or b.level >= 3:
        return f"### {text}" if b.level <= 3 else f"#### {text}"
    return f"## {text}"


def body_text(b: SrcBlock) -> str:
    if b.kind == "heading":
        return ""
    return b.text


def part_md(p: dict, by_id: dict[str, SrcBlock]) -> str:
    ids = list(p["blocks"])
    title = p.get("title") or ""
    if ids and by_id[ids[0]].kind == "heading":
        title = title or clean_title(by_id[ids[0]].heading_text)
        ids = ids[1:]
    body = "\n\n".join(body_text(by_id[i]) for i in ids if body_text(by_id[i]))
    return f"### {title or 'Details'}\n{body}".rstrip()


def promote_title(section: dict, by_id: dict[str, SrcBlock]) -> set[str]:
    """Use the source's own page heading as the title; return heading ids consumed by kicker/title."""
    heads = [by_id[i] for i in section["blocks"] if by_id[i].kind == "heading"]
    consumed: set[str] = set()
    names = {section["kicker"].lower(), section["title"].lower()}
    lead = [by_id[i] for i in section["blocks"][:2] if by_id[i].kind == "heading"]
    for h in lead:
        if clean_title(h.heading_text).lower() in names:
            consumed.add(h.id)
    if not section["title"] or section["title"].lower() == section["kicker"].lower():
        nxt = next((h for h in lead if h.id not in consumed and h.level >= 2), None)
        if nxt is None and len(lead) == 1 and lead[0].id in consumed:
            first_body = [by_id[i] for i in section["blocks"][1:3]]
            nxt = next((b for b in first_body if b.kind == "heading"), None)
        if nxt is not None:
            section["title"] = clean_title(nxt.heading_text)
            consumed.add(nxt.id)
    for h in heads:
        if clean_title(h.heading_text).lower() == section["title"].lower():
            consumed.add(h.id)
            break
    return consumed


def section_md(n: int, section: dict, items: list[dict], by_id: dict[str, SrcBlock]) -> str:
    title_ids = promote_title(section, by_id)
    out = [f"# {n:02d} {section['kicker'] or section['title']}", "", f"## {section['title'] or section['kicker']}", ""]
    for it in items:
        comp = it["component"]
        if comp == "text":
            for i in it["blocks"]:
                b = by_id[i]
                chunk = heading_md(b, title_ids) if b.kind == "heading" else b.text
                if b.kind in {"hr"} or not chunk:
                    continue
                out += [chunk, ""]
        elif comp == "callout":
            body = [body_text(by_id[i]) for i in it["blocks"]]
            title = it.get("title") or (clean_title(by_id[it["blocks"][0]].heading_text) if by_id[it["blocks"][0]].kind == "heading" else "")
            out += [":::callout", f"### {title}" if title else "", "\n\n".join(t for t in body if t), ":::", ""]
        else:
            out.append(f":::{comp}")
            out.append("\n\n".join(part_md(p, by_id) for p in it["parts"]))
            out += [":::", ""]
    return "\n".join(line for line in out if line is not None).rstrip() + "\n"


INTERNAL_AUDIENCE = {"spark technologies", "spark ai", "spark", "internal"}


def is_internal(meta: dict, plan: dict) -> bool:
    who = str(meta.get("prepared_for") or plan.get("prepared_for") or "").strip().lower()
    text = " ".join(str(meta.get(k) or "") for k in ("subtitle", "footer", "title")).lower() + " " + str(plan.get("subtitle") or "").lower()
    return who in INTERNAL_AUDIENCE or "internal" in text


def spark_content_default(meta: dict, plan: dict, arch: dict) -> bool:
    for key in ("include_spark_content", "include_company_intro", "include_company_outro"):
        if key in meta:
            return bool(meta[key])
    if is_internal(meta, plan):
        return False
    return bool(arch.get("spark_content", False))


def frontmatter(plan: dict, meta: dict, source: Path, spark_content: bool | None) -> str:
    if is_internal(meta, plan) and plan.get("archetype") in {"technical_document", "client_guide", "proposal"}:
        plan["archetype"] = "internal_report"
    arch = ARCHETYPES.get(plan.get("archetype") or "", {})
    doc_type = (meta.get("doc_type") or plan.get("doc_type") or arch.get("doc_type") or "DOCUMENT").upper()
    if plan.get("archetype") != "proposal" and doc_type == "PROPOSAL":
        doc_type = arch.get("doc_type", "DOCUMENTATION")
    include = spark_content_default(meta, plan, arch) if spark_content is None else spark_content
    fm = {
        "title": meta.get("title") or plan.get("title") or source.stem.replace("_", " "),
        "subtitle": meta.get("subtitle") or plan.get("subtitle") or "",
        "doc_type": doc_type,
        "prepared_for": meta.get("prepared_for") or plan.get("prepared_for") or "",
        "prepared_by": meta.get("prepared_by") or "Spark Technologies",
        "date": str(meta.get("date") or date.today().strftime("%B %Y")),
        "confidential": True,
        "section_break": True,
        "include_spark_content": bool(include),
        "asset_roots": [str(source.parent.resolve())] + [str(r) for r in meta.get("asset_roots") or []],
    }
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n"


# ---------------------------------------------------------------------------


def build_plan(router: Router, blocks: list[SrcBlock], meta: dict, log=print) -> tuple[dict, list[str]]:
    plan: dict = {}
    issues: list[str] = []
    fixes: list[str] = []
    feedback = ""
    for attempt in range(2):
        try:
            plan = plan_document(router, blocks, meta, feedback)
        except LLMError as exc:
            issues.append(f"document planner failed: {exc}")
            plan = fallback_plan(blocks)
            break
        plan["title_block"] = resolve_title_block(blocks, plan.get("title_block"))
        natural = natural_sections(blocks, plan.get("title_block"))
        if natural:
            names = [s.get("kicker") for s in plan.get("sections") or []]
            if len(names) == len(natural):  # keep the planner's (cleaner) kicker names when they line up
                for s, k in zip(natural, names, strict=False):
                    s["kicker"] = clean_title(k or s["kicker"]) or s["kicker"]
            plan["sections"] = natural
        plan, fixes = validate_doc_plan(plan, blocks)
        big_gaps = any("not placed" in f for f in fixes) and int(fixes[-1].split()[0]) > max(3, len(blocks) // 5)
        if not big_gaps:
            issues += fixes
            break
        feedback = "\n".join(fixes)
        log(f"  plan attempt {attempt + 1} left too many blocks unplaced, re-planning")
    else:
        issues += fixes
    return plan, issues


def execute_plan(
    source: Path,
    router: Router,
    plan: dict,
    issues: list[str],
    meta: dict,
    body: str,
    blocks: list[SrcBlock],
    *,
    spark_content: bool | None,
    render: bool,
    docx: bool,
    out_dir: Path | None,
    log,
    run_id: str,
) -> ConvertResult:
    by_id = {b.id: b for b in blocks}

    def do_section(s: dict) -> list[dict]:
        items: list[dict] = []
        for piece in dev_units(s, router):
            try:
                items.extend(validate_section_plan(plan_section(router, piece, by_id), piece, by_id))
            except LLMError as exc:
                issues.append(f"section planner failed for {piece['kicker']}: {exc}")
                items.append({"component": "text", "blocks": list(piece["blocks"])})
        return items

    def traced(s: dict) -> list[dict]:
        log(f"  agent start: {s['kicker']}")
        try:
            return do_section(s)
        finally:
            log(f"  agent done: {s['kicker']}")

    workers = workers_for(router)
    if workers > 1:
        gov = router.config.get("governor") or {}
        report = run_items(
            plan["sections"],
            traced,
            workers=workers,
            lane="cloud",
            provider=router.profile,
            low_resource=bool(gov.get("low_resource")),
            timeout_s=float((router.config.get("limits") or {}).get("request_timeout_s") or 300),
            run_id=run_id,
        )
        section_items = []
        for section, result in zip(plan["sections"], report.results, strict=False):
            if result.ok:
                section_items.append(result.value)
            else:
                issues.append(f"section planner failed for {section['kicker']}: {result.error}")
                section_items.append([{"component": "text", "blocks": list(section["blocks"])}])
        if report.paused:
            log("  paused: memory is high, remaining sections were left as source text")
    else:
        section_items = []
        for k, s in enumerate(plan["sections"], 1):
            log(f"  section {k}/{len(plan['sections'])}: {s['kicker']}")
            section_items.append(do_section(s))
    plan["layout"] = section_items
    for note in harmonize(
        [
            {
                "name": section.get("kicker") or "section",
                "text": "\n".join(by_id[block_id].text for block_id in section.get("blocks") or [] if block_id in by_id),
            }
            for section in plan["sections"]
        ]
    ):
        log(f"  harmonize: {note}")

    md = frontmatter(plan, meta, source, spark_content) + "\n"
    md += "\n".join(section_md(k, s, items, by_id) for k, (s, items) in enumerate(zip(plan["sections"], section_items, strict=False), 1))
    from spark_docs.parser import tighten_tables

    md = tighten_tables(md)
    from .verify import verify_markdown

    issues += verify_markdown(body, md)
    result = ConvertResult(md, plan, issues, run_id)

    out_dir = Path(out_dir or ROOT / "output")
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{source.stem}.spark.md"
    md_path.write_text(md, encoding="utf-8")
    if render:
        from .verify import render_and_check

        log("  rendering pages")
        result.pdf, result.docx, render_issues = render_and_check(md_path, out_dir, docx=docx)
        result.issues += render_issues
    usage = store.usage(run_id)
    store.finish_run(run_id, "ok" if not result.issues else "ok_with_issues", str(md_path), {"issues": result.issues, "usage": usage})
    log(f"  tokens in {usage['input_tokens']} out {usage['output_tokens']} in {usage['calls']} calls, {usage['llm_seconds']}s model time")
    return result


def convert(
    source: Path,
    router: Router | None = None,
    spark_content: bool | None = None,
    render: bool = True,
    docx: bool = False,
    out_dir: Path | None = None,
    log=print,
) -> ConvertResult:
    source = Path(source)
    raw = source.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    blocks = segment(body)
    router = router or Router()
    run_id = store.start_run("convert", str(source), router.profile)
    router.run_id = run_id
    log(f"run {run_id}: {len(blocks)} source blocks, profile {router.profile} ({router.model_for('planner')})")
    plan, issues = build_plan(router, blocks, meta, log)
    store.event(run_id, "doc_plan", plan)
    log(f"  archetype {plan.get('archetype')}, {len(plan['sections'])} sections")
    return execute_plan(
        source,
        router,
        plan,
        issues,
        meta,
        body,
        blocks,
        spark_content=spark_content,
        render=render,
        docx=docx,
        out_dir=out_dir,
        log=log,
        run_id=run_id,
    )


def fallback_plan(blocks: list[SrcBlock]) -> dict:
    """No model available: one section per top-level heading, content as text."""
    sections, cur = [], None
    top = min((b.level for b in blocks if b.kind == "heading"), default=1)
    title_block = None
    for b in blocks:
        if b.kind == "heading" and b.level == top and title_block is None and not sections and sum(1 for x in blocks if x.kind == "heading" and x.level == top) == 1:
            title_block = b.id
            continue
        if b.kind == "heading" and b.level <= top + (1 if title_block else 0):
            cur = {"kicker": clean_title(b.heading_text), "title": clean_title(b.heading_text), "blocks": [b.id]}
            sections.append(cur)
        elif b.kind != "hr":
            if cur is None:
                cur = {"kicker": "Overview", "title": "Overview", "blocks": []}
                sections.append(cur)
            cur["blocks"].append(b.id)
    title = clean_title(next((b.heading_text for b in blocks if b.id == title_block), "")) if title_block else ""
    return {"archetype": "technical_document", "title": title, "doc_type": "DOCUMENTATION", "title_block": title_block, "sections": sections}


def split_heading(text: str) -> str:
    m = HEADING.match(text.strip())
    return m.group(2) if m else text
