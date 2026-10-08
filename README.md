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

## The studio and its agent

```bash
.\install.ps1                      # venv, Python and Node packages, setup check
.venv\Scripts\python -m spark_agent serve
```

Add an OpenAI key in Settings, Models and keys (or set `OPENAI_API_KEY`), then pick OpenAI in the composer.
Every message then goes to the agent (`spark_agent/harness.py`), one model in a tool loop on the OpenAI
Responses API:

| Piece | File | What it does |
| --- | --- | --- |
| Providers | `spark_agent/providers.py` | Responses adapter (hosted web search, streamed text and reasoning summaries, function tools, prompt cache key per chat), Ollama adapter, model list and fallback to the newest model the key has |
| Tools | `spark_agent/agent_tools.py` | `search_web` (when hosted search is off), `read_url`, `create_draft` / `edit_draft` / `rewrite_draft` / `read_draft`, `publish_draft` (the Spark template), `read_document` / `edit_document` / `revise_sections`, `check_diagram`, `template_guide`, `brand_facts`, `calculate`, `remember` / `forget`, `read_attachment`, and every MCP tool |
| Context | `spark_agent/context.py` | Stable system prompt first (cacheable), then a rolling summary of older turns, recent turns within a token budget, and a workspace snapshot each turn |
| Workspace | `spark_agent/workspace.py` | Versioned drafts, pinned notes, chat summaries |
| MCP | `spark_agent/mcp_hub.py`, `spark_agent/mcp_server.py` | MCP client (stdio, Streamable HTTP, SSE; servers in `workspace/studio/mcp.json`, edited in Settings) and Docufy as an MCP server: `python -m spark_agent mcp` |

The flow: research and write a draft in the canvas, refine it in chat or by hand, then **Set in template**
(button or "convert it to the template"). Publishing the same draft again makes the next version of the same
document. After that, edit the pages: select a section on a page and ask, use a quick change, or edit the
template source. The template, its components and diagram style are never changed by the agent.

The chat components are an OpenUI library (`web/src/openui/chat.tsx`). `npm run build --prefix web` regenerates
the prompts in `spark_agent/prompts/` from it.

Without an OpenAI key, Format only and the local model keep the classic pipeline below. The agent can run on the
local model too (Settings, experimental): small models call tools poorly.

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
