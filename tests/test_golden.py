"""Golden test: render the ADICC transcription and compare it with the reference PDF."""

from pathlib import Path

import pytest

from spark_docs.conformance import compare

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "sample pdfs" / "ADICC_Proposal_updated (1).pdf"
GOLDEN_MD = ROOT / "tests" / "golden" / "adicc.md"
OUT = ROOT / "tests" / "golden" / "out"

# Pages where the reference itself is inconsistent, so exact vertical positions are not expected.
KNOWN_OFFSETS = {
    6: "reference uses 29pt before the Module 1 heading here but 47pt everywhere else",
    15: "reference switches to a different bullet style for the last list",
    16: "reference pads some roadmap rows with blank lines",
    17: "reference KPI groups use inconsistent item spacing",
    18: "reference has a blank line above the kicker (+24pt)",
    28: "reference has blank lines between pipeline sections",
    33: "reference orphans the section 10 kicker at the bottom of this page",
    34: "reference lost the section 10 kicker (see page 33); we render it as intended",
}


@pytest.fixture(scope="module")
def reports():
    from spark_docs.parser import parse_path
    from spark_docs.pdf import build_pdf

    OUT.mkdir(parents=True, exist_ok=True)
    meta, blocks = parse_path(GOLDEN_MD)
    pdf = build_pdf(meta, blocks, OUT / "adicc_test.pdf")
    return compare(REFERENCE, pdf)


def test_page_count(reports):
    assert len(reports) == 34
    assert all(r.cand_page is not None for r in reports)


def test_text_lines_match(reports):
    total = sum(r.ref_lines for r in reports)
    matched = sum(r.matched for r in reports)
    assert matched / total >= 0.97, f"only {matched}/{total} lines match the reference line breaks"


@pytest.mark.parametrize("page", [p for p in range(1, 35) if p not in KNOWN_OFFSETS])
def test_vertical_positions(reports, page):
    r = reports[page - 1]
    assert r.max_dy <= 3.5, f"page {page}: baselines off by up to {r.max_dy}pt"
    assert r.max_dx <= 2.0, f"page {page}: x positions off by up to {r.max_dx}pt"


def test_cover_exact(reports):
    assert reports[0].match_ratio == 1.0
    assert reports[0].max_dy <= 1.0
