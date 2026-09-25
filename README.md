# Spark Doc Agent

Spark Technologies branded PDF and Word documents from markdown, matched to
`sample pdfs/ADICC_Proposal_updated (1).pdf`. The renderer is token-driven (`themes/spark/tokens.yaml`,
measured from the reference) and ships with tools that prove the match.

## Setup

```bash
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pytest ruff numpy scikit-image
cd diagrams\node_worker && npm install        # ELK layout + DejaVu Sans for Claude-style diagrams
```

## Create a document

```bash
py create_doc.py examples/sample.md
py create_doc.py file.md --format both            # PDF + DOCX
py create_doc.py file.md --spark-content          # client doc: Who We Are, Why Choose Us, contact banner
py create_doc.py file.md --no-spark-content       # internal doc: strip company sections
```

Syntax reference: `.cursor/skills/spark-documents/SKILL.md`.

## Verify against the template

```bash
py -m spark_docs.conformance "sample pdfs/ADICC_Proposal_updated (1).pdf" tests/golden/out/adicc.pdf --out tests/golden/out/report
py -m pytest
```

`tests/golden/adicc.md` is a full transcription of the 34-page reference (made with
`py -m spark_docs.importer`). The conformance report matches every text line and gives baseline
offsets, x offsets, SSIM, and side-by-side images per page.

## Layout

```
create_doc.py            CLI
spark_docs/              parser, token-driven PDF renderer, DOCX export, sections, measure/import/conformance
diagrams/                Claude-style diagram engine (mermaid import, ELK worker, vector/PNG/SVG render)
themes/spark/            design tokens measured from the reference
tests/golden/            golden transcription, reference renders and measurements
```
