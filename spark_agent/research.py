"""Turn ingested files into a searchable brief.

Chunks stay near 800 tokens and keep the anchor they came from. Search is BM25
over those chunks. The brief lists findings with those anchors, plus assumptions,
risks, and gaps. It is the pack a person approves before a document is planned.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .ingest import SourceDocument, ingest_path
from .llm import estimate_tokens

CHUNK_TOKENS = 800
_DATE = re.compile(r"\b(?:\d{1,2}\s+[A-Z][a-z]+\s+\d{4}|[A-Z][a-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})\b")
_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\b")
_ENTITY = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")


class Chunk(BaseModel):
    id: str
    source: str
    anchor: str
    text: str
    tokens: int


class SourceCard(BaseModel):
    source_id: str
    title: str
    facts: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    text: str
    anchor: str
    source: str


class ResearchBrief(BaseModel):
    question: str
    findings: list[Finding] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    approved: bool = False


class Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._docs = [_terms(chunk.text) for chunk in chunks]
        self.avg = (sum(len(tokens) for tokens in self._docs) / len(self._docs)) if self._docs else 0.0
        self.df: dict[str, int] = {}
        for tokens in self._docs:
            for term in set(tokens):
                self.df[term] = self.df.get(term, 0) + 1

    def search(self, query: str, limit: int = 5) -> list[tuple[Chunk, float]]:
        terms = _terms(query)
        if not terms or not self.chunks:
            return []
        scored = [(chunk, self._score(terms, tokens)) for chunk, tokens in zip(self.chunks, self._docs, strict=True)]
        scored = [item for item in scored if item[1] > 0]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def _score(self, query: list[str], tokens: list[str]) -> float:
        if not tokens:
            return 0.0
        counts: dict[str, int] = {}
        for term in tokens:
            counts[term] = counts.get(term, 0) + 1
        score = 0.0
        total = len(self.chunks)
        for term in query:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            docs = self.df.get(term, 0)
            idf = math.log(1 + (total - docs + 0.5) / (docs + 0.5))
            denom = frequency + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / (self.avg or 1))
            score += idf * (frequency * 2.5) / denom
        return score


def chunk_document(doc: SourceDocument, limit: int = CHUNK_TOKENS) -> list[Chunk]:
    parts = [part.strip() for part in re.split(r"\n\s*\n", doc.text) if part.strip()]
    if not parts and doc.text.strip():
        parts = [doc.text.strip()]
    chunks: list[Chunk] = []
    bucket: list[str] = []
    anchor = doc.anchors[0].id if doc.anchors else doc.id
    used = 0

    def flush() -> None:
        nonlocal bucket, used
        if not bucket:
            return
        text = "\n\n".join(bucket)
        chunks.append(Chunk(id=f"{doc.id}.c{len(chunks) + 1}", source=doc.id, anchor=anchor, text=text, tokens=estimate_tokens(text)))
        bucket = []
        used = 0

    for part in parts:
        found = re.match(r"\[([^\]]+)\]", part)
        size = estimate_tokens(part)
        if bucket and used + size > limit:
            flush()
        if not bucket and found:
            anchor = found.group(1)
        bucket.append(part)
        used += size
    flush()
    return chunks


def source_card(doc: SourceDocument) -> SourceCard:
    numbers = list(dict.fromkeys(_NUMBER.findall(doc.text)))[:12]
    dates = list(dict.fromkeys(_DATE.findall(doc.text)))[:8]
    entities = list(dict.fromkeys(_ENTITY.findall(doc.text)))[:8]
    facts = []
    for sentence in re.split(r"(?<=[.!?])\s+", doc.text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if _NUMBER.search(sentence) or _DATE.search(sentence):
            facts.append(sentence[:300])
        if len(facts) >= 5:
            break
    if not facts and doc.text:
        facts.append(doc.text.split("\n", 1)[0][:300])
    for table in doc.tables:
        facts.append(f"{table.name}: {table.row_count} rows, columns {', '.join(table.columns)}.")
    questions = [sentence.strip() for sentence in re.split(r"(?<=\?)\s+", doc.text) if sentence.strip().endswith("?")][:4]
    return SourceCard(
        source_id=doc.id,
        title=doc.title,
        facts=facts,
        numbers=numbers,
        dates=dates,
        entities=entities,
        questions=questions,
        warnings=list(doc.warnings),
    )


def build_brief(docs: list[SourceDocument], question: str = "", *, approved: bool = False) -> ResearchBrief:
    cards = [source_card(doc) for doc in docs]
    index = Index([chunk for doc in docs for chunk in chunk_document(doc)])
    findings: list[Finding] = []
    if question.strip():
        for chunk, _score in index.search(question, limit=5):
            findings.append(Finding(text=_sentence(chunk.text), anchor=chunk.anchor, source=chunk.source))
    if not findings:
        for doc, card in zip(docs, cards, strict=True):
            for fact in card.facts[:2]:
                anchor = doc.anchors[0].id if doc.anchors else doc.id
                findings.append(Finding(text=fact, anchor=anchor, source=doc.id))
    risks = [warning for doc in docs for warning in doc.warnings]
    if any(doc.hidden_text for doc in docs):
        risks.append("At least one PDF contains text that is too small to read on the page.")
    gaps = []
    if question.strip() and not index.search(question, limit=1):
        gaps.append(f"None of the files mention: {question.strip()}")
    assumptions = ["Figures and names in this brief come only from the uploaded files."]
    if not docs:
        gaps.append("No files were uploaded.")
    return ResearchBrief(question=question.strip(), findings=findings[:8], assumptions=assumptions, risks=risks, gaps=gaps, approved=approved)


def render_brief(brief: ResearchBrief) -> str:
    lines = ["# Research brief", ""]
    if brief.question:
        lines.extend([f"Question: {brief.question}", ""])
    lines.append(f"Status: {'approved' if brief.approved else 'waiting for approval'}")
    lines.append("")
    lines.append("## Findings")
    if brief.findings:
        for finding in brief.findings:
            lines.append(f"- {finding.text} [{finding.source}:{finding.anchor}]")
    else:
        lines.append("- No findings yet.")
    lines.extend(["", "## Assumptions"])
    lines.extend(f"- {item}" for item in brief.assumptions)
    lines.extend(["", "## Risks"])
    lines.extend(f"- {item}" for item in brief.risks or ["None noted."])
    lines.extend(["", "## Gaps"])
    lines.extend(f"- {item}" for item in brief.gaps or ["None noted."])
    if brief.contradictions:
        lines.extend(["", "## Contradictions"])
        lines.extend(f"- {item}" for item in brief.contradictions)
    if brief.unverified:
        lines.extend(["", "## Could not verify"])
        lines.extend(f"- {item}" for item in brief.unverified)
    lines.append("")
    return "\n".join(lines)


def research_files(paths: list[str | Path], question: str = "", *, approved: bool = False) -> tuple[ResearchBrief, list[SourceDocument]]:
    docs = [ingest_path(path) for path in paths]
    return build_brief(docs, question, approved=approved), docs


def write_brief(brief: ResearchBrief, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_brief(brief), encoding="utf-8")
    return dest


def _sentence(text: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]\n?", "", text).strip()
    part = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    return part[:300]


_STOP = {"the", "and", "for", "with", "from", "that", "this", "what", "was", "were", "are", "how", "why", "when", "where", "who", "which", "into", "about", "not"}


def _terms(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2 and word not in _STOP]
