#!/usr/bin/env python3
"""Create Spark Technologies branded PDF (and optional DOCX) from markdown or pasted text."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spark_docs.docx_export import build_docx
from spark_docs.parser import parse_many, parse_markdown
from spark_docs.pdf import build_pdf
from spark_docs.sections import apply_company_sections


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="create_doc",
        description="Render markdown or pasted text as a Spark Technologies branded document.",
    )
    parser.add_argument(
        "sources",
        nargs="*",
        help="Markdown files, a folder of .md files, or omit to use input/ (or stdin).",
    )
    parser.add_argument("-o", "--output", help="Output path. Default: output/<title>.pdf or .docx")
    parser.add_argument("--text", help="Paste document content directly instead of a file.")
    parser.add_argument("--title", help="Cover title (overrides frontmatter).")
    parser.add_argument("--subtitle", help="Cover subtitle.")
    parser.add_argument("--doc-type", dest="doc_type", help="Cover kicker, e.g. PROPOSAL.")
    parser.add_argument("--prepared-for", dest="prepared_for", help="Prepared-for line on the cover.")
    parser.add_argument("--prepared-by", dest="prepared_by", help="Prepared-by line on the cover.")
    parser.add_argument("--date", help="Cover date.")
    parser.add_argument(
        "--format",
        choices=["pdf", "docx", "both"],
        default=None,
        help="Output format. Defaults to pdf, or docx when --docx is set / --output ends in .docx.",
    )
    parser.add_argument("--docx", action="store_true", help="Write a .docx file (with PDF unless --format docx).")
    parser.add_argument("--docx-only", action="store_true", help="Write only a Word document, no PDF.")
    parser.add_argument("--no-cover", action="store_true", help="Skip the branded cover page.")
    parser.add_argument("--no-contact", action="store_true", help="Do not append the contact banner.")
    parser.add_argument(
        "--company-intro",
        action="store_true",
        help="Insert the Spark company intro (Who We Are / Solution Areas) after the first section.",
    )
    parser.add_argument(
        "--company-outro",
        action="store_true",
        help="Insert the Why Choose Us section before the contact banner.",
    )
    parser.add_argument(
        "--spark-content",
        action="store_true",
        help="Client document: add Spark company intro, Why Choose Us page, and contact banner.",
    )
    parser.add_argument(
        "--no-spark-content",
        action="store_true",
        help="Internal document: remove Spark company sections and contact banners.",
    )
    parser.add_argument(
        "--legacy-chrome",
        action="store_true",
        help="Use legacy inner-page logo, footer, and top bar (not ADICC sample style).",
    )
    args = parser.parse_args(argv)

    source_dir = ROOT
    if args.text is not None:
        meta, blocks = parse_markdown(args.text, source_dir=ROOT)
    else:
        paths = resolve_sources(args.sources)
        if not paths:
            if not sys.stdin.isatty():
                meta, blocks = parse_markdown(sys.stdin.read(), source_dir=ROOT)
            else:
                parser.print_help()
                print("\nNo markdown found. Put .md files in input\\ or pass a file path.", file=sys.stderr)
                return 2
        else:
            meta, blocks = parse_many(paths)
            source_dir = paths[0].parent

    if args.title:
        meta.title = args.title
    if args.subtitle:
        meta.subtitle = args.subtitle
    if args.doc_type:
        meta.doc_type = args.doc_type
    if args.prepared_for:
        meta.prepared_for = args.prepared_for
    if args.prepared_by:
        meta.prepared_by = args.prepared_by
    if args.date:
        meta.date = args.date
    if args.no_cover:
        meta.title = ""
    if args.no_contact:
        meta.show_contact = False
        blocks = [b for b in blocks if b.get("kind") != "contact"]
    if args.company_intro or args.spark_content:
        meta.include_company_intro = True
    if args.company_outro or args.spark_content:
        meta.include_company_outro = True
    if args.no_spark_content:
        meta.include_company_intro = meta.include_company_outro = False
        meta.strip_spark_content = True
        meta.show_contact = False
    if args.legacy_chrome:
        meta.inner_logo = True
        meta.inner_footer = True
        meta.top_bar = True

    blocks = apply_company_sections(meta, blocks)

    if not meta.date:
        meta.date = date.today().strftime("%B %Y")
    if not meta.title:
        for block in blocks:
            if block.get("kind") == "heading":
                meta.title = block.get("text") or "Spark Document"
                break
        else:
            meta.title = "Spark Document"

    fmt = args.format
    if args.docx_only:
        fmt = "docx"
    elif fmt is None:
        if args.output and str(args.output).lower().endswith(".docx"):
            fmt = "docx"
        elif args.docx:
            fmt = "both"
        else:
            fmt = "pdf"

    stem = slug(meta.title)
    default_ext = ".docx" if fmt == "docx" else ".pdf"
    out_path = Path(args.output) if args.output else ROOT / "output" / f"{stem}{default_ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt in {"pdf", "both"}:
        pdf_path = out_path if out_path.suffix.lower() == ".pdf" else out_path.with_suffix(".pdf")
        print(f"PDF  {build_pdf(meta, blocks, pdf_path)}")
    if fmt in {"docx", "both"}:
        docx_path = out_path if out_path.suffix.lower() == ".docx" else out_path.with_suffix(".docx")
        print(f"DOCX {build_docx(meta, blocks, docx_path)}")
    return 0


def resolve_sources(sources: list[str]) -> list[Path]:
    paths: list[Path] = []
    if not sources:
        folder = ROOT / "input"
        if folder.is_dir():
            paths = sorted(folder.glob("*.md"))
        return paths
    for src in sources:
        p = Path(src)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if p.is_dir():
            paths.extend(sorted(p.glob("*.md")))
        elif p.is_file():
            paths.append(p)
        else:
            raise SystemExit(f"Not found: {src}")
    return paths


def slug(title: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in title.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return (cleaned.strip("_") or "spark_document")[:80]


if __name__ == "__main__":
    raise SystemExit(main())
