"""Link resolution and checks: markdown, bare URLs, autolinks, reference links, footnotes, anchors."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BARE_RE = re.compile(r"(?<![\w/\[])https?://[^\s)\]>\"']+")
ANGLE_RE = re.compile(r"<(https?://[^>\s]+)>")
DEF_RE = re.compile(r"^\[([^\]]+)\]:\s+(\S+)\s*$")
NOTE_RE = re.compile(r"^\[\^([^\]]+)\]:\s*(.*)$")
NOTE_USE_RE = re.compile(r"\[\^([^\]]+)\]")
REF_USE_RE = re.compile(r"\[([^\]]+)\]\[([^\]]*)\]")
CHILD_KEYS = ("blocks", "columns", "cards", "steps", "groups", "items")


def slug(text: str) -> str:
    text = re.sub(r"^(?:A?\d+(?:\.\d+)?)[.)\s:\-–—]+", "", text.strip(), flags=re.I)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "section"


def _apply_refs(text: str, defs: dict[str, str], notes: dict[str, str]) -> str:
    def note_sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in notes:
            return m.group(0)
        return f"[{key}](#fn-{key})"

    text = NOTE_USE_RE.sub(note_sub, text)

    def ref_sub(m: re.Match) -> str:
        key = (m.group(2) or m.group(1)).lower()
        url = defs.get(key)
        if not url:
            return m.group(0)
        return f"[{m.group(1)}]({url})"

    return REF_USE_RE.sub(ref_sub, text)


def _walk(blocks: list, fn) -> list:
    kept = []
    for b in blocks:
        if not isinstance(b, dict):
            kept.append(b)
            continue
        for key in CHILD_KEYS:
            val = b.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                b[key] = _walk(val, fn)
        if fn(b):
            kept.append(b)
    return kept


def resolve_blocks(blocks: list[dict]) -> list[dict]:
    """Turn reference-style links and footnotes into ordinary markdown links. No-op when unused."""
    defs: dict[str, str] = {}
    notes: dict[str, str] = {}

    def collect(b: dict) -> bool:
        if b.get("kind") != "para":
            return True
        text = (b.get("text") or "").strip()
        note = NOTE_RE.match(text)
        if note:
            notes[note.group(1)] = note.group(2)
            return True
        ref = DEF_RE.match(text)
        if ref:
            defs[ref.group(1).lower()] = ref.group(2)
        return True

    def rewrite(b: dict) -> bool:
        if b.get("kind") == "para":
            text = (b.get("text") or "").strip()
            note = NOTE_RE.match(text)
            if note:
                b["text"] = f"[#fn-{note.group(1)}] {_apply_refs(note.group(2), defs, notes)}".strip()
                return True
            if DEF_RE.match(text):
                return False
        if isinstance(b.get("text"), str):
            b["text"] = _apply_refs(b["text"], defs, notes)
        if b.get("kind") == "list":
            for item in b.get("items") or []:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    item["text"] = _apply_refs(item["text"], defs, notes)
        for step in b.get("steps") or []:
            if isinstance(step, dict):
                for key in ("title", "body"):
                    if isinstance(step.get(key), str):
                        step[key] = _apply_refs(step[key], defs, notes)
        return True

    _walk(blocks, collect)
    return _walk(blocks, rewrite)


def _valid_url(href: str) -> bool:
    parsed = urlparse(href.strip())
    if parsed.scheme in {"http", "https", "mailto"} and (parsed.netloc or parsed.scheme == "mailto" and parsed.path):
        return " " not in href
    return False


def destinations(md: str) -> set[str]:
    found = set()
    for heading in HEADING_RE.findall(md):
        found.add(slug(heading))
    for note in re.findall(r"(?m)^\[\^([^\]]+)\]:", md):
        found.add(f"fn-{note}")
    return found


def iter_hrefs(md: str) -> list[str]:
    hrefs = [h.strip() for _, h in MD_LINK_RE.findall(md)]
    hrefs += BARE_RE.findall(md)
    hrefs += ANGLE_RE.findall(md)
    for note in re.findall(r"\[\^([^\]]+)\]", md):
        hrefs.append(f"#fn-{note}")
    return hrefs


def check_links(md: str, pdf: Path | None = None, reach: bool = False) -> list[str]:
    """Flag broken anchors and illegal URLs. Network reachability is off unless reach=True."""
    issues: list[str] = []
    dest = destinations(md)
    seen: set[str] = set()
    hrefs_extra: list[str] = []
    extra_seen: set[str] = set()
    defs = {k.lower(): v for k, v in re.findall(r"(?m)^\[([^\]]+)\]:\s+(\S+)", md) if not k.startswith("^")}
    for label, key in re.findall(r"\[([^\]]+)\]\[([^\]]*)\]", md):
        name = (key or label).lower()
        url = defs.get(name)
        if not url:
            issues.append(f"link target missing: [{label}][{key or label}]")
        elif url not in extra_seen:
            extra_seen.add(url)
            hrefs_extra.append(url)
    for href in iter_hrefs(md) + hrefs_extra:
        if href in seen:
            continue
        seen.add(href)
        if href.startswith("#"):
            target = href[1:].split()[0]
            if target not in dest and slug(target) not in dest:
                issues.append(f"link target missing: {href}")
            continue
        if not _valid_url(href):
            issues.append(f"invalid link: {href}")
            continue
        if reach and href.startswith("http") and not _reachable(href):
            issues.append(f"link unreachable: {href}")
    if pdf is not None and Path(pdf).is_file():
        issues += _pdf_link_issues(Path(pdf))
    return issues


def _pdf_link_issues(pdf: Path) -> list[str]:
    import pymupdf

    issues = []
    with pymupdf.open(str(pdf)) as doc:
        for page in doc:
            for link in page.get_links():
                uri = link.get("uri") or ""
                if uri and not _valid_url(uri):
                    issues.append(f"invalid link: {uri}")
    return issues


def _reachable(url: str) -> bool:
    import urllib.request

    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            return resp.status < 400
    except Exception:
        return False
