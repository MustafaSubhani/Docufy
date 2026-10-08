"""Screenshot style edits. The words stay. Only the look of the selected blocks changes.

Colors are measured from the image and snapped to the Spark palette while brand
lock is on. A vision model may describe the layout. The change is applied in
order: an existing component, token overrides, a declarative variant, then a
sandboxed snippet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from spark_docs.ir import Block, Doc, Section

from .edit import (
    EditError,
    EditRequest,
    EditResult,
    ScopeError,
    Suggestion,
    scope_violations,
)
from .llm import LLMError, Router

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ROOT / "themes" / "spark" / "variants"
_ALLOWED_FONTS = ("arial", "courier", "dejavu", "segoe")
_LAYOUTS = {
    "cards": {"cards"},
    "steps": {"steps", "cards"},
    "columns": {"columns"},
    "tight": {"para", "list", "tight"},
    "roadmap": {"roadmap", "steps"},
    "timeline": {"roadmap", "steps"},
}


class StyleIntent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    layout: str = ""
    columns: int = 0
    items_per_row: int = 0
    alignment: str = ""
    numbering: str = ""
    icons: bool = False
    radius: float | None = None
    border: bool | None = None
    emphasis: str = ""
    spacing: str = ""
    type_scale: str = ""
    colors: list[str] = Field(default_factory=list)
    font: str = ""


@dataclass
class StyleDecision:
    rung: str
    variant: str = ""
    overrides: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    saved: str = ""


def theme_palette() -> dict[str, str]:
    data = yaml.safe_load((ROOT / "themes" / "spark" / "tokens.yaml").read_text(encoding="utf-8"))
    return {str(name): str(value).upper() for name, value in (data.get("colors") or {}).items()}


def sample_colors(path: str | Path, k: int = 4) -> list[str]:
    """Dominant colors by k-means, ignoring near-white paper."""
    import numpy as np
    from PIL import Image

    image = Image.open(path).convert("RGB")
    image.thumbnail((80, 80))
    pixels = np.asarray(image, dtype=np.float64).reshape(-1, 3)
    kept = pixels[pixels.sum(axis=1) < 740]
    if len(kept) == 0:
        kept = pixels
    centers = _kmeans(kept, min(k, len(kept)))
    return [_hex(center) for center in centers]


def snap_color(value: str, palette: dict[str, str] | None = None, brand_lock: bool = True) -> tuple[str, str]:
    """Return the color to store and a warning when it left the palette."""
    palette = palette or theme_palette()
    raw = _parse_hex(value)
    if raw is None:
        return value, ""
    nearest_name, nearest, distance = _nearest(raw, palette)
    exact = _hex(raw)
    if not brand_lock:
        return exact, ""
    if distance <= 48:
        return nearest, ""
    return nearest, f"{exact} is off the Spark palette; the closest brand color is {nearest_name} {nearest}"


def locate_preview(screenshot: str | Path, page_image: str | Path) -> dict:
    """Find a preview crop on a rendered page. Red marks are returned separately."""
    import numpy as np
    from PIL import Image

    page = Image.open(page_image).convert("RGB")
    shot = Image.open(screenshot).convert("RGB")
    scale = 1.0
    if max(page.size) > 180:
        scale = 180 / max(page.size)
    page_small = page.resize((max(1, int(page.width * scale)), max(1, int(page.height * scale))))
    shot_small = shot.resize((max(1, int(shot.width * scale)), max(1, int(shot.height * scale))))
    if shot_small.size[0] > page_small.size[0] or shot_small.size[1] > page_small.size[1]:
        shot_small.thumbnail(page_small.size)
    haystack = np.asarray(page_small.convert("L"), dtype=np.float64)
    needle = np.asarray(shot_small.convert("L"), dtype=np.float64)
    x, y, score = _match(haystack, needle)
    inv = 1 / scale
    box = {"x": x * inv, "y": y * inv, "w": shot_small.width * inv, "h": shot_small.height * inv, "score": score}
    mark = _red_box(np.asarray(shot))
    if mark:
        box["mark"] = {
            "x": box["x"] + mark[0] * inv,
            "y": box["y"] + mark[1] * inv,
            "w": mark[2] * inv,
            "h": mark[3] * inv,
        }
    return box


def restyle(doc: Doc, request: EditRequest, router: Router | None = None, variant_dir: Path | None = None) -> EditResult:
    """Apply a look change inside the scope. Block text is not rewritten."""
    trial = doc.model_copy(deep=True)
    chosen, skipped = _select(trial, request)
    if not chosen:
        if skipped:
            return EditResult("locked", doc, skipped=skipped)
        raise EditError("nothing to restyle; the target is missing or locked")
    colors = _attached_colors(request)
    intent = _intent(request.instruction, colors, router)
    decision = map_style(intent, chosen[0], request.brand_lock, _code(request.instruction))
    if request.apply_to == "save_as_variant":
        decision.saved = str(_save_variant(decision, chosen[0], variant_dir or VARIANTS))
        decision.variant = decision.variant or Path(decision.saved).stem
    allowed: set[str] = set()
    for block in chosen:
        _paint(block, decision)
        allowed.add(block.id)
    violations = scope_violations(doc, trial, allowed)
    if violations:
        raise ScopeError("style edit changed blocks outside its scope: " + ", ".join(violations))
    for block in chosen:
        original = _find(doc, block.id)
        if original.data != block.data or original.kind != block.kind:
            raise ScopeError(f"style edit changed the words of {block.id}")
    notes = list(decision.warnings)
    if decision.saved:
        notes.append(f"saved variant {decision.saved}")
    for image in request.attachments:
        if image.role == "preview" and image.page_image:
            found = locate_preview(image.path, image.page_image)
            notes.append(f"preview matched at {found['x']:.0f},{found['y']:.0f} (score {found['score']:.2f})")
    if request.attachments and router is not None and router.fake is not None:
        matched, note = judge_style(intent, request.attachments[0].path, router)
        if note:
            notes.append(note)
        if matched is False:
            notes.append("the vision check does not see a match yet")
    return EditResult(
        "ok",
        trial,
        [trial],
        [Suggestion(block.id, note) for block in chosen for note in decision.warnings],
        skipped=skipped,
        diff=f"style {decision.rung} on {', '.join(sorted(allowed))}",
        notes=notes,
    )


def map_style(intent: StyleIntent, block: Block, brand_lock: bool = True, code: str = "") -> StyleDecision:
    """Pick the earliest rung that can express the intent."""
    warnings: list[str] = []
    overrides: dict = {}
    palette = theme_palette()
    snapped = []
    for color in intent.colors:
        value, warning = snap_color(color, palette, brand_lock)
        if warning:
            warnings.append(warning)
        snapped.append(value)
    if snapped:
        overrides["colors"] = snapped
    if intent.spacing in {"tight", "airy", "regular"}:
        overrides["spacing"] = intent.spacing
    if intent.radius is not None:
        overrides["radius"] = intent.radius
    if intent.border is not None:
        overrides["border"] = intent.border
    if intent.alignment:
        overrides["alignment"] = intent.alignment
    if intent.numbering:
        overrides["numbering"] = intent.numbering
    if intent.emphasis:
        overrides["emphasis"] = intent.emphasis
    if intent.items_per_row:
        overrides["items_per_row"] = intent.items_per_row
    font = (intent.font or "").strip()
    if font and not any(font.lower().startswith(name) for name in _ALLOWED_FONTS):
        if brand_lock:
            warnings.append(f"{font} is not a Spark font; the text stays in Arial")
            overrides["font"] = "Arial"
        else:
            overrides["font"] = font
    layout = (intent.layout or "").lower()
    if layout in _LAYOUTS and block.kind in _LAYOUTS[layout]:
        return StyleDecision("variant", layout if layout != "timeline" else "roadmap", overrides, warnings)
    if overrides:
        return StyleDecision("override", block.variant, overrides, warnings)
    if layout == "custom" and code:
        from .visual import code_hash, run_sandbox

        result = run_sandbox(code)
        overrides["custom_component"] = code_hash(code)
        overrides["custom_result"] = result
        return StyleDecision("custom", block.variant, overrides, warnings)
    primitives = [{"type": "stack"}]
    if intent.columns or intent.items_per_row:
        primitives.insert(0, {"type": "grid", "columns": intent.columns or intent.items_per_row})
    if layout in {"cards", "steps", "callout"}:
        primitives.append({"type": "card"})
    overrides["primitives"] = primitives
    return StyleDecision("declarative", layout or "stack", overrides, warnings)


def judge_style(intent: StyleIntent, image: str | Path, router: Router | None = None) -> tuple[bool | None, str]:
    """Ask whether the crop still matches the requested look. No model means a color check."""
    if router is not None:
        result = router.complete(
            "critic_vision",
            "Compare the reference with the requested style. Reply with JSON {\"match\": true or false, \"note\": \"...\"}.",
            f"REQUESTED: {intent.model_dump()}\nIMAGE: {image}",
            schema={"type": "object"},
        )
        data = result.data or {}
        matched = data.get("match")
        return (bool(matched) if matched is not None else None), str(data.get("note") or "")
    if not intent.colors or not Path(image).is_file():
        return None, ""
    sampled = sample_colors(image, k=3)
    wanted = [_parse_hex(color) for color in intent.colors]
    wanted = [item for item in wanted if item is not None]
    if not wanted:
        return None, ""
    close = any(_distance(_parse_hex(color) or (0, 0, 0), target) <= 48 for color in sampled for target in wanted)
    return close, "sampled colors agree with the reference" if close else "sampled colors do not match the reference"


def _intent(instruction: str, colors: list[str], router: Router | None) -> StyleIntent:
    inferred = _infer(instruction, colors)
    if router is None:
        return inferred
    try:
        result = router.complete(
            "critic_vision",
            "Describe the look of the attachment. Do not rewrite the document text. Reply with JSON.",
            f"INSTRUCTION: {instruction}\nSAMPLED COLORS: {', '.join(colors) or 'none'}",
            schema={"type": "object"},
        )
    except LLMError:
        return inferred
    data = dict(result.data or {})
    if colors and not data.get("colors"):
        data["colors"] = colors
    parsed = StyleIntent.model_validate(data)
    if not parsed.layout:
        parsed.layout = inferred.layout
    if not parsed.spacing:
        parsed.spacing = inferred.spacing
    if not parsed.colors:
        parsed.colors = inferred.colors
    return parsed


def _infer(instruction: str, colors: list[str]) -> StyleIntent:
    text = instruction.lower()
    layout = "custom" if "custom component" in text else ""
    if not layout:
        for name in ("steps", "cards", "columns", "roadmap", "timeline", "tight"):
            if name in text:
                layout = name
                break
    if "two column" in text or "2 column" in text:
        layout = layout or "columns"
    spacing = "tight" if "tight" in text else "airy" if "airy" in text or "spacious" in text else ""
    columns = 2 if layout == "columns" or "two column" in text else 0
    font_match = re.search(r"font to ([A-Za-z0-9 ]+)", instruction, re.I)
    font = font_match.group(1).strip() if font_match else ""
    return StyleIntent(layout=layout, columns=columns, spacing=spacing, colors=list(colors), font=font)


def _select(doc: Doc, request: EditRequest) -> tuple[list[Block], list[str]]:
    named = set(request.scope.ids)
    blocks = _all_blocks(doc)
    explicit = {block.id for block in blocks if block.id in named}
    if request.scope.kind == "section":
        seeds = [block for section in _sections_of(doc, list(named)) for block in section.blocks]
    else:
        seeds = [block for block in blocks if block.id in named]
    if request.apply_to == "all_of_type":
        if not seeds:
            raise EditError("all_of_type needs a block to copy the component from")
        kind = seeds[0].kind
        pool = [block for block in blocks if block.kind == kind]
    elif request.apply_to == "section":
        pool = [block for section in _sections_of(doc, [block.id for block in seeds]) for block in section.blocks]
    else:
        pool = seeds
    skipped: list[str] = []
    chosen: list[Block] = []
    for block in pool:
        if (block.locked or block.edited_by == "user") and block.id not in explicit:
            skipped.append(block.id)
            continue
        chosen.append(block)
    return chosen, skipped


def _paint(block: Block, decision: StyleDecision) -> None:
    if decision.variant:
        block.variant = decision.variant
    block.style_overrides = {**block.style_overrides, **decision.overrides}
    block.rev += 1


def _save_variant(decision: StyleDecision, block: Block, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^a-z0-9]+", "-", (decision.variant or block.kind or "style").lower()).strip("-") or "style"
    path = folder / f"{name}.yaml"
    payload = {"name": name, "kind": block.kind, "rung": decision.rung, "style_overrides": decision.overrides}
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _attached_colors(request: EditRequest) -> list[str]:
    found: list[str] = []
    for image in request.attachments:
        if image.path and Path(image.path).is_file():
            found.extend(sample_colors(image.path))
    return found[:6]


def _code(instruction: str) -> str:
    match = re.search(r"```(?:python)?\s*\n(.*?)```", instruction, re.S)
    return match.group(1).strip() if match else ""


def _sections_of(doc: Doc, ids: list[str]) -> list[Section]:
    wanted = set(ids)
    found = []
    for section in _every_section(doc):
        if section.id in wanted or any(block.id in wanted for block in section.blocks):
            found.append(section)
    return found


def _every_section(doc: Doc) -> list[Section]:
    return [section for part in doc.parts for section in part.sections]


def _all_blocks(doc: Doc) -> list[Block]:
    return [block for section in _every_section(doc) for block in section.blocks]


def _find(doc: Doc, block_id: str) -> Block:
    for block in _all_blocks(doc):
        if block.id == block_id:
            return block
    raise EditError(f"no block {block_id}")


def _kmeans(pixels, k: int, rounds: int = 8):
    import numpy as np

    count = len(pixels)
    k = max(1, min(k, count))
    indexes = np.linspace(0, count - 1, k).astype(int)
    centers = pixels[indexes].copy()
    labels = np.zeros(count, dtype=int)
    for _ in range(rounds):
        distance = ((pixels[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = distance.argmin(axis=1)
        for index in range(k):
            group = pixels[labels == index]
            if len(group):
                centers[index] = group.mean(axis=0)
    order = sorted(range(k), key=lambda index: -int((labels == index).sum()))
    return [centers[index] for index in order]


def _match(haystack, needle) -> tuple[int, int, float]:
    import numpy as np

    height, width = haystack.shape
    box_h, box_w = needle.shape
    if box_h > height or box_w > width:
        return 0, 0, 0.0
    centered = needle - needle.mean()
    needle_norm = float(np.sqrt((centered ** 2).sum()))
    if needle_norm < 1e-6:
        target = float(needle.mean())
        best_distance = 1e18
        best = (0.0, 0, 0)
        for y in range(height - box_h + 1):
            for x in range(width - box_w + 1):
                window = haystack[y : y + box_h, x : x + box_w]
                distance = abs(float(window.mean()) - target)
                if distance < best_distance:
                    best_distance = distance
                    best = (1.0 if distance < 1 else 1.0 / (1.0 + distance), x, y)
        return best[1], best[2], best[0]
    best = (-1.0, 0, 0)
    for y in range(height - box_h + 1):
        for x in range(width - box_w + 1):
            window = haystack[y : y + box_h, x : x + box_w]
            window = window - window.mean()
            score = float((window * centered).sum() / ((float(np.sqrt((window ** 2).sum())) or 1.0) * needle_norm))
            if score > best[0]:
                best = (score, x, y)
    return best[1], best[2], best[0]


def _red_box(rgb) -> tuple[int, int, int, int] | None:
    import numpy as np

    red = (rgb[:, :, 0] > 180) & (rgb[:, :, 1] < 90) & (rgb[:, :, 2] < 90)
    ys, xs = np.nonzero(red)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


def _nearest(rgb: tuple[int, int, int], palette: dict[str, str]) -> tuple[str, str, float]:
    best = ("", "#000000", 1e9)
    for name, value in palette.items():
        parsed = _parse_hex(value)
        if parsed is None:
            continue
        distance = _distance(rgb, parsed)
        if distance < best[2]:
            best = (name, _hex(parsed), distance)
    return best


def _distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right, strict=True)) ** 0.5


def _parse_hex(value: str) -> tuple[int, int, int] | None:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        return None
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _hex(rgb) -> str:
    channels = [int(max(0, min(255, round(float(channel))))) for channel in list(rgb)[:3]]
    return "#{:02X}{:02X}{:02X}".format(*channels)
