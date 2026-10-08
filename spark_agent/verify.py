"""Deterministic checks for converted documents: content preserved, render clean, template rules."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
ENUM_RE = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+)?\d{1,2}[.)][ \t]+")
LINK_RE = re.compile(r"\]\((https?://[^)\s]+)\)|(?<![(\[])(https?://[^\s)\]>]+)")
IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
FENCE_RE = re.compile(r"```(?:mermaid|mmd|diagram)")
LEAK_RE = re.compile(r"\*\*|:::|\]\(|\{\.|\|\s*---|^#{1,6}\s", re.M)


def strip_front(md: str) -> str:
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            return md[end + 4 :]
    return md


def verify_markdown(source_body: str, out_md: str) -> list[str]:
    issues = []
    out_body = strip_front(out_md)
    src_nums = Counter(NUM_RE.findall(ENUM_RE.sub("", source_body)))
    out_nums = Counter(NUM_RE.findall(ENUM_RE.sub("", out_body)))
    lost = [n for n, c in src_nums.items() if out_nums.get(n, 0) < c and not re.fullmatch(r"0?\d", n)]
    if lost:
        issues.append(f"numbers missing from output: {', '.join(lost[:12])}")
    src_links = {a or b for a, b in LINK_RE.findall(source_body)}
    out_links = {a or b for a, b in LINK_RE.findall(out_body)}
    if src_links - out_links:
        issues.append(f"{len(src_links - out_links)} links missing from output")
    if len(IMG_RE.findall(source_body)) != len(IMG_RE.findall(out_body)):
        issues.append("image count changed")
    if len(FENCE_RE.findall(source_body)) != len(FENCE_RE.findall(out_body)):
        issues.append("diagram count changed")
    fm = out_md[: out_md.find("\n---", 3)] if out_md.startswith("---") else ""
    if "doc_type: PROPOSAL" not in fm:
        titles = re.findall(r"^#{1,2}\s+(.*)$", out_body, re.M)
        if any("proposal" in t.lower() for t in titles):
            issues.append("a non-proposal document uses 'proposal' in a section title")
    from .tokens import truncation_issues

    issues += truncation_issues(out_body)
    return issues


def render_and_check(md_path: Path, out_dir: Path, docx: bool = False) -> tuple[Path | None, Path | None, list[str]]:
    import pymupdf

    from spark_docs import pdf as pdfmod
    from spark_docs.brand import contact_issues
    from spark_docs.links import check_links
    from spark_docs.parser import parse_path
    from spark_docs.sections import apply_company_sections

    issues: list[str] = []
    meta, blocks = parse_path(md_path)
    blocks = apply_company_sections(meta, blocks)
    stem = md_path.name.replace(".spark.md", "")
    pdf_path = out_dir / f"{stem}.pdf"
    pdfmod.WARNINGS.clear()
    pdfmod.build_pdf(meta, blocks, pdf_path)
    issues += [f"render: {w}" for w in pdfmod.WARNINGS]
    issues += check_links(md_path.read_text(encoding="utf-8"), pdf_path)
    issues += contact_issues(meta.contact)
    with pymupdf.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc, 1):
            text = page.get_text()
            m = LEAK_RE.search(text)
            if m:
                issues.append(f"page {i}: raw markdown leaked into the PDF ({m.group(0)!r})")
    docx_path = None
    if docx:
        from spark_docs.docx_export import build_docx

        docx_path = build_docx(meta, blocks, out_dir / f"{stem}.docx")
    return pdf_path, docx_path, issues
