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
from spark_docs.ir import document_from_parse, to_gfm, to_spark_markdown
from spark_docs.parser import parse_many, parse_markdown
from spark_docs.pdf import build_pdf
from spark_docs.sections import apply_company_sections


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "edit":
        return _edit_cli(argv[1:])
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
        choices=["pdf", "docx", "both", "spark", "gfm"],
        default=None,
        help="Output format. Defaults to pdf, or docx when --docx is set / --output ends in .docx. spark writes Spark markdown; gfm writes plain Markdown.",
    )
    parser.add_argument("--docx", action="store_true", help="Write a .docx file (with PDF unless --format docx).")
    parser.add_argument("--docx-only", action="store_true", help="Write only a Word document, no PDF.")
    parser.add_argument("--no-cover", action="store_true", help="Skip the branded cover page.")
    parser.add_argument("--no-contact", action="store_true", help="Do not append the contact banner.")
    parser.add_argument(
        "--company-intro",
        action="store_true",
        help="Insert the Spark company intro (Who We Are / Solution Areas) before the first section.",
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
    default_ext = {"docx": ".docx", "spark": ".spark.md", "gfm": ".gfm.md"}.get(fmt, ".pdf")
    out_path = Path(args.output) if args.output else ROOT / "output" / f"{stem}{default_ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt in {"pdf", "both"}:
        pdf_path = out_path if out_path.suffix.lower() == ".pdf" else out_path.with_suffix(".pdf")
        print(f"PDF  {build_pdf(meta, blocks, pdf_path)}")
    if fmt in {"docx", "both"}:
        docx_path = out_path if out_path.suffix.lower() == ".docx" else out_path.with_suffix(".docx")
        print(f"DOCX {build_docx(meta, blocks, docx_path)}")
    if fmt in {"spark", "gfm"}:
        md_path = out_path if out_path.suffix.lower() == ".md" else out_path.with_suffix(
            ".spark.md" if fmt == "spark" else ".gfm.md"
        )
        doc = document_from_parse(meta, blocks)
        if fmt == "spark":
            md_path.write_text(to_spark_markdown(doc), encoding="utf-8")
        else:
            assets = md_path.with_name(md_path.stem + "_files")
            md_path.write_text(to_gfm(doc, asset_dir=assets), encoding="utf-8")
        print(f"MD   {md_path}")
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


def _edit_cli(argv: list[str]) -> int:
    """py create_doc.py edit doc.md --block b4 --instruction \"shorten this\""""
    from spark_agent.edit import (
        EditRequest,
        EditScope,
        ImageRef,
        accept,
        apply_edit,
        lock_block,
        manual_edit,
        read_doc,
        write_doc,
    )
    from spark_agent.llm import Router

    ap = argparse.ArgumentParser(prog="create_doc edit")
    ap.add_argument("doc")
    ap.add_argument("--block")
    ap.add_argument("--section")
    ap.add_argument("--quote", help="Text to find. The edit replaces that range.")
    ap.add_argument("--ask", help="For example: shorten the second paragraph of Overview")
    ap.add_argument("--instruction", default="")
    ap.add_argument("--intent", default="rewrite")
    ap.add_argument("--apply-to", choices=["block", "section", "all_of_type", "save_as_variant"], default="block")
    ap.add_argument("--image", action="append", default=[], help="Reference image for a style edit")
    ap.add_argument("--unlock-brand", action="store_true", help="Keep measured colors instead of the Spark palette")
    ap.add_argument("--vision", action="store_true", help="Ask the vision model to read a style attachment")
    ap.add_argument("--variants", type=int, default=1)
    ap.add_argument("--pick", type=int, default=1)
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--lock")
    ap.add_argument("--unlock")
    ap.add_argument("--manual", help="Block id to replace by hand")
    ap.add_argument("--replace", help="New text for --manual")
    ap.add_argument("--tweak", action="store_true")
    ap.add_argument("--remake", action="store_true")
    ap.add_argument("--node", action="append", default=[])
    args = ap.parse_args(argv)
    path = Path(args.doc)
    doc, history = read_doc(path)
    if args.undo:
        history.undo()
        _show_current(path, history)
        print("undone")
        return 0
    if args.redo:
        history.redo()
        _show_current(path, history)
        print("redone")
        return 0
    before_text = path.read_text(encoding="utf-8")
    before = doc.model_copy(deep=True)
    note = args.instruction or args.ask or "edit"
    if args.lock or args.unlock:
        lock_block(doc, args.lock or args.unlock, locked=bool(args.lock))
        write_doc(path, doc, history, note, before, before_text)
        print(("locked " if args.lock else "unlocked ") + (args.lock or args.unlock))
        return 0
    if args.manual:
        from spark_agent.memory import note_correction

        previous = ""
        for part in before.parts:
            for section in part.sections:
                for block in section.blocks:
                    if block.id == args.manual:
                        previous = str(block.data.get("text") or "")
        manual_edit(doc, args.manual, args.replace or "")
        note_correction(previous, args.replace or "", component="para")
        write_doc(path, doc, history, note, before, before_text)
        print(f"updated {args.manual}")
        return 0
    scope, instruction = _cli_scope(doc, args)
    if scope is None:
        return 2
    intent = "tweak_diagram" if args.tweak else "regenerate_diagram" if args.remake else args.intent
    if args.image and intent == "rewrite":
        intent = "restyle"
    if args.node:
        scope = EditScope(kind="diagram_nodes", ids=[scope.ids[0], *args.node])
    request = EditRequest(
        scope=scope,
        instruction=instruction,
        intent=intent,
        attachments=[ImageRef(path=path) for path in args.image],
        apply_to=args.apply_to,
        variants=args.variants,
        brand_lock=not args.unlock_brand,
    )
    result = apply_edit(doc, request, Router() if intent != "restyle" or args.vision else None)
    if result.choices:
        print("which one?")
        for choice in result.choices:
            print(f"  - {choice}")
        return 2
    chosen = accept(result, args.pick)
    for note in result.notes:
        print(note)
    for item in result.suggestions:
        print(f"suggestion {item.block_id}: {item.message}")
    if result.diff:
        print(result.diff)
    write_doc(path, chosen, history, instruction, doc, before_text)
    print(f"accepted variant {args.pick}")
    return 0


def _show_current(path: Path, history) -> None:
    doc, written = history.current()
    path.write_text(written or "", encoding="utf-8")
    return doc


def _cli_scope(doc, args):
    from spark_agent.edit import EditScope, locate_quote, resolve_phrase

    if args.ask:
        found = resolve_phrase(doc, args.ask)
        if found.choices:
            print("which one?")
            for choice in found.choices:
                print(f"  - {choice}")
            return None, args.ask
        if found.scope is None:
            raise SystemExit(f"could not find a scope in: {args.ask}")
        return found.scope, args.instruction or args.ask
    if args.quote:
        found = locate_quote(doc, args.quote)
        if found.choices:
            print("which one?")
            for choice in found.choices:
                print(f"  - {choice}")
            return None, args.quote
        if found.scope is None:
            raise SystemExit(f"could not find {args.quote!r}")
        return found.scope, args.instruction or f"edit {args.quote}"
    if args.section:
        return EditScope(kind="section", ids=[args.section]), args.instruction
    if args.block:
        kind = "diagram" if args.tweak or args.remake else "block"
        return EditScope(kind=kind, ids=[args.block]), args.instruction
    raise SystemExit("name a scope with --block, --section, --quote, or --ask")


def slug(title: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in title.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return (cleaned.strip("_") or "spark_document")[:80]


if __name__ == "__main__":
    raise SystemExit(main())
