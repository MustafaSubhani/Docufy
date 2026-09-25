---
name: spark-documents
description: >-
  Creates Spark Technologies branded PDF and Word documents from markdown or
  pasted text using create_doc.py. Use when the user wants a proposal,
  documentation pack, scope of work, executive summary, or any document that
  should match the ADICC proposal sample style (navy/purple theme, divider
  lines, Spark logo on cover only, callout cards, navy tables).
---

# Spark Documents

Generate documents matching **`sample pdfs/ADICC_Proposal_updated (1).pdf`** — the single style reference. Arial, navy `#1A1A2E`, purple `#6B4EFF`, grey divider lines, callout cards, navy table headers, logo on the **cover page only**.

## Workflow

1. Structure content as Spark markdown (frontmatter + headings + optional layout blocks).
2. Save under `input/` or `examples/`.
3. Render:

```bash
py create_doc.py path/to/file.md
py create_doc.py path/to/file.md --docx
py create_doc.py path/to/file.md --company-intro --company-outro
py create_doc.py input/
```

Default output: `output/<title>.pdf`.

Do not invent a different look. Run the script rather than hand-building a PDF.

## Document pattern (ADICC)

| Part | Content | Optional? |
| --- | --- | --- |
| Cover | Logo, `PROPOSAL`, title, subtitle, prepared for/by, date, confidential box | Always |
| `# 01 …` | Executive summary / overview | Always |
| `# 02 …` | **Who We Are** + **Our Solution Areas** (company intro) | Optional |
| `# 03+ …` | Proposal body — solution, scope, commercial, etc. | Always |
| `# …` | **Why Choose Us** cards + contact banner | Optional |

Toggle company blocks with frontmatter or CLI — do **not** duplicate them in markdown when flags are on.

## Frontmatter

```yaml
---
title: Client Name
subtitle: Short offer line
doc_type: PROPOSAL
prepared_for: Client
prepared_by: Spark Technologies
date: August 2026
confidential: true
section_break: true
show_contact: true
include_company_intro: false
include_company_outro: false
footer: Spark Technologies  |  Confidential
contact:
  email: contact@sparktechnologies.ai
  phone: "+971 58 958 8081"
  website: sparktechnologies.ai
  address: Office 705, New Century City Tower, Port Saeed, Dubai, UAE
---
```

**Company sections**

- `include_company_intro: true` — inserts Who We Are + Solution Areas after section 01, then renumbers all `#` sections.
- `include_company_outro: true` — inserts Why Choose Us before the contact banner, then renumbers.
- `company_intro: path/to/custom.md` — override default intro template.
- `company_outro: path/to/custom.md` — override default outro template.

CLI equivalents: `--company-intro`, `--company-outro`.

Default templates: `spark_docs/templates/company_intro.md`, `company_outro.md`.

**Chrome (ADICC default)**

- No logo on inner pages
- No footer or page numbers on inner pages
- No purple top bar

Legacy inner logo/footer/bar: `--legacy-chrome` or set `inner_logo`, `inner_footer`, `top_bar` to `true`.

## Heading rules

| Markdown | Renders as |
| --- | --- |
| `# 01 Executive Summary` | Purple kicker `01  EXECUTIVE SUMMARY`, new page, grey rule |
| `## Proposal Overview` | Large navy title (26pt) |
| `### The Challenge` | Bold navy subhead (11pt) |
| `### The Opportunity {.accent}` | Bold purple subhead (11pt) |
| `### Module 1 — Estimation` | Bold navy module title (14pt) |
| `## Why Choose Us {.compact}` | Compact navy title (14pt) — use for outro page title |

Numbered H1s become section kickers. Keep `##` as the visible page title under the kicker.

## Layout blocks

Two columns (second heading is purple):

```markdown
:::columns
### The Challenge
Left body copy.

### The Opportunity
Right body copy.
:::
```

Callout band:

```markdown
:::callout
### Our Approach
Full-width shaded paragraph.
:::
```

Card grid (2×N):

```markdown
:::cards
### Card title
Short supporting copy.
:::
```

Contact banner: `show_contact: true` or `:::contact`.

Tables: navy header `#0D1B3E`, white header text, alternating rows.

## After generating

Check against ADICC sample:

- Cover: logo top-left only, purple `PROPOSAL`, navy title, purple rule, bold label + regular value on prepared-for lines
- Inner pages: **no logo, no footer, no top bar**
- Purple section kicker + grey rule + navy page title
- Callouts/cards in `#E5EAF1`
- Why Choose Us page: compact 14pt title, 2×3 card grid, navy contact banner

See `examples/sample.md` for a full inline example (intro + outro written in markdown). For client proposals without company pages, omit those sections and set flags to `false`.
