"""Brand pack: facts, boilerplate, archetypes, lexicon, exemplar crops."""

from pathlib import Path

import yaml

from spark_docs.brand import (
    boilerplate_blocks,
    boilerplate_text,
    contact_issues,
    facts_contact,
    lexicon_issues,
    load_archetypes,
    load_facts,
)
from spark_docs.exemplars import OUT, export_exemplars
from spark_docs.parser import parse_markdown
from spark_docs.sections import apply_company_sections, fill_placeholders
from spark_docs.theme import DEFAULT_CONFIDENTIAL, DEFAULT_CONTACT, ROOT

CLIENT = (
    "This document is confidential and intended solely for the named recipient. "
    "It contains commercially sensitive information and must not be shared, "
    "reproduced, or distributed without prior written consent from Spark Technologies."
)


def test_facts_match_the_reference_contact():
    assert DEFAULT_CONTACT["phone"] == "+971 56 956 8061"
    assert DEFAULT_CONTACT == facts_contact()
    assert DEFAULT_CONFIDENTIAL == CLIENT
    assert contact_issues({"phone": "+971 58 958 8081"})


def test_company_intro_keeps_products_and_uk_spelling():
    text = boilerplate_text("company_intro")
    assert "specialising" in text
    for product in load_facts()["products"]:
        assert product in text
    blocks = boilerplate_blocks("company_intro")
    assert any(b.get("text") == "Who We Are" for b in blocks)
    assert not lexicon_issues(text)


def test_why_choose_variants_fill_the_client_name():
    meta, _ = parse_markdown("---\ntitle: ACME\nprepared_for: ACME\nindustry: oil_and_gas\n---\n# 01 Summary\n\n## Overview\n\nBody.\n")
    meta.include_company_outro = True
    out = apply_company_sections(meta, _)
    text = " ".join(str(b) for b in out)
    assert "upstream" in text
    assert "ACME's infrastructure" in text
    assert "{{" not in text


def test_archetype_turns_spark_content_on_for_a_proposal():
    meta, blocks = parse_markdown(
        "---\narchetype: proposal\ntitle: ACME\nprepared_for: ACME\n---\n\n# 01 Executive Summary\n\n## Proposal Overview\n\nBody.\n"
    )
    out = apply_company_sections(meta, blocks)
    headings = [b["text"] for b in out if b.get("kind") == "heading" and b.get("level") == 1]
    assert "01  About Spark Technologies" in headings
    assert any(b.get("kind") == "contact" for b in out)
    assert meta.doc_type == "PROPOSAL"


def test_archetype_internal_uses_staff_confidentiality():
    meta, blocks = parse_markdown("---\narchetype: internal_report\ntitle: Notes\n---\n\n# 01 Methods\n\n## Results\n\nBody.\n")
    out = apply_company_sections(meta, blocks)
    assert meta.strip_spark_content
    assert "staff" in meta.confidential_text
    assert not any(b.get("kind") == "contact" for b in out)


def test_explicit_switch_beats_the_archetype():
    meta, blocks = parse_markdown(
        "---\narchetype: proposal\ninclude_spark_content: false\ntitle: ACME\n---\n\n# 01 Executive Summary\n\n## Overview\n\nBody.\n"
    )
    out = apply_company_sections(meta, blocks)
    assert not any("Who We Are" in (b.get("text") or "") for b in out)


def test_lexicon_flags_us_spelling_only():
    assert lexicon_issues("We are specializing in mobilization.")
    assert not lexicon_issues("We are specialising in mobilisation and a programme of work.")


def test_archetype_catalog_covers_the_document_kinds():
    found = load_archetypes()
    for name in (
        "proposal",
        "technical_document",
        "requirements_document",
        "internal_report",
        "client_guide",
        "questionnaire",
        "plan_of_action",
        "addendum",
        "addendum_technical",
    ):
        assert name in found
    assert found["requirements_document"]["doc_type"] == "DOCUMENTATION"
    labels = [slot.get("kicker") or "" for slot in found["proposal"]["slots"]]
    labels += [slot.get("title") or "" for slot in found["proposal"]["slots"]]
    assert "Executive Summary" in labels
    assert "The Path Forward" in labels


def test_path_forward_pattern_uses_placeholders():
    blocks = fill_placeholders(boilerplate_blocks("path_forward"), parse_markdown("---\nprepared_for: ADICC\n---\n")[0])
    text = " ".join(b.get("text") or "" for b in blocks)
    assert "ADICC" in text and "{{" not in text
    assert "scoping session" in text


def test_exemplar_crops_exist():
    if not (OUT / "manifest.yaml").is_file():
        export_exemplars()
    manifest = yaml.safe_load((OUT / "manifest.yaml").read_text(encoding="utf-8"))
    ids = [crop["id"] for crop in manifest["crops"]]
    assert "cover" in ids and "contact" in ids and "why_choose_us" in ids
    for crop in manifest["crops"]:
        path = OUT / crop["file"]
        assert path.is_file() and path.stat().st_size > 1000
    assert (ROOT / "sample pdfs" / "ADICC_Proposal_updated (1).pdf").is_file()
    assert Path(manifest["source"]).name.startswith("ADICC")
