"""Web research with a budget, a question tree, and checked citations.

Quick, Standard, and Deep runs share one pipeline. Search connectors are
plugins. In development the connectors are DuckDuckGo and OpenAlex, and any
keyed connector stays off until its key is set. Pages are fetched in full.
A finding is kept only when its quote is on a fetched page, and only when a
primary source or two independent sources agree. Dev runs one question at a
time so a long research job does not overload the machine.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import date
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from .ingest import _html_text
from .research import Finding, ResearchBrief, _terms

BUDGETS = {
    "quick": {"rounds": 1, "searches": 8, "fetches": 12},
    "standard": {"rounds": 3, "searches": 30, "fetches": 40},
    "deep": {"rounds": 5, "searches": 48, "fetches": 60},
}
DEV_WORKERS = 1
_FETCH_BYTES = 2_000_000
_TRACK = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
_TIER2 = {"reuters.com", "bloomberg.com", "ft.com", "bbc.com", "bbc.co.uk", "thenationalnews.com", "gulfnews.com"}
_INJECT = re.compile(r"ignore (?:all |any |previous )?instructions|system prompt|you are now", re.I)
_BLOCK = re.compile(r"\b(sign in|log in|subscribe to read|paywall)\b", re.I)
_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\b")
_LINK = re.compile(r"https?://[^\s)>\"]+")
_AR = {"construction": "الإنشاءات", "output": "الناتج", "price": "السعر", "cement": "الأسمنت", "steel": "الصلب"}
_EXECUTABLE = (".exe", ".msi", ".bat", ".cmd", ".ps1", ".dll")
_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class Leaf(BaseModel):
    id: str
    question: str
    evidence: str = "official figures"
    freshness: str = ""
    geography: str = ""
    done_when: str = "a cited figure from a primary source, or two independent sources"
    status: str = "open"
    route: str = "web"


class SearchHit(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    connector: str = ""
    rank: int = 1


class Page(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    tier: int = 3
    lead: bool = False


class Claim(BaseModel):
    text: str
    quote: str
    url: str
    tier: int = 3
    lead: bool = False
    year: str = ""


class ChatResult(BaseModel):
    query: str
    pages: list[Page] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)


class DeepRun(BaseModel):
    goal: str
    depth: str
    tree: list[Leaf] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    brief: ResearchBrief
    skipped: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    searches: int = 0
    fetches: int = 0
    stopped: str = ""
    checked_on: str = ""


class MemoryConnector:
    """Returns a fixed result list. Tests and fixtures use this in place of the network."""

    def __init__(self, name: str, hits: list[SearchHit], kinds: tuple[str, ...] = ("web", "news", "stats", "academic")):
        self.name = name
        self.hits = hits
        self.kinds = kinds
        self.needs_key = False
        self.key = ""
        self.enabled = True
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        self.queries.append(query)
        return list(self.hits[:limit])


class KeyedConnector:
    def __init__(self, name: str, key: str = "", kinds: tuple[str, ...] = ("web", "news"), searcher=None):
        self.name = name
        self.key = key
        self.kinds = kinds
        self.needs_key = True
        self.enabled = True
        self.searcher = searcher
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        self.queries.append(query)
        if not self.key or self.searcher is None:
            return []
        try:
            return list(self.searcher(query, self.key, limit))
        except (OSError, ValueError, KeyError):
            return []


def plan_tree(goal: str) -> list[Leaf]:
    text = " ".join(goal.split())
    geography = "UAE" if re.search(r"\b(uae|dubai|abu dhabi|gcc)\b", text, re.I) else ""
    if re.search(r"\b(arxiv|paper|papers|segmentation|model|models)\b", text, re.I):
        route, evidence = "academic", "papers"
    elif re.search(r"\b(news|announce|announced)\b", text, re.I):
        route, evidence = "news", "news"
    else:
        route, evidence = "web", "official figures"
    return [
        Leaf(id="q1", question=text, evidence=evidence, freshness="last 12 months", geography=geography, route=route),
        Leaf(
            id="q2",
            question=f"What published evidence contradicts this: {text}",
            evidence="disconfirming",
            geography=geography,
            route=route,
        ),
    ]


def edit_leaf(tree: list[Leaf], leaf_id: str, **changes) -> list[Leaf]:
    found = False
    updated = []
    for leaf in tree:
        if leaf.id == leaf_id:
            found = True
            updated.append(leaf.model_copy(update=changes))
        else:
            updated.append(leaf)
    if not found:
        raise KeyError(leaf_id)
    return updated


def sanitize(query: str, secrets: list[str]) -> str:
    text = query
    for secret in secrets:
        if secret.strip():
            text = re.sub(re.escape(secret), " ", text, flags=re.I)
    return " ".join(text.split())


def queries_for(leaf: Leaf, *, year: int, secrets: list[str] | None = None) -> list[str]:
    hidden = secrets or []
    base = sanitize(leaf.question, hidden)
    if not base:
        return []
    out = [base]
    if leaf.freshness:
        out.append(f"{base} {year}")
    if "official" in leaf.evidence or leaf.route in {"web", "stats"}:
        out.append(f"{base} filetype:pdf")
    if leaf.route == "academic":
        out.append(f"site:arxiv.org {base}")
    if re.search(r"\b(filing|edgar|sec)\b", leaf.question, re.I):
        out.append(f"site:sec.gov {base}")
    if leaf.geography.upper() in {"UAE", "GCC"}:
        out.append(f"site:.gov.ae {base}")
        out.append(_arabic(base))
    if "contradict" in leaf.question.lower() or leaf.evidence == "disconfirming":
        out.append(f"{base} incorrect OR dispute")
    elif not any("dispute" in item for item in out):
        out.append(f"{base} dispute")
    cleaned = []
    for item in out:
        item = sanitize(item, hidden)
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned[:6]


def canonicalize(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in _TRACK]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def fuse(rankings: list[list[SearchHit]]) -> list[SearchHit]:
    """Reciprocal rank fusion. Tracking parameters are stripped before URLs are merged."""
    scores: dict[str, float] = {}
    chosen: dict[str, SearchHit] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, hit in enumerate(ranking, start=1):
            url = canonicalize(hit.url)
            if not url or url in seen:
                continue
            seen.add(url)
            scores[url] = scores.get(url, 0.0) + 1.0 / (60 + rank)
            if url not in chosen:
                chosen[url] = hit.model_copy(update={"url": url})
    ordered = sorted(scores, key=lambda url: (-scores[url], url))
    return [chosen[url] for url in ordered]


def rerank(hits: list[SearchHit], question: str, pinned: list[str] | None = None) -> list[SearchHit]:
    """Lexical rerank. A neural reranker can replace this when one is configured."""
    terms = set(_terms(question))
    pins = [item.lower() for item in pinned or []]

    def sort_key(hit: SearchHit) -> tuple[int, int]:
        host = _host(hit.url)
        pinned_first = 0 if any(host == item or host.endswith("." + item) for item in pins) else 1
        overlap = len(terms & set(_terms(f"{hit.title} {hit.snippet}")))
        return pinned_first, -overlap

    return sorted(hits, key=sort_key)


def run_research(
    goal: str,
    *,
    depth: str = "quick",
    connectors: list | None = None,
    fetch: Callable[[str], Page | None] | None = None,
    secrets: list[str] | None = None,
    year: int | None = None,
    tree: list[Leaf] | None = None,
    banned: list[str] | None = None,
    pinned: list[str] | None = None,
    allow: Callable[[str], bool] | None = None,
    judge: Callable[[str, str], bool] | None = None,
    approved: bool = False,
    today: str = "",
) -> DeepRun:
    if depth not in BUDGETS:
        raise ValueError(f"unknown depth {depth}")
    limits = BUDGETS[depth]
    leaves = [leaf.model_copy(deep=True) for leaf in (tree or plan_tree(goal))]
    active, notes = _active(connectors if connectors is not None else default_connectors())
    fetcher = fetch or fetch_url
    clock_year = year or date.today().year
    checked = today or date.today().isoformat()
    hidden = list(secrets or [])
    blocked = [item.lower() for item in banned or []]
    permit = allow or (lambda _url: True)
    cache: dict[str, Page | None] = {}
    skipped: list[str] = []
    searches = 0
    fetches = 0
    kept: list[Claim] = []
    clashes: list[str] = []
    unverified: list[str] = []
    previous = -1
    stopped = "budget"

    def pull(url: str) -> Page | None:
        nonlocal fetches
        url = canonicalize(url)
        if url in cache:
            return cache[url]
        if fetches >= limits["fetches"]:
            return None
        host = _host(url)
        if urlsplit(url).scheme not in {"http", "https"} or url.lower().endswith(_EXECUTABLE):
            skipped.append(f"refused {url}")
            cache[url] = None
            return None
        if any(host == item or host.endswith("." + item) for item in blocked):
            skipped.append(f"banned {url}")
            cache[url] = None
            return None
        if not permit(url):
            skipped.append(f"robots {url}")
            cache[url] = None
            return None
        fetches += 1
        try:
            page = fetcher(url)
        except (OSError, ValueError):
            page = None
        if page is None or _blocked(page):
            skipped.append(f"skipped {url}" if page is None else f"paywall {url}")
            cache[url] = None
            return None
        stored = page.model_copy(update={"url": url, "tier": tier_of(url), "lead": is_lead(url)})
        cache[url] = stored
        return stored

    for _ in range(limits["rounds"]):
        for leaf in leaves:
            if leaf.status == "answered" or searches >= limits["searches"]:
                continue
            for query in queries_for(leaf, year=clock_year, secrets=hidden):
                if searches >= limits["searches"]:
                    break
                searches += 1
                rankings = []
                for connector in _route(leaf, active):
                    rankings.append(connector.search(query, limit=5))
                for hit in rerank(fuse(rankings), leaf.question, pinned):
                    if fetches >= limits["fetches"]:
                        break
                    page = pull(hit.url)
                    if page is None:
                        continue
                    for link in primary_links(page)[:1]:
                        if fetches >= limits["fetches"]:
                            break
                        pull(link)
        pages = _dedupe([page for page in cache.values() if page is not None])
        extracted = [claim for page in pages for claim in extract_claims(page)]
        kept, _dropped = verify_claims(extracted, {page.url: page for page in pages}, judge)
        published, clashes, unverified = triangulate(kept)
        _mark(leaves, published, unverified)
        if previous >= 0 and len(kept) <= previous:
            stopped = "plateau"
            break
        previous = len(kept)
        if leaves and all(leaf.status == "answered" for leaf in leaves):
            stopped = "covered"
            break
    pages = _dedupe([page for page in cache.values() if page is not None])
    published, clashes, unverified = triangulate(kept)
    brief = _brief(goal, published, clashes, unverified, leaves, skipped, approved, checked)
    return DeepRun(
        goal=goal,
        depth=depth,
        tree=leaves,
        pages=pages,
        claims=kept,
        brief=brief,
        skipped=skipped,
        notes=notes,
        searches=searches,
        fetches=fetches,
        stopped=stopped,
        checked_on=checked,
    )


def extract_claims(page: Page) -> list[Claim]:
    claims = []
    for match in re.finditer(r"[^.!?\n]+[.!?]", page.text):
        sentence = " ".join(match.group(0).split())
        if _INJECT.search(sentence) or not re.search(r"\d", sentence) or len(sentence) > 400:
            continue
        year = ""
        found = re.search(r"\b(20\d\d)\b", sentence)
        if found:
            year = found.group(1)
        claims.append(Claim(text=sentence, quote=sentence, url=page.url, tier=page.tier, lead=page.lead, year=year))
    return claims


def verify_claims(
    claims: list[Claim], pages: dict[str, Page], judge: Callable[[str, str], bool] | None = None
) -> tuple[list[Claim], list[str]]:
    kept = []
    dropped = []
    for claim in claims:
        url = canonicalize(claim.url)
        page = pages.get(url)
        if page is None or not _quote_in_page(claim.quote, page.text) or not _supports(claim.text, claim.quote):
            dropped.append(url)
            continue
        if judge is not None and not judge(claim.text, claim.quote):
            dropped.append(url)
            continue
        kept.append(claim.model_copy(update={"url": url}))
    return kept, dropped


def triangulate(claims: list[Claim]) -> tuple[list[Claim], list[str], list[str]]:
    groups: dict[str, list[Claim]] = {}
    for claim in claims:
        groups.setdefault(_fingerprint(claim.text), []).append(claim)
    published: list[Claim] = []
    clashes: list[str] = []
    unverified: list[str] = []
    for group in groups.values():
        by_number: dict[str, list[Claim]] = {}
        for claim in group:
            numbers = _NUMBER.findall(claim.quote)
            by_number.setdefault(numbers[0] if numbers else "", []).append(claim)
        if len(by_number) > 1:
            primary = [number for number, items in by_number.items() if any(item.tier == 1 and not item.lead for item in items)]
            if len(primary) == 1:
                chosen = _best(by_number[primary[0]])
                published.append(chosen)
                others = ", ".join(number for number in by_number if number != primary[0])
                clashes.append(f"{chosen.quote} is used because a primary source states it. Other pages say {others}.")
            else:
                sides = [f"{items[0].quote} ({items[0].url})" for items in by_number.values()]
                clashes.append("Conflict: " + " | ".join(sides))
            continue
        hosts = {_host(claim.url) for claim in group}
        primary_claims = [claim for claim in group if claim.tier == 1 and not claim.lead]
        independent = [claim for claim in group if not claim.lead]
        if primary_claims:
            published.append(_best(primary_claims))
        elif len(hosts) >= 2 and independent:
            published.append(_best(independent))
        else:
            unverified.append(group[0].quote)
    return published, clashes, unverified


def render_deep(run: DeepRun) -> str:
    lines = [
        "# Research brief",
        "",
        f"Question: {run.goal}",
        f"Depth: {run.depth}",
        f"As of {run.checked_on}",
        f"Status: {'approved' if run.brief.approved else 'waiting for approval'}",
        f"Stopped: {run.stopped} after {run.searches} searches and {run.fetches} fetches",
        "",
        "## Questions",
    ]
    for leaf in run.tree:
        lines.append(f"- [{leaf.status}] {leaf.question}")
    lines.extend(["", "## Sources"])
    if run.pages:
        for page in run.pages:
            badge = "lead" if page.lead else f"tier {page.tier}"
            lines.append(f"- {badge}: {page.url}")
    else:
        lines.append("- No pages fetched.")
    lines.extend(["", "## Findings"])
    if run.brief.findings:
        for finding in run.brief.findings:
            lines.append(f"- {finding.text} [{finding.source}:{finding.anchor}]")
    else:
        lines.append("- No finding was supported strongly enough to state.")
    lines.extend(["", "## Contradictions"])
    lines.extend(f"- {item}" for item in run.brief.contradictions or ["None noted."])
    lines.extend(["", "## Could not verify"])
    lines.extend(f"- {item}" for item in run.brief.unverified or ["None noted."])
    lines.extend(["", "## Risks"])
    lines.extend(f"- {item}" for item in run.brief.risks or ["None noted."])
    if run.notes:
        lines.extend(["", "## Connectors"])
        lines.extend(f"- {item}" for item in run.notes)
    lines.append("")
    return "\n".join(lines)


def refresh_research(question: str, **kwargs) -> DeepRun:
    return run_research(question, **kwargs)


_CHAT_DROP = {
    "help", "want", "you", "please", "some", "where", "can", "implement",
    "preferably", "preferrably", "basically", "any", "kind", "this", "that",
    "with", "from", "into", "about", "have", "the", "and", "for", "are",
    "research", "search", "internet", "online", "web", "looking", "need",
    "your", "just", "also", "using", "use", "make", "write", "areas", "area",
    "big", "little", "me", "my", "to", "of", "in", "on", "it", "is", "or",
    "be", "do", "we", "our", "what", "which", "who", "how", "when", "there",
    "here", "more", "very", "really", "like", "such", "as", "at", "by", "so",
    "if", "but", "not", "get", "all", "too", "out", "than", "then", "them",
}
_SHORT_KEEP = {
    "ai", "uae", "oil", "gas", "tax", "data", "risk", "cost", "firm", "port",
    "rail", "grid", "mine", "crop", "loan", "fund", "tech", "sme", "gdp",
    "llm", "erp", "crm", "nlp", "hr", "sip", "pbx", "img", "api", "gpu",
    "rtp", "udp", "tcp", "tls", "voip", "gps", "rfid", "csv", "gcc", "hse",
}


def _skip_host(url: str) -> bool:
    host = _host(url)
    if host == "news.google.com":
        return False
    return host.endswith(("google.com", "duckduckgo.com", "bing.com", "microsoft.com"))


def _chat_host_penalty(hit: SearchHit) -> int:
    host = _host(hit.url)
    blob = f"{hit.title} {hit.snippet}".lower()
    if "genspark" in host or "genspark" in blob:
        return 3
    if any(token in host for token in ("medium.com", "linkedin.com", "substack.com")):
        return 2
    if re.search(r"\b(how to use ai|ai docs|document creation|research topic)\b", blob):
        return 2
    if host.startswith("learning.") or "/guide/" in hit.url.lower():
        return 2
    return 0


def parse_news(xml: str) -> list[SearchHit]:
    hits = []
    seen: set[str] = set()
    for block in re.findall(r"<item>(.*?)</item>", xml or "", re.S):
        title = unescape(_xml_text(block, "title"))
        link = unescape(_xml_text(block, "link"))
        if not title or not link.startswith("http") or link in seen:
            continue
        seen.add(link)
        hits.append(SearchHit(url=link, title=title, snippet=title, connector="news", rank=len(hits) + 1))
    return hits


def _xml_text(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
    return " ".join(match.group(1).split()) if match else ""


def _news_locale(query: str) -> dict[str, str]:
    low = (query or "").lower()
    if "pakistan" in low:
        return {"hl": "en-PK", "gl": "PK", "ceid": "PK:en"}
    if re.search(r"\b(uae|dubai|abu dhabi|gcc|uzbekistan|tashkent)\b", low):
        return {"hl": "en-AE", "gl": "AE", "ceid": "AE:en"}
    return {"hl": "en-US", "gl": "US", "ceid": "US:en"}


def search_news(query: str, limit: int = 6) -> list[SearchHit]:
    """Google News RSS. One request, and it stays up when the HTML search page is blocked."""
    params = {"q": query, **_news_locale(query)}
    target = "https://news.google.com/rss/search?" + urlencode(params)
    request = Request(target, headers={"User-Agent": _AGENT, "Accept": "application/rss+xml, application/xml"})
    with urlopen(request, timeout=12) as response:
        xml = response.read(160_000).decode("utf-8", "replace")
    return parse_news(xml)[:limit]


def chat_query(text: str) -> str:
    """A short search phrase. The word research itself pulls how-to guides."""
    raw = text or ""
    low = raw.lower()
    if "oil" in low and "gas" in low and "ai" in low:
        return "oil gas AI operational use cases"
    if "oil" in low and "gas" in low:
        return "oil gas industry AI problems"
    words = re.findall(r"[A-Za-z][A-Za-z0-9+-]{1,}", raw)
    kept: list[str] = []
    seen: set[str] = set()
    labels = {"ai": "AI", "pakistan": "Pakistan", "sip": "SIP", "pbx": "PBX", "img": "IMG", "voip": "VoIP"}
    for word in words:
        low = word.lower()
        if low in _CHAT_DROP or len(low) < 2 or low in seen:
            continue
        if word.isupper() and 2 <= len(word) <= 6:
            seen.add(low)
            kept.append(labels.get(low, word))
            continue
        if len(low) < 5 and low not in _SHORT_KEEP:
            continue
        seen.add(low)
        kept.append(labels.get(low, word))
    return " ".join(kept[:4]) or " ".join(words[:4])


def _chat_terms(query: str) -> set[str]:
    return {word.lower() for word in query.split() if len(word) > 2 or word.lower() == "ai"}


def _has_term(blob: str, term: str) -> bool:
    if len(term) <= 3:
        return re.search(rf"\b{re.escape(term)}\b", blob) is not None
    return term in blob


def _matches(blob: str, terms: set[str]) -> bool:
    low = blob.lower()
    have = {term for term in terms if _has_term(low, term)}
    if "pakistan" in terms and "pakistan" not in have:
        return False
    need = 2 if len(terms) >= 3 else 1
    return len(have) >= need


def _hit_overlap(hit: SearchHit, terms: set[str]) -> int:
    blob = f"{hit.title} {hit.snippet} {hit.url}".lower()
    return sum(1 for term in terms if _has_term(blob, term))


def _junk_line(text: str) -> bool:
    letters = sum(ch.isalpha() for ch in text)
    if letters < 24:
        return True
    return bool(re.search(r"\bdoi\.org\b|\b10\.\d{4}/", text, re.I))


def page_blurb(page: Page, terms: set[str]) -> str:
    best = ""
    best_score = 0
    for sentence in re.split(r"(?<=[.!?])\s+", page.text or ""):
        clean = " ".join(sentence.split())
        if len(clean) < 40 or len(clean) > 280 or _junk_line(clean):
            continue
        words = set(re.findall(r"[a-z0-9]{3,}", clean.lower()))
        score = len(words & terms)
        if score > best_score:
            best, best_score = clean, score
    return best


def chat_fetch(url: str) -> Page | None:
    """One HTML page, with a short timeout. PDFs and failed calls are skipped."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or url.lower().split("?", 1)[0].endswith(".pdf"):
        return None
    request = Request(url, headers={"User-Agent": _AGENT, "Accept": "text/html"})
    try:
        with urlopen(request, timeout=8) as response:
            kind = response.headers.get("Content-Type", "")
            final = response.geturl()
            if "pdf" in kind or str(final).lower().split("?", 1)[0].endswith(".pdf"):
                return None
            raw = response.read(400_000)
    except (OSError, HTTPError, TimeoutError):
        return None
    if _host(final) == "news.google.com":
        return None
    html = raw.decode("utf-8", "replace")
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    title = " ".join(title_match.group(1).split()) if title_match else ""
    text = _html_text(html)
    if len(text) < 280:
        return None
    return Page(url=final or url, title=title or url, text=text)


