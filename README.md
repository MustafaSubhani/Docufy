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

## Smart Convert (agent)

Turns any markdown into template-correct Spark markdown, then PDF. The model only plans structure; source text, numbers, links, images and diagrams are copied unchanged.

```bash
py -m spark_agent convert input/WALL_SEGMENTATION_REPORT.md          # local Ollama (config/models.yaml, profile dev)
py -m spark_agent convert file.md --profile claude --docx            # once an Anthropic key is set
py -m spark_agent convert file.md --no-llm                           # heading-based plan, no model
py -m spark_agent usage                                              # token usage per run
```

Dev profile uses `ollama/qwen2.5-coder:7b` with a 16k context. Pull `qwen3:8b` and point `config/models.yaml` at it for better planning. API profiles run section planning in parallel.

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
