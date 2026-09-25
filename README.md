# Spark Document Creator

Turn markdown (or pasted text) into Spark Technologies branded PDF and Word files. **All styling matches `sample pdfs/ADICC_Proposal_updated (1).pdf`** — the single reference template.

Defaults: logo on cover only, no inner-page footer or top bar, purple section kickers, navy titles, grey callout cards, navy tables. Optional company intro/outro via `include_company_intro` / `include_company_outro` in frontmatter or `--company-intro` / `--company-outro` flags.

## Setup

```bash
py -3 -m pip install -r requirements.txt
```

## Create a document

```bash
py create_doc.py examples/sample.md
py create_doc.py examples/sample.md --docx
py create_doc.py input/
py create_doc.py --text "# 01 Executive Summary`n`nYour content here."
```

Drop `.md` files into `input/` and run `py create_doc.py`. Output goes to `output/`.

Cover fields live in YAML frontmatter (`title`, `subtitle`, `doc_type`, `prepared_for`, `date`, …). Layout helpers: `:::columns`, `:::callout`, `:::cards`, `:::contact`. Details are in `.cursor/skills/spark-documents/SKILL.md`.
