"""Verify, then fix, then stop.

Hard checks are deterministic. The critic may only raise an issue that names a page and a box.
Fixes run in order: auto, one-block patch, one-section regen, then escalate.
The best-scoring markdown is kept. The loop stops when the document is clean, the score
stops improving, the iteration cap is hit, a fix brings an old issue back, or the budget runs out.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from spark_docs.brand import BRAND, contact_issues, lexicon_issues, load_archetypes, load_facts
from spark_docs.parser import parse_markdown
from spark_docs.theme import MARGIN_BOTTOM, PAGE_H, PAGE_W

from .llm import LLMError, Router
from .tools import audit_numbers
from .verify import LEAK_RE, NUM_RE, strip_front, verify_markdown

PROMPTS = Path(__file__).resolve().parent / "prompts"
_FENCE = re.compile(r"```.*?```", re.S)
_H1 = re.compile(r"^# (\d{2}) (.+)$", re.M)
_ADD = re.compile(r"^# (A\d+) (.+)$", re.M)
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "hard"
    page: int | None = None
    block_id: str | None = None
    bbox: list[float] | None = None
    fix_type: str = "escalate"
    section: str = ""
    kind: str = ""

    def key(self) -> str:
        return f"{self.code}|{self.block_id or ''}|{self.message}"


@dataclass
class LoopResult:
    status: str
    markdown: str
    score: float
    issues: list[Issue] = field(default_factory=list)
    iterations: int = 0
    reason: str = ""
    history: list[dict] = field(default_factory=list)


def score_of(issues: list[Issue]) -> float:
    hard = sum(1 for i in issues if i.severity == "hard")
    soft = sum(1 for i in issues if i.severity == "soft")
    return max(0.0, 100.0 - 20.0 * hard - 5.0 * soft)


def run_loop(
    markdown: str,
    router: Router | None = None,
    *,
    source_body: str = "",
    pdf: Path | None = None,
    archetype: str = "",
    layout: dict | None = None,
    pages: int | None = None,
    protected: tuple[str, ...] = (),
    log=print,
) -> LoopResult:
    router = router or Router()
    cap, delta = _limits(router)

    def gather(text: str) -> list[Issue]:
        return collect(
            text,
            router,
            source_body=source_body,
            pdf=pdf,
            archetype=archetype,
            layout=layout,
            pages=pages,
            protected=protected,
        )

    current = markdown
    issues = gather(current)
    best_md, best_issues, best_score = current, issues, score_of(issues)
    cleared: set[str] = set()
    history: list[dict] = []
    status, reason = "clean", "clean"
    iterations = 0
    if not issues:
        _remember(router, status, best_score, reason, history, cleared, archetype)
        return LoopResult(status, best_md, best_score, best_issues, 0, reason, history)
    for iteration in range(1, cap + 1):
        iterations = iteration
        try:
            action, candidate = _next_fix(current, issues, router, protected)
        except LLMError as exc:
            if "budget" in str(exc):
                status, reason = "budget", str(exc)
                break
            raise
        if candidate is None:
            status, reason = "escalated", "remaining issues need a person"
            history.append({"iteration": iteration, "action": action, "score": score_of(issues)})
            break
        new_issues = gather(candidate)
        if any(item.key() in cleared for item in new_issues):
            history.append({"iteration": iteration, "action": "reject", "score": best_score})
            status, reason = "reintroduced", "a fix brought back an earlier issue"
            break
        new_score = score_of(new_issues)
        gain = new_score - score_of(issues)
        history.append({"iteration": iteration, "action": action, "score": new_score, "gain": gain})
        log(f"  verify {iteration}: {action}, score {new_score:.0f} ({gain:+.0f})")
        if new_score > best_score:
            best_md, best_issues, best_score = candidate, new_issues, new_score
        cleared |= {item.key() for item in issues} - {item.key() for item in new_issues}
        if gain < delta:
            status, reason = "plateau", f"score gain {gain:.1f} is under {delta:.1f}"
            break
        current, issues = candidate, new_issues
        if not issues:
            status, reason = "clean", "clean"
            break
    else:
        status, reason = "capped", f"stopped after {cap} iterations"
    _remember(router, status, best_score, reason, history, cleared, archetype)
    return LoopResult(status, best_md, best_score, best_issues, iterations, reason, history)


def collect(
    markdown: str,
    router: Router | None = None,
    *,
    source_body: str = "",
    pdf: Path | None = None,
    archetype: str = "",
    layout: dict | None = None,
    pages: int | None = None,
    protected: tuple[str, ...] = (),
) -> list[Issue]:
    layout = layout if layout is not None else _read_layout(pdf)
    page_count = pages if pages is not None else _page_count(pdf, layout)
    issues = hard_checks(
        markdown,
        source_body=source_body,
        pdf=pdf,
        archetype=archetype,
        layout=layout,
        pages=page_count,
        protected=protected,
    )
    if router is not None:
        issues += critic_issues(router, markdown, layout, archetype, page_count)
    return issues


def hard_checks(
    markdown: str,
    *,
    source_body: str = "",
    pdf: Path | None = None,
    archetype: str = "",
    layout: dict | None = None,
    pages: int | None = None,
    protected: tuple[str, ...] = (),
) -> list[Issue]:
    issues: list[Issue] = []
    body = strip_front(markdown)
    if source_body:
        for msg in verify_markdown(source_body, markdown):
            issues.append(Issue("content", msg, fix_type="escalate"))
        invented = audit_numbers(_traced_corpus(source_body), _structural_numbers_removed(body))["invented"]
        if invented:
            issues.append(Issue("numbers", f"numbers not in the source or brand pack: {', '.join(invented[:12])}", fix_type="escalate"))
    for msg in lexicon_issues(_without(body, protected)):
        issues.append(Issue("lexicon", msg, fix_type="auto"))
    numbering = _numbering_issue(body)
    if numbering:
        issues.append(numbering)
    meta, _ = parse_markdown(markdown)
    for msg in contact_issues(meta.contact):
        issues.append(Issue("contact", msg, fix_type="escalate"))
    budget = (load_archetypes().get(archetype) or {}).get("page_budget")
    if budget and pages and pages > int(budget):
        issues.append(Issue("page_budget", f"{pages} pages exceeds the {budget}-page budget", fix_type="escalate"))
    issues += _layout_issues(layout or {}, markdown)
    issues += _pdf_issues(pdf)
    return issues


def critic_issues(router: Router, markdown: str, layout: dict | None, archetype: str, pages: int | None) -> list[Issue]:
    system = (PROMPTS / "verify_critic.md").read_text(encoding="utf-8")
    brief = _layout_brief(layout or {})
    user = f"Verification critic\n\nArchetype: {archetype or 'unknown'}\nPages: {pages or 'unknown'}\n\nLAYOUT:\n{brief}\n\nMARKDOWN:\n{markdown}"
    try:
        res = router.complete("critic_vision", system, user, schema=_CRITIC_SCHEMA, temperature=0.1)
    except LLMError:
        return []
    data = res.data if isinstance(res.data, dict) else {}
    return accept_soft(data)


def accept_soft(data: dict) -> list[Issue]:
    """Drop critic issues that have no page or no four-number box."""
    found: list[Issue] = []
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        page = raw.get("page")
        bbox = raw.get("bbox")
        if not isinstance(page, int) or not _bbox_ok(bbox):
            continue
        fix = str(raw.get("fix_type") or "escalate")
        if fix not in {"auto", "patch", "regen", "escalate"}:
            fix = "escalate"
        severity = str(raw.get("severity") or "soft")
        if severity not in {"hard", "soft"}:
            severity = "soft"
        found.append(
            Issue(
                "critic",
                str(raw.get("message") or "critic issue"),
                severity=severity,
                page=page,
                block_id=str(raw.get("block_id") or "") or None,
                bbox=[float(n) for n in bbox],
                fix_type=fix,
                section=str(raw.get("section") or ""),
                kind=str(raw.get("kind") or ""),
            )
        )
    return found


def _next_fix(markdown: str, issues: list[Issue], router: Router, protected: tuple[str, ...] = ()) -> tuple[str, str | None]:
    auto = [item for item in issues if item.fix_type == "auto"]
    if auto:
        return "auto", _apply_auto(markdown, auto, protected)
    patch = next((item for item in issues if item.fix_type == "patch" and item.section), None)
    if patch:
        return "patch", _rewrite_section(markdown, patch, router, regen=False)
    regen = next((item for item in issues if item.fix_type == "regen" and item.section), None)
    if regen:
        return "regen", _rewrite_section(markdown, regen, router, regen=True)
    return "escalated", None


def _apply_auto(markdown: str, issues: list[Issue], protected: tuple[str, ...] = ()) -> str:
    codes = {item.code for item in issues}
    if "lexicon" in codes:
        markdown = _fix_lexicon(markdown, protected)
    if "numbering" in codes:
        markdown = _renumber(markdown)
    if any(item.code == "overflow" and item.kind == "cards" for item in issues):
        markdown = _cards_to_list(markdown)
    return markdown


def _rewrite_section(markdown: str, issue: Issue, router: Router, *, regen: bool) -> str | None:
    body = strip_front(markdown)
    span = _section_span(body, issue.section)
    if span is None:
        return None
    start, end = span
    old = body[start:end]
    system = (PROMPTS / "verify_patch.md").read_text(encoding="utf-8")
    if regen:
        system = system.replace("Rewrite one section", "Regenerate one section")
    user = f"ISSUE: {issue.message}\n\nSECTION:\n{old}"
    from .memory import watchout

    user += watchout(section_type=issue.section, stage="verify")
    res = router.complete("writer", system, user, temperature=0.1)
    new = res.text.strip() + "\n"
    if not new.startswith("#"):
        return None
    if not _keeps_facts(old, new):
        return None
    front, _ = _split(markdown)
    rewritten = body[:start] + new + body[end:]
    return f"{front}{rewritten}" if front else rewritten


def _keeps_facts(old: str, new: str) -> bool:
    def nums(text: str) -> list[str]:
        return [n for n in NUM_RE.findall(text) if not re.fullmatch(r"0?\d", n)]

    return all(new.count(n) >= old.count(n) for n in set(nums(old)))


def _fix_lexicon(markdown: str, protected: tuple[str, ...] = ()) -> str:
    front, body = _split(markdown)
    if protected:
        blocked = _protected_set(protected)
        body = _map_paragraphs(body, lambda part: part if _norm(part) in blocked else _lexicon_text(part))
    else:
        body = _lexicon_text(body)
    return front + body


def _lexicon_text(body: str) -> str:
    swaps = {str(item["avoid"]).lower(): str(item["use"]) for item in (load_lexicon_pairs())}

    def repl(match: re.Match) -> str:
        word = match.group(0)
        preferred = swaps.get(word.lower())
        if not preferred:
            return word
        if word.isupper():
            return preferred.upper()
        if word[0].isupper():
            return preferred[:1].upper() + preferred[1:]
        return preferred

    parts = _FENCE.split(body)
    fences = _FENCE.findall(body)
    fixed = []
    for i, part in enumerate(parts):
        fixed.append(_WORD.sub(repl, part))
        if i < len(fences):
            fixed.append(fences[i])
    return "".join(fixed)


def _without(body: str, protected: tuple[str, ...]) -> str:
    if not protected:
        return body
    blocked = _protected_set(protected)
    return _map_paragraphs(body, lambda part: "" if _norm(part) in blocked else part)


def _map_paragraphs(body: str, fn) -> str:
    parts = re.split(r"(\n\s*\n)", body)
    return "".join(part if not part.strip() else fn(part) for part in parts)


def _protected_set(protected: tuple[str, ...]) -> set[str]:
    return {_norm(part) for part in protected}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_lexicon_pairs() -> list[dict]:
    from spark_docs.brand import load_lexicon

    return list(load_lexicon().get("prefer") or [])


def _renumber(markdown: str) -> str:
    front, body = _split(markdown)
    parts = _FENCE.split(body)
    fences = _FENCE.findall(body)
    n = 0
    a = 0

    def main(match: re.Match) -> str:
        nonlocal n
        n += 1
        return f"# {n:02d} {match.group(2)}"

    def add(match: re.Match) -> str:
        nonlocal a
        a += 1
        return f"# A{a} {match.group(2)}"

    out: list[str] = []
    for i, part in enumerate(parts):
        out.append(_ADD.sub(add, _H1.sub(main, part)))
        if i < len(fences):
            out.append(fences[i])
    return front + "".join(out)


def _cards_to_list(markdown: str) -> str:
    def repl(match: re.Match) -> str:
        lines = []
        title = ""
        buf: list[str] = []
        for line in match.group(1).strip().splitlines():
            if line.startswith("### "):
                if title or buf:
                    lines.append(f"- **{title}.** {' '.join(buf)}".rstrip())
                title = line[4:].strip()
                buf = []
            elif line.strip():
                buf.append(line.strip())
        if title or buf:
            lines.append(f"- **{title}.** {' '.join(buf)}".rstrip())
        return "\n".join(lines) + "\n"

    return re.sub(r":::cards\n(.*?):::", repl, markdown, flags=re.S)


def _numbering_issue(body: str) -> Issue | None:
    nums = [int(m.group(1)) for m in _H1.finditer(body)]
    adds = [int(m.group(1)[1:]) for m in _ADD.finditer(body)]
    bad = (nums and nums != list(range(1, len(nums) + 1))) or (adds and adds != list(range(1, len(adds) + 1)))
    if not bad:
        return None
    return Issue("numbering", "section numbers are not sequential", fix_type="auto")


def _layout_issues(layout: dict, markdown: str) -> list[Issue]:
    issues = []
    cards_left = ":::cards" in markdown
    for block in layout.get("blocks") or []:
        if block.get("kind") == "cards" and not cards_left:
            continue
        x, y = float(block.get("x") or 0), float(block.get("y") or 0)
        w, h = float(block.get("w") or 0), float(block.get("h") or 0)
        bbox = [x, y, w, h]
        off = x < -1 or y < -1 or x + w > PAGE_W + 1 or y + h > PAGE_H + 1
        if off:
            issues.append(
                Issue(
                    "overflow",
                    f"{block.get('kind') or 'block'} {block.get('id') or ''} extends outside the page",
                    page=int(block.get("page") or 0) or None,
                    block_id=str(block.get("id") or "") or None,
                    bbox=bbox,
                    fix_type="auto" if block.get("kind") == "cards" else "escalate",
                    kind=str(block.get("kind") or ""),
                )
            )
        if block.get("kind") in {"kicker", "title"} and y < MARGIN_BOTTOM:
            issues.append(
                Issue(
                    "heading_low",
                    f"{block.get('kind')} {block.get('id') or ''} sits in the footer",
                    page=int(block.get("page") or 0) or None,
                    block_id=str(block.get("id") or "") or None,
                    bbox=bbox,
                    fix_type="escalate",
                    kind=str(block.get("kind") or ""),
                )
            )
    return issues


def _pdf_issues(pdf: Path | None) -> list[Issue]:
    if not pdf or not Path(pdf).is_file():
        return []
    import pymupdf

    from spark_docs import pdf as pdfmod

    issues: list[Issue] = []
    for msg in pdfmod.WARNINGS:
        issues.append(Issue("glyphs", msg, fix_type="escalate"))
    with pymupdf.open(str(pdf)) as doc:
        for i, page in enumerate(doc, 1):
            text = page.get_text()
            leak = LEAK_RE.search(text)
            if leak:
                issues.append(Issue("leak", f"page {i}: raw markdown leaked ({leak.group(0)!r})", page=i, fix_type="escalate"))
            if len(text.strip()) < 2 and not page.get_images():
                issues.append(Issue("blank_page", f"page {i} is blank", page=i, fix_type="escalate"))
    return issues


def _section_span(body: str, title: str) -> tuple[int, int] | None:
    match = re.search(rf"(?m)^# \d{{2}} .*{re.escape(title)}.*$", body)
    if not match:
        match = re.search(rf"(?m)^## .*{re.escape(title)}.*$", body)
    if not match:
        return None
    start = match.start()
    if not body.startswith("# ", start):
        prev = body.rfind("\n# ", 0, start)
        if prev != -1:
            start = prev + 1
    nxt = re.search(r"(?m)^# \d{2} ", body[match.end() :])
    end = match.end() + nxt.start() if nxt else len(body)
    return start, end


def _structural_numbers_removed(body: str) -> str:
    body = _H1.sub(lambda m: f"# {m.group(2)}", body)
    return _ADD.sub(lambda m: f"# {m.group(2)}", body)


@lru_cache(maxsize=1)
def _brand_corpus() -> str:
    parts = [json.dumps(load_facts())]
    for path in (BRAND / "boilerplate").rglob("*.md"):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _traced_corpus(source: str) -> str:
    return source + "\n" + _brand_corpus()


def _layout_brief(layout: dict) -> str:
    lines = []
    for block in (layout.get("blocks") or [])[:200]:
        lines.append(
            f"{block.get('id')} {block.get('kind')} page {block.get('page')} bbox [{block.get('x')}, {block.get('y')}, {block.get('w')}, {block.get('h')}]"
        )
    return "\n".join(lines)


def _read_layout(pdf: Path | None) -> dict:
    if not pdf:
        return {}
    path = Path(pdf).with_suffix(".layout.json")
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _page_count(pdf: Path | None, layout: dict) -> int | None:
    if layout.get("pages"):
        return int(layout["pages"])
    if not pdf or not Path(pdf).is_file():
        return None
    import pymupdf

    with pymupdf.open(str(pdf)) as doc:
        return doc.page_count


def _split(markdown: str) -> tuple[str, str]:
    if markdown.startswith("---"):
        end = markdown.find("\n---", 3)
        if end != -1:
            return markdown[: end + 4], markdown[end + 4 :]
    return "", markdown


def _bbox_ok(bbox) -> bool:
    return isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(n, (int, float)) for n in bbox)


def _limits(router: Router) -> tuple[int, float]:
    budgets = router.config.get("budgets") or {}
    return int(budgets.get("max_verify_iterations") or 4), float(budgets.get("score_delta") if budgets.get("score_delta") is not None else 1)


def _remember(router: Router, status: str, score: float, reason: str, history: list[dict], cleared: set[str], archetype: str) -> None:
    from . import store
    from .memory import note_verified

    store.event(router.run_id, "verify", {"status": status, "score": score, "reason": reason, "steps": history})
    if not cleared:
        return
    model = ""
    try:
        model = router.model_for("writer")
    except Exception:
        model = ""
    for key in cleared:
        note_verified(key, archetype=archetype, model=model)


_CRITIC_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array"}},
    "required": ["issues"],
}
