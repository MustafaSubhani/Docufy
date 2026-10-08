"""Turn a rough diagram into mermaid the template can draw. A broken diagram is edited, not removed."""

from __future__ import annotations

import re

from spark_docs.parser import claude_diagram_spec

_FENCE = re.compile(r"```(mermaid|mmd|diagram)[^\n]*\n(.*?)```", re.I | re.S)
_HEADER = re.compile(r"(?i)^(?:flowchart|graph)\s+(TD|TB|LR|RL|BT)\b")
_ARROW = re.compile(r"(?<!-)->(?!>)")


def repair_fences(text: str) -> str:
    """Replace each diagram fence with mermaid that parses."""

    def repl(match: re.Match) -> str:
        lang = match.group(1).lower()
        source = match.group(2).strip()
        if lang == "diagram" and _ok(source, "diagram"):
            return match.group(0)
        fixed = repair_mermaid(source)
        return f"```mermaid\n{fixed}\n```"

    return _FENCE.sub(repl, text or "")


def repair_mermaid(source: str) -> str:
    raw = (source or "").strip()
    if _ok(raw, "mermaid"):
        return raw
    cleaned = _normalize(raw)
    if cleaned and _ok(cleaned, "mermaid"):
        return cleaned
    rebuilt = _rebuild(raw)
    if _ok(rebuilt, "mermaid"):
        return rebuilt
    return "flowchart LR\n  A[Start] --> B[Next]"


def _ok(source: str, lang: str) -> bool:
    spec = claude_diagram_spec(source, lang)
    return bool(spec and spec.get("nodes"))


def _normalize(source: str) -> str:
    text = source.replace("\u2192", "-->").replace("\u2014>", "-->")
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.strip().startswith("%%")]
    if not lines:
        return ""
    if re.match(r"(?i)flowchart\s*$", lines[0]) or re.match(r"(?i)graph\s*$", lines[0]):
        lines[0] = "flowchart LR"
    elif not _HEADER.match(lines[0]):
        lines = ["flowchart LR", *[_ARROW.sub("-->", line) for line in lines]]
    else:
        lines = [_ARROW.sub("-->", line) for line in lines]
    return "\n".join(lines)


def _rebuild(source: str) -> str:
    labels = _labels(source)
    if len(labels) < 2:
        labels = (labels + ["Start", "Next"])[:2]
    lines = ["flowchart LR"]
    previous = ""
    for index, label in enumerate(labels[:6], start=1):
        node = f"N{index}"
        lines.append(f"  {node}[{label}]")
        if previous:
            lines.append(f"  {previous} --> {node}")
        previous = node
    return "\n".join(lines)


def _labels(source: str) -> list[str]:
    found = [_safe(label) for label in re.findall(r"\[([^\]\n]{1,48})\]", source)]
    if len(found) < 2:
        found = []
        for part in re.split(r"\s*(?:-->|->|\u2192|,|\n|;)\s*", source):
            part = re.sub(r"(?i)^(?:flowchart|graph)\s*(?:TD|TB|LR|RL|BT)?\s*", "", part).strip()
            safe = _safe(part)
            if safe and safe.casefold() not in {"flowchart", "graph", "lr", "td", "tb", "rl", "bt"}:
                found.append(safe)
    kept: list[str] = []
    for label in found:
        if label.casefold() not in {item.casefold() for item in kept}:
            kept.append(label)
    return kept[:6]


def _safe(label: str) -> str:
    text = re.sub(r"[\[\]{}()\"'`#]", "", label)
    text = " ".join(text.split())
    return text[:40].strip()
