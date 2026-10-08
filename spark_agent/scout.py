"""The research agent. A quick lookup checks a few pages. A deep run reads them and follows one gap."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from .deep import Page, chat_fetch

_DEEP = re.compile(
    r"\b(?:deep research|in depth|in-depth|thorough|comprehensive|dig(?:ging)? into|longer research)\b",
    re.I,
)


def research_depth(text: str) -> str:
    return "deep" if _DEEP.search(text or "") else "quick"


def budget(depth: str) -> dict:
    if depth == "deep":
        return {"queries": 1, "read": 0, "follow": False, "use_tree": True, "page_limit": 6}
    return {"queries": 3, "read": 5, "follow": True, "use_tree": False, "page_limit": 5}


def read_page(url: str) -> Page | None:
    return chat_fetch(url)


def read_pages(urls: list[str], limit: int = 3) -> list[Page | None]:
    """Open a handful of pages at once. The model stays on one call at a time."""
    chosen = [url for url in urls if url][:limit]
    if not chosen:
        return []

    def safe(url: str) -> Page | None:
        try:
            return read_page(url)
        except (OSError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=min(3, len(chosen))) as pool:
        return list(pool.map(safe, chosen))
