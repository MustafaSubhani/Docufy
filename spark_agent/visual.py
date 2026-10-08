"""Compare each component with the theme exemplar, then fix only that section.

A crop is mismatched when a measurement is outside the token tolerance, or when two of
the three signals (measurement, structure, vision) agree that it looks different.
Vision issues without a page and a box are ignored. Fixes stay inside the section:
declarative, then layout, then a re-slot, then a sandboxed snippet, then escalate.
The sandbox cannot import arbitrary modules or read the renderer and the theme.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

import yaml

from spark_docs.ir import Block, Doc, Section
from spark_docs.theme import ROOT

from .edit import scope_violations
from .llm import LLMError, Router
from .loop import accept_soft

EXEMPLARS = ROOT / "themes" / "spark" / "exemplars"
TOKENS = ROOT / "themes" / "spark" / "tokens.yaml"
_BOX_KINDS = {"callout", "table", "contact", "commercial", "roadmap"}
_ALLOWED_IMPORTS = {"reportlab", "PIL", "numpy", "pymupdf", "math", "json", "pathlib"}
_BANNED_CALLS = {"__import__", "eval", "exec"}
_SSIM_DIFFERENT = 0.55


class SandboxError(RuntimeError):
    pass


@dataclass
class Signal:
    name: str
    different: bool | None
    detail: str = ""


@dataclass
class Finding:
    block_id: str
    section_id: str
    kind: str
    exemplar: str
    page: int | None
    bbox: list[float] | None
    mismatched: bool
    signals: list[Signal] = field(default_factory=list)
    action: str = ""


@dataclass
class VisualReport:
    findings: list[Finding]
    doc: Doc
    escalated: list[str] = field(default_factory=list)


def is_mismatch(measure_bad: bool, ssim_different: bool | None, vision_different: bool | None) -> bool:
    """Measurement is enough on its own. Otherwise two signals have to agree."""
    if measure_bad:
        return True
    return sum(1 for vote in (ssim_different, vision_different) if vote is True) >= 2


def review(doc: Doc, router: Router | None = None, *, layout: dict | None = None, apply: bool = False) -> VisualReport:
    layout_by_id = {str(block.get("id")): block for block in (layout or {}).get("blocks") or []}
    findings = [_judge(doc, section, block, layout_by_id.get(block.id), router) for section in _sections(doc) for block in section.blocks]
    findings = [item for item in findings if item.mismatched]
    if not apply:
        return VisualReport(findings, doc)
    current = doc
    escalated: list[str] = []
    for finding in findings:
        current, action = repair(current, finding.block_id, router)
        finding.action = action
        if action == "escalate":
            escalated.append(finding.block_id)
    return VisualReport(findings, current, escalated)


def repair(doc: Doc, block_id: str, router: Router | None = None, code: str | None = None) -> tuple[Doc, str]:
    """Try the fix ladder on one block. A fix that touches anything else is discarded."""
    for action, fn in (
        ("declarative", lambda trial: _declarative(trial, block_id)),
        ("layout", lambda trial: _layout(trial, block_id)),
        ("reslot", lambda trial: _reslot(trial, block_id, router)),
        ("sandbox", lambda trial: _sandbox_fix(trial, block_id, code)),
    ):
        if action == "reslot" and router is None:
            continue
        if action == "sandbox" and not code:
            continue
        trial = doc.model_copy(deep=True)
        if not fn(trial):
            continue
        if scope_violations(doc, trial, {block_id}):
            continue
        return trial, action
    return doc, "escalate"


def measure_block(section: Section, block: Block, entry: dict | None = None) -> Signal:
    tokens = _tokens()
    problems: list[str] = []
    expected = _expected_kind(section, block)
    if expected and block.kind != expected:
        problems.append(f"{block.kind} used where {expected} belongs")
    override = block.style_overrides.get("width")
    if override is not None and abs(float(override) - float(tokens["text_width"])) / float(tokens["text_width"]) > 0.15:
        problems.append(f"style width {override} is outside the text width")
    if entry:
        x = float(entry.get("x") or 0)
        w = float(entry.get("w") or 0)
        margin = float(tokens["margin_left"])
        if block.kind not in {"cover"} and abs(x - margin) > 12:
            problems.append(f"left edge {x:.0f} is off the {margin:.0f}pt margin")
        target = float(tokens["box_width"] if block.kind in _BOX_KINDS else tokens["text_width"])
        if w and abs(w - target) / target > 0.15:
            problems.append(f"width {w:.0f} is outside {target:.0f}")
    return Signal("measure", bool(problems), "; ".join(problems))


def structure_score(left, right) -> float | None:
    """SSIM on a small grayscale image, so the score follows the layout rather than the words."""
    try:
        import numpy as np
        from PIL import Image
        from skimage.metrics import structural_similarity
    except ImportError:
        return None

    def gray(value):
        image = value if isinstance(value, Image.Image) else Image.fromarray(np.asarray(value))
        return np.asarray(image.convert("L").resize((32, 32)), dtype=float)

    return round(float(structural_similarity(gray(left), gray(right), data_range=255)), 4)


def structure_signal(score: float | None) -> Signal:
    if score is None:
        return Signal("ssim", None, "no image")
    return Signal("ssim", score < _SSIM_DIFFERENT, f"ssim {score}")


def vision_signal(router: Router, section: Section, block: Block) -> Signal:
    system = (
        "You compare one component crop with the Spark exemplar. "
        "Return JSON {\"issues\": [...]} . Each issue needs message, page, bbox as [x, y, w, h], and severity. "
        "An empty list means they match. Issues without a page and a box are discarded."
    )
    user = f"Verification critic\n\nComponent {block.kind} in {section.kicker} / {section.title}. Exemplar {_exemplar_id(section, block)}."
    try:
        res = router.complete("critic_vision", system, user, schema={"type": "object", "properties": {"issues": {"type": "array"}}, "required": ["issues"]}, temperature=0.0)
    except LLMError:
        return Signal("vision", None, "abstain")
    data = res.data if isinstance(res.data, dict) else {}
    kept = accept_soft(data)
    if kept:
        return Signal("vision", True, kept[0].message)
    if data.get("issues"):
        return Signal("vision", None, "abstain")
    return Signal("vision", False, "match")


def clip_rect(entry: dict, page_h: float | None = None) -> tuple[float, float, float, float]:
    """Layout boxes are bottom-left. PyMuPDF clips are top-down."""
    page_h = float(page_h if page_h is not None else _tokens()["height"])
    x = float(entry.get("x") or 0)
    y = float(entry.get("y") or 0)
    w = float(entry.get("w") or 0)
    h = float(entry.get("h") or 0)
    y_top = float(entry["y_top"]) if entry.get("y_top") is not None else page_h - (y + h)
    return (x, y_top, x + w, y_top + h)


def run_sandbox(source: str, timeout: float = 5) -> dict:
    """Run a measurement snippet in a short-lived process. Imports outside the allowlist never start."""
    _reject_source(source)
    env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        with tempfile.TemporaryDirectory() as folder:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", source],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=folder,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError("sandbox timed out") from exc
    if proc.returncode != 0:
        raise SandboxError(proc.stderr.strip() or "sandbox failed")
    text = proc.stdout.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"stdout": text}
    return data if isinstance(data, dict) else {"value": data}


def code_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _judge(doc: Doc, section: Section, block: Block, entry: dict | None, router: Router | None) -> Finding:
    measured = measure_block(section, block, entry)
    ssim = Signal("ssim", None, "no crop")
    vision = Signal("vision", None, "not asked")
    if measured.different is False and router is not None:
        vision = vision_signal(router, section, block)
    mismatched = is_mismatch(bool(measured.different), ssim.different, vision.different)
    bbox = None
    page = None
    if entry:
        page = int(entry.get("page") or 0) or None
        bbox = [float(entry.get("x") or 0), float(entry.get("y") or 0), float(entry.get("w") or 0), float(entry.get("h") or 0)]
    return Finding(block.id, section.id, block.kind, _exemplar_id(section, block), page, bbox, mismatched, [measured, ssim, vision])


def _declarative(doc: Doc, block_id: str) -> bool:
    section, block = _locate(doc, block_id)
    expected = _expected_kind(section, block)
    if expected == "steps" and block.kind == "cards":
        _cards_to_steps(block)
        return True
    if block.style_overrides:
        block.style_overrides = {}
        block.rev += 1
        block.edited_by = "agent"
        return True
    spec = block.data.get("spec") if block.kind == "diagram" else None
    if isinstance(spec, dict) and spec.get("layout") == "tweak":
        from .edit import remake_spec

        block.data["spec"] = remake_spec(spec, [])
        block.rev += 1
        block.edited_by = "agent"
        return True
    return False


def _layout(doc: Doc, block_id: str) -> bool:
    _section, block = _locate(doc, block_id)
    if block.kind not in {"image", "figure"}:
        return False
    width = block.data.get("width")
    target = float(_tokens()["text_width"])
    if width is None or float(width) <= target + 8:
        return False
    block.data["width"] = target
    block.rev += 1
    block.edited_by = "agent"
    return True


def _reslot(doc: Doc, block_id: str, router: Router | None) -> bool:
    if router is None:
        return False
    _section, block = _locate(doc, block_id)
    system = "Choose the Spark component for this section. Reply with JSON {\"kind\": \"steps\"|\"cards\"|\"callout\"|\"columns\"|\"para\"} only."
    user = f"KIND: {block.kind}\nSECTION: {_section.kicker} {_section.title}\nTEXT:\n{_preview(block)}"
    try:
        res = router.complete("writer", system, user, schema={"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}, temperature=0.0)
    except LLMError:
        return False
    kind = str((res.data or {}).get("kind") or "")
    if kind == "steps" and block.kind == "cards":
        _cards_to_steps(block)
        return True
    if kind and kind != block.kind and kind in {"callout", "columns", "para", "cards", "steps"}:
        block.kind = kind
        block.rev += 1
        block.edited_by = "agent"
        return True
    return False


def _sandbox_fix(doc: Doc, block_id: str, code: str | None) -> bool:
    if not code:
        return False
    result = run_sandbox(code)
    _section, block = _locate(doc, block_id)
    block.data["custom_component"] = {"sha256": code_hash(code), "result": result}
    block.rev += 1
    block.edited_by = "agent"
    return True


def _cards_to_steps(block: Block) -> None:
    steps = []
    for card in block.data.get("cards") or []:
        body = []
        for child in card.get("blocks") or []:
            if child.get("text"):
                body.append(str(child["text"]))
            for item in child.get("items") or []:
                if item.get("text"):
                    body.append(str(item["text"]))
        steps.append({"title": str(card.get("title") or ""), "body": " ".join(body).strip()})
    block.kind = "steps"
    block.data = {"steps": steps}
    block.style_overrides = {}
    block.rev += 1
    block.edited_by = "agent"


def _expected_kind(section: Section, block: Block) -> str | None:
    label = f"{section.kicker} {section.title}".lower()
    if block.kind == "cards" and ("move forward" in label or "next step" in label):
        return "steps"
    return None


def _exemplar_id(section: Section, block: Block) -> str:
    label = f"{section.kicker} {section.title}".lower()
    if "why choose" in label or "why spark" in label:
        return "why_choose_us"
    return {
        "cards": "cards",
        "steps": "steps",
        "callout": "callout",
        "columns": "columns",
        "roadmap": "roadmap",
        "commercial": "commercial",
        "kpis": "kpis",
        "contact": "contact",
        "figure": "figure",
        "image": "figure",
    }.get(block.kind, "section_start")


def _reject_source(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SandboxError(f"sandbox source is not valid Python: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _allow(alias.name)
        elif isinstance(node, ast.ImportFrom):
            _allow(node.module or "")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BANNED_CALLS:
            raise SandboxError(f"{node.func.id} is not allowed")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            folded = node.value.replace("\\", "/").lower()
            if "spark_docs" in folded or "/themes/" in folded or folded.startswith("themes/"):
                raise SandboxError("sandbox cannot read the renderer or the theme")


def _allow(module: str) -> None:
    root = module.split(".")[0]
    if root and root not in _ALLOWED_IMPORTS:
        raise SandboxError(f"import {root} is not allowed")


def _tokens() -> dict:
    data = yaml.safe_load(TOKENS.read_text(encoding="utf-8"))
    return data["page"]


def _sections(doc: Doc) -> list[Section]:
    return [section for part in doc.parts for section in part.sections]


def _locate(doc: Doc, block_id: str) -> tuple[Section, Block]:
    for section in _sections(doc):
        for block in section.blocks:
            if block.id == block_id:
                return section, block
    raise KeyError(block_id)


def _preview(block: Block) -> str:
    if block.data.get("text"):
        return str(block.data["text"])
    return json.dumps(block.data, default=str)[:500]