def chat_find(query: str, limit: int = 8, connectors: list | None = None) -> list[SearchHit]:
    """Chat search: keyed APIs, DuckDuckGo, then Google News RSS, merged."""
    conns = connectors if connectors is not None else default_connectors()
    rankings: list[list[SearchHit]] = []
    for connector in conns:
        if connector.name == "openalex":
            continue
        if getattr(connector, "needs_key", False) and not getattr(connector, "key", ""):
            continue
        try:
            hits = list(connector.search(query, limit))
        except (OSError, ValueError):
            hits = []
        if hits:
            rankings.append(hits)
    try:
        news = search_news(query, limit)
        if news:
            rankings.append(news)
    except (OSError, ValueError):
        pass
    if not rankings:
        return []
    return fuse(rankings)[:limit]


def _news_result(query: str, ranked: list, urls: list[str]) -> ChatResult:
    by_url = {canonicalize(hit.url): hit for _score, hit in ranked}
    pages = []
    for url in urls:
        hit = by_url.get(url)
        title = " ".join((hit.title if hit else "").split()) or url
        pages.append(Page(url=url, title=title, text=""))
    return ChatResult(query=query, pages=pages, findings=[""] * len(pages))


def chat_search(
    goal: str,
    *,
    query: str = "",
    search=None,
    fetch=None,
    connectors: list | None = None,
    page_limit: int = 3,
) -> ChatResult:
    """One web query and a few matching pages. The full research tree is for the deep command."""
    from concurrent.futures import ThreadPoolExecutor

    query = " ".join((query or "").split()) or chat_query(goal)
    terms = _chat_terms(query)
    loader = fetch or chat_fetch
    if search is not None:
        try:
            hits = list(search(query, 8))
        except (OSError, ValueError):
            hits = []
    else:
        hits = chat_find(query, 8, connectors)
    strict: list[tuple[int, SearchHit]] = []
    loose: list[tuple[int, SearchHit]] = []
    for hit in hits:
        if _skip_host(hit.url):
            continue
        blob = f"{hit.title} {hit.snippet} {hit.url}"
        overlap = _hit_overlap(hit, terms)
        if _matches(blob, terms):
            strict.append((overlap, hit))
        elif overlap > 0:
            loose.append((overlap, hit))
    used_strict = bool(strict)
    ranked = strict or loose
    if not ranked and hits:
        ranked = [(0, hit) for hit in hits[:5] if not _skip_host(hit.url)]
    ranked.sort(key=lambda item: (-item[0], _chat_host_penalty(item[1]), item[1].rank))
    urls = []
    for _score, hit in ranked:
        url = canonicalize(hit.url)
        if url.lower().split("?", 1)[0].endswith(".pdf") or url in urls:
            continue
        urls.append(url)
        if len(urls) >= max(1, page_limit):
            break
    if not urls:
        return ChatResult(query=query)
    if all(_host(url) == "news.google.com" for url in urls):
        return _news_result(query, ranked, urls)

    def _load(url: str):
        try:
            return loader(url)
        except (OSError, ValueError):
            return None

    workers = min(max(3, page_limit), len(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        loaded = list(pool.map(_load, urls))
    pages = []
    findings = []
    cap = max(1, page_limit)
    for page in loaded:
        if page is None or _blocked(page):
            continue
        blob = f"{page.title} {page.text[:2000]}"
        if used_strict and not _matches(blob, terms):
            continue
        pages.append(page)
        findings.append(page_blurb(page, terms))
        if len(pages) >= cap:
            break
    return ChatResult(query=query, pages=pages[:cap], findings=findings[:cap])


def tier_of(url: str) -> int:
    host = _host(url)
    if host.endswith((".gov", ".gov.ae", ".go.ae", ".gov.uk")) or host.endswith(("arxiv.org", "doi.org", "sec.gov")):
        return 1
    if host in _TIER2 or host.endswith(".edu"):
        return 2
    return 3


def is_lead(url: str) -> bool:
    host = _host(url)
    return host.endswith(("wikipedia.org", "wikidata.org"))


def primary_links(page: Page) -> list[str]:
    links = []
    for raw in _LINK.findall(page.text):
        url = canonicalize(raw.rstrip(".,"))
        if url != page.url and tier_of(url) == 1 and url not in links:
            links.append(url)
    return links


def parse_duckduckgo(html: str) -> list[SearchHit]:
    hits = []
    seen: set[str] = set()
    for match in re.finditer(r"uddg=([^&\"']+)", html):
        target = unquote(match.group(1))
        if not target.startswith("http"):
            continue
        url = canonicalize(target)
        if url in seen:
            continue
        seen.add(url)
        title = ""
        label = re.match(r"[^>]*>(.*?)</a>", html[match.end() : match.end() + 500], re.S)
        if label:
            title = " ".join(re.sub(r"<[^>]+>", "", unescape(label.group(1))).split())
        hits.append(SearchHit(url=url, title=title, snippet="", connector="ddg", rank=len(hits) + 1))
    return hits


def parse_openalex(data: dict) -> list[SearchHit]:
    hits = []
    for rank, item in enumerate(data.get("results") or [], start=1):
        location = item.get("primary_location") or {}
        url = location.get("landing_page_url") or ""
        if not url:
            continue
        hits.append(SearchHit(url=url, title=item.get("display_name") or "", snippet="", connector="openalex", rank=rank))
    return hits


def robots_allows(path: str, body: str) -> bool:
    agent = False
    disallows: list[str] = []
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        if key.lower() == "user-agent":
            agent = value == "*"
        elif agent and key.lower() == "disallow" and value:
            disallows.append(value)
    path = path or "/"
    return not any(path.startswith(rule) for rule in disallows)


def default_connectors() -> list:
    import os

    return [
        DdgConnector(),
        OpenAlexConnector(),
        KeyedConnector("serper", key=os.environ.get("SERPER_API_KEY", ""), kinds=("web", "news"), searcher=search_serper),
        KeyedConnector("brave", key=os.environ.get("BRAVE_API_KEY", ""), kinds=("web",), searcher=search_brave),
        KeyedConnector("exa", key=os.environ.get("EXA_API_KEY", ""), kinds=("web",), searcher=search_exa),
    ]


class DdgConnector(MemoryConnector):
    def __init__(self):
        super().__init__("ddg", [], kinds=("web", "news", "stats"))

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        self.queries.append(query)
        try:
            return search_duckduckgo(query, limit)
        except (OSError, ValueError):
            return []


class OpenAlexConnector(MemoryConnector):
    def __init__(self):
        super().__init__("openalex", [], kinds=("academic",))

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        self.queries.append(query)
        try:
            return search_openalex(query, limit)
        except (OSError, ValueError, KeyError):
            return []


def search_duckduckgo(query: str, limit: int = 5) -> list[SearchHit]:
    target = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    html = _http_text(target)
    return parse_duckduckgo(html)[:limit]


def search_openalex(query: str, limit: int = 5) -> list[SearchHit]:
    target = "https://api.openalex.org/works?" + urlencode({"search": query, "per-page": str(limit)})
    return parse_openalex(json.loads(_http_text(target)))[:limit]


def search_serper(query: str, key: str, limit: int = 5) -> list[SearchHit]:
    payload = json.dumps({"q": query, "num": limit}).encode()
    request = Request(
        "https://google.serper.dev/search",
        data=payload,
        headers={"X-API-KEY": key, "Content-Type": "application/json", "User-Agent": "SparkDocAgent"},
    )
    with urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    hits = []
    for rank, item in enumerate(data.get("organic") or [], start=1):
        link = item.get("link") or ""
        if link:
            hits.append(SearchHit(url=link, title=item.get("title") or "", snippet=item.get("snippet") or "", connector="serper", rank=rank))
    return hits[:limit]


def search_brave(query: str, key: str, limit: int = 5) -> list[SearchHit]:
    target = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": query, "count": str(limit)})
    request = Request(target, headers={"X-Subscription-Token": key, "User-Agent": "SparkDocAgent"})
    with urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    hits = []
    for rank, item in enumerate((data.get("web") or {}).get("results") or [], start=1):
        link = item.get("url") or ""
        if link:
            hits.append(SearchHit(url=link, title=item.get("title") or "", snippet=item.get("description") or "", connector="brave", rank=rank))
    return hits[:limit]


def search_exa(query: str, key: str, limit: int = 5) -> list[SearchHit]:
    payload = json.dumps({"query": query, "numResults": limit}).encode()
    request = Request(
        "https://api.exa.ai/search",
        data=payload,
        headers={"x-api-key": key, "Content-Type": "application/json", "User-Agent": "SparkDocAgent"},
    )
    with urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    hits = []
    for rank, item in enumerate(data.get("results") or [], start=1):
        link = item.get("url") or ""
        if link:
            hits.append(SearchHit(url=link, title=item.get("title") or "", snippet="", connector="exa", rank=rank))
    return hits[:limit]


def fetch_url(url: str) -> Page | None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or url.lower().endswith(_EXECUTABLE):
        return None
    if not _robots_allow_url(url):
        return None
    status, content_type, raw = _http_bytes(url)
    if status >= 400:
        status, content_type, raw = _http_bytes("https://web.archive.org/web/2/" + url)
        if status >= 400:
            return None
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        text = _pdf_text(raw)
        title = parts.path.rsplit("/", 1)[-1] or url
    else:
        html = raw.decode("utf-8", "replace")
        title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        title = " ".join(title_match.group(1).split()) if title_match else url
        text = _html_text(html)
    return Page(url=url, title=title, text=text)


def _brief(goal, published, clashes, unverified, leaves, skipped, approved, checked) -> ResearchBrief:
    findings = [Finding(text=claim.quote, anchor="p1", source=claim.url) for claim in published]
    open_leaves = [leaf.question for leaf in leaves if leaf.status != "answered"]
    gaps = list(unverified)
    if open_leaves:
        gaps.append("Still open: " + " | ".join(open_leaves))
    risks = list(skipped)
    assumptions = [
        "A figure is stated only when the quote appears on a page this run fetched.",
        "Instructions found inside web pages were ignored.",
        f"Checked on {checked}.",
    ]
    return ResearchBrief(
        question=goal,
        findings=findings,
        assumptions=assumptions,
        risks=risks,
        gaps=gaps,
        contradictions=clashes,
        unverified=unverified,
        approved=approved,
    )


def _active(connectors: list) -> tuple[list, list[str]]:
    active = []
    notes = []
    for connector in connectors:
        if not getattr(connector, "enabled", True):
            continue
        if getattr(connector, "needs_key", False) and not getattr(connector, "key", ""):
            notes.append(f"{connector.name} is off until a key is set")
            continue
        active.append(connector)
    return active, notes


def _route(leaf: Leaf, connectors: list) -> list:
    chosen = [connector for connector in connectors if leaf.route in connector.kinds]
    if chosen:
        return chosen
    return [connector for connector in connectors if "web" in connector.kinds]


def _mark(leaves: list[Leaf], published: list[Claim], unverified: list[str]) -> None:
    for leaf in leaves:
        words = set(_terms(leaf.question))
        if "contradict" in leaf.question.lower():
            disagreed = any(_overlaps(words, claim.text) and re.search(r"dispute|incorrect|contradict", claim.text, re.I) for claim in published)
            leaf.status = "answered" if disagreed else "open"
        elif any(_overlaps(words, claim.text) for claim in published):
            leaf.status = "answered"
        elif any(_overlaps(words, item) for item in unverified):
            leaf.status = "partial"


def _overlaps(words: set[str], text: str) -> bool:
    if not words:
        return False
    return len(words & set(_terms(text))) >= min(2, len(words))


def _dedupe(pages: list[Page]) -> list[Page]:
    kept: list[Page] = []
    for page in sorted(pages, key=lambda item: (item.tier, item.lead, item.url)):
        if any(_near_duplicate(page.text, older.text) for older in kept):
            continue
        kept.append(page)
    return kept


def _shingles(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) < 3:
        return {" ".join(words)} if words else set()
    return {" ".join(words[index : index + 3]) for index in range(len(words) - 2)}


def _near_duplicate(left: str, right: str) -> bool:
    first, second = _shingles(left), _shingles(right)
    if not first or not second:
        return False
    if len(first & second) / len(first | second) >= 0.85:
        return True
    if len(left.split()) < 80 or len(right.split()) < 80:
        return False
    return _same_page(_minhash(left), _minhash(right))


def _minhash(text: str, size: int = 16) -> tuple[int, ...]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) < 3:
        shingles = {" ".join(words)} if words else set()
    else:
        shingles = {" ".join(words[index : index + 3]) for index in range(len(words) - 2)}
    if not shingles:
        return tuple()
    values = []
    for seed in range(size):
        best = min(int(hashlib.md5(f"{seed}:{shingle}".encode()).hexdigest()[:8], 16) for shingle in shingles)
        values.append(best)
    return tuple(values)


def _same_page(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    if not left or not right or len(left) != len(right):
        return False
    same = sum(1 for a, b in zip(left, right, strict=True) if a == b)
    return same / len(left) >= 0.85


def _fingerprint(text: str) -> str:
    words = [word for word in _terms(text) if not word.isdigit()]
    return " ".join(words[:8])


def _best(claims: list[Claim]) -> Claim:
    return sorted(claims, key=lambda claim: (claim.lead, claim.tier, claim.url))[0]


def _quote_in_page(quote: str, text: str) -> bool:
    return _flat(quote) in _flat(text)


def _supports(claim: str, quote: str) -> bool:
    if any(_flat(number) not in _flat(quote) for number in _NUMBER.findall(claim)):
        return False
    terms = _terms(claim)
    if not terms:
        return True
    overlap = sum(1 for word in terms if word in set(_terms(quote)))
    return overlap / len(terms) >= 0.6


def _flat(text: str) -> str:
    return " ".join(text.lower().split())


def _blocked(page: Page) -> bool:
    return bool(_BLOCK.search(f"{page.title}\n{page.text[:180]}"))


def _arabic(text: str) -> str:
    words = [_AR.get(word.lower(), word) for word in text.split()]
    if not any("\u0600" <= char <= "\u06ff" for char in " ".join(words)):
        words.append("الإمارات")
    return " ".join(words)


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _http_text(url: str) -> str:
    status, _kind, raw = _http_bytes(url)
    if status >= 400:
        raise URLError(f"status {status}")
    return raw.decode("utf-8", "replace")


def _http_bytes(url: str) -> tuple[int, str, bytes]:
    request = Request(url, headers={"User-Agent": _AGENT, "Accept": "text/html"})
    try:
        with urlopen(request, timeout=15) as response:
            return getattr(response, "status", 200), response.headers.get("Content-Type", ""), response.read(_FETCH_BYTES)
    except HTTPError as exc:
        return exc.code, "", b""


def _robots_allow_url(url: str) -> bool:
    parts = urlsplit(url)
    robots = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        body = _http_text(robots)
    except (OSError, ValueError):
        return True
    return robots_allows(parts.path or "/", body)


def _pdf_text(raw: bytes) -> str:
    import pymupdf

    doc = pymupdf.open(stream=raw, filetype="pdf")
    try:
        return "\n".join(doc[index].get_text() for index in range(min(doc.page_count, 20)))
    finally:
        doc.close()
