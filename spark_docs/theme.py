"""Spark Technologies document theme — matched to ADICC_Proposal_updated (1).pdf."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
LOGO_PATH = ASSETS / "spark-logo.png"

# Brand colors sampled from ADICC_Proposal_updated.pdf
NAVY = HexColor("#1A1A2E")
NAVY_DEEP = HexColor("#0D1B3E")  # table headers, contact banner
PURPLE = HexColor("#6B4EFF")
PURPLE_LINE = HexColor("#6C3FE0")
PURPLE_SOFT = HexColor("#8B7CFF")
BLUE_ACCENT = HexColor("#4A90E2")
BODY = HexColor("#3B3B3B")
MUTED = HexColor("#444444")
SUBTLE = HexColor("#64748B")
FOOTER = HexColor("#888888")
RULE = HexColor("#C8C8C8")
RULE_SOFT = HexColor("#E8EAEE")
CALLOUT_BG = HexColor("#E5EAF1")
COVER_BOX_BG = HexColor("#F8F7FE")
TABLE_ALT = HexColor("#F5F7FA")
WHITE = HexColor("#FFFFFF")
PAGE_BG = HexColor("#FFFFFF")

PAGE_SIZE = A4
PAGE_W, PAGE_H = A4

MARGIN_LEFT = 60
MARGIN_RIGHT = 60
MARGIN_TOP = 56
MARGIN_BOTTOM = 48
CONTENT_W = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT

FONT = "Arial"
FONT_BOLD = "Arial-Bold"
FONT_ITALIC = "Arial-Italic"
FONT_BOLD_ITALIC = "Arial-BoldItalic"
FONT_MONO = "Courier"

DEFAULT_CONFIDENTIAL = (
    "This document is confidential and intended solely for the named recipient. "
    "It contains commercially sensitive information and must not be shared, "
    "reproduced, or distributed without prior written consent from Spark Technologies."
)

DEFAULT_CONTACT = {
    "email": "contact@sparktechnologies.ai",
    "phone": "+971 58 958 8081",
    "website": "sparktechnologies.ai",
    "address": "Office 705, New Century City Tower, Port Saeed, Dubai, UAE",
}


@dataclass
class Meta:
    title: str = ""
    subtitle: str = ""
    doc_type: str = "DOCUMENT"
    prepared_for: str = ""
    prepared_by: str = "Spark Technologies"
    date: str = ""
    confidential: bool = True
    confidential_text: str = DEFAULT_CONFIDENTIAL
    section_break: bool = True
    show_contact: bool = False
    footer: str = "Spark Technologies  |  Confidential"
    client: str = ""
    contact: dict = field(default_factory=lambda: dict(DEFAULT_CONTACT))
    include_company_intro: bool = False
    include_company_outro: bool = False
    strip_spark_content: bool = False
    company_intro_path: str = ""
    company_outro_path: str = ""
    inner_logo: bool = False
    inner_footer: bool = False
    top_bar: bool = False
    asset_roots: list[str] = field(default_factory=list)

    def has_cover(self) -> bool:
        return bool(self.title.strip())


def merge_contact(raw: dict | None) -> dict:
    data = dict(DEFAULT_CONTACT)
    if raw:
        data.update({k: v for k, v in raw.items() if v})
    return data
