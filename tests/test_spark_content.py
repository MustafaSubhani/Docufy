from spark_docs.parser import parse_markdown
from spark_docs.sections import apply_company_sections

DOC = """---
title: ACME
prepared_for: ACME
---

# 01 Executive Summary

## Proposal Overview

Body.

# 02 Solution Overview

## Our Assessment

Body.
"""


def headings(blocks):
    return [b["text"] for b in blocks if b.get("kind") == "heading" and b.get("level") == 1]


def test_switch_on_adds_intro_outro_and_renumbers():
    meta, blocks = parse_markdown(DOC)
    meta.include_company_intro = meta.include_company_outro = True
    out = apply_company_sections(meta, blocks)
    assert headings(out) == ["01  Executive Summary", "02  About Spark Technologies", "03  Solution Overview", "04  Why Spark Technologies"]
    text = " ".join(str(b) for b in out)
    assert "ACME sees measurable results" in text and "ACME's infrastructure" in text
    assert "{{" not in text
    assert out[-1]["kind"] == "contact"
    assert all(b.get("origin") == "spark_content" for b in out if "Who We Are" in str(b.get("text")))


def test_switch_off_strips_company_sections():
    meta, blocks = parse_markdown(DOC)
    meta.include_company_intro = meta.include_company_outro = True
    with_spark = apply_company_sections(meta, blocks)
    meta.include_company_intro = meta.include_company_outro = False
    meta.strip_spark_content = True
    out = apply_company_sections(meta, with_spark)
    assert headings(out) == ["01  Executive Summary", "02  Solution Overview"]
    assert not any(b.get("kind") == "contact" for b in out)


def test_no_duplicate_intro():
    src = DOC + "\n# 03 About Spark Technologies\n\n## Who We Are\n\nOurs.\n"
    meta, blocks = parse_markdown(src)
    meta.include_company_intro = True
    out = apply_company_sections(meta, blocks)
    assert sum(1 for b in out if b.get("kind") == "heading" and b.get("text") == "Who We Are") == 1
