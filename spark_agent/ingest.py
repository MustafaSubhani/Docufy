"""Read uploaded files into one source shape.

PDF, Word, slides, spreadsheets, text, images, and HTML are supported with the
libraries already in the project. Audio and video keep their duration or file
facts; a full transcript needs a transcriber that is not installed here.
Macros and zip bombs are refused.
"""

from __future__ import annotations

import csv
import re
import wave
import zipfile
from html import unescape
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from pydantic import BaseModel, Field

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 80
MAX_ZIP_RATIO = 80
MAX_TABLE_ROWS = 5000
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"}
_AUDIO = {".wav", ".mp3", ".m4a"}
_VIDEO = {".mp4", ".mov", ".webm"}


class IngestError(RuntimeError):
    pass


class Anchor(BaseModel):
    id: str
    page: int | None = None
    text: str


class TableProfile(BaseModel):
    name: str
    columns: list[str]
    row_count: int
    numeric: dict[str, dict[str, float]] = Field(default_factory=dict)
    preview: list[dict[str, str]] = Field(default_factory=list)


class SourceDocument(BaseModel):
    id: str
    path: str
    kind: str
    title: str
    text: str
    anchors: list[Anchor] = Field(default_factory=list)
    tables: list[TableProfile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    hidden_text: bool = False


def ingest_path(path: str | Path, *, max_bytes: int = MAX_BYTES, max_pages: int = MAX_PAGES) -> SourceDocument:
    src = Path(path)
    if not src.is_file():
        raise IngestError(f"not found: {src}")
    if src.stat().st_size > max_bytes:
        raise IngestError(f"{src.name} is over the {max_bytes} byte limit")
    suffix = src.suffix.lower()
    if suffix == ".pdf":
        return _pdf(src, max_pages)
    if suffix == ".docx":
        return _docx(src)
    if suffix == ".pptx":
        return _pptx(src)
    if suffix == ".xlsx":
        return _xlsx(src)
    if suffix == ".csv":
        return _csv(src)
    if suffix in {".txt", ".md"}:
        return _text(src, src.read_text(encoding="utf-8", errors="replace"))
    if suffix in _IMAGE:
        return _image(src)
    if suffix in _AUDIO:
        return _audio(src)
    if suffix in _VIDEO:
        return _video(src)
    raise IngestError(f"unsupported file type {suffix or src.name}")


def ingest_html(html: str, *, url: str = "") -> SourceDocument:
    return _text(Path(url or "page.html"), _html_text(html), kind="url")


def ingest_url(url: str, *, max_bytes: int = MAX_BYTES, timeout: float = 20) -> SourceDocument:
    if not url.startswith(("http://", "https://")):
        raise IngestError("only http and https links are fetched")
    request = Request(url, headers={"User-Agent": "SparkDocAgent"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except URLError as exc:
        raise IngestError(f"could not fetch {url}: {exc}") from exc
    if len(raw) > max_bytes:
        raise IngestError(f"{url} is over the {max_bytes} byte limit")
    return ingest_html(raw.decode("utf-8", errors="replace"), url=url)


def _pdf(path: Path, max_pages: int) -> SourceDocument:
    import pymupdf

    doc = pymupdf.open(path)
    try:
        if doc.page_count > max_pages:
            raise IngestError(f"{path.name} has {doc.page_count} pages; the limit is {max_pages}")
        anchors: list[Anchor] = []
        tables: list[TableProfile] = []
        hidden = False
        parts: list[str] = []
        for index, page in enumerate(doc, start=1):
            hidden = hidden or _page_hides_text(page)
            text = page.get_text("text").strip()
            anchor = f"p{index}"
            if text:
                anchors.append(Anchor(id=anchor, page=index, text=text[:240]))
                parts.append(f"[{anchor}]\n{text}")
            for number, table in enumerate(getattr(page, "find_tables", lambda: [])() or [], start=1):
                extracted = table.extract() or []
                if len(extracted) < 2:
                    continue
                tables.append(_profile(f"page-{index}-table-{number}", extracted[0], extracted[1:]))
        warnings = ["hidden text was found in the PDF"] if hidden else []
        return _document(path, "pdf", "\n\n".join(parts), anchors, tables, warnings, hidden)
    finally:
        doc.close()


def _page_hides_text(page) -> bool:
    data = page.get_text("dict")
    for block in data.get("blocks") or []:
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                if (span.get("text") or "").strip() and float(span.get("size") or 12) < 1:
                    return True
    return False


def _docx(path: Path) -> SourceDocument:
    _guard_office(path)
    from docx import Document

    doc = Document(str(path))
    anchors: list[Anchor] = []
    parts: list[str] = []
    for index, paragraph in enumerate(doc.paragraphs, start=1):
        text = paragraph.text.strip()
        if not text:
            continue
        style = paragraph.style.name if paragraph.style is not None else ""
        line = f"# {text}" if style.startswith("Heading") else text
        anchors.append(Anchor(id=f"b{index}", text=text[:240]))
        parts.append(line)
    tables = []
    for index, table in enumerate(doc.tables, start=1):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if len(rows) < 2:
            continue
        tables.append(_profile(f"table-{index}", rows[0], rows[1:]))
    warnings = []
    if doc.inline_shapes:
        warnings.append(f"{len(doc.inline_shapes)} embedded image(s) were kept as files, not described")
    return _document(path, "docx", "\n\n".join(parts), anchors, tables, warnings, False)


def _pptx(path: Path) -> SourceDocument:
    names = _guard_office(path)
    slides = sorted(name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
    anchors: list[Anchor] = []
    parts: list[str] = []
    with zipfile.ZipFile(path) as package:
        for index, name in enumerate(slides, start=1):
            root = ElementTree.fromstring(package.read(name))
            bits = [node.text.strip() for node in root.iter(f"{_A}t") if node.text and node.text.strip()]
            text = "\n".join(bits)
            if not text:
                continue
            anchors.append(Anchor(id=f"s{index}", page=index, text=text[:240]))
            parts.append(f"[s{index}]\n{text}")
    return _document(path, "pptx", "\n\n".join(parts), anchors, [], [], False)


def _xlsx(path: Path) -> SourceDocument:
    _guard_office(path)
    with zipfile.ZipFile(path) as package:
        strings = _shared_strings(package)
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in package.namelist():
            raise IngestError(f"{path.name} has no worksheet")
        rows = _sheet_rows(ElementTree.fromstring(package.read(sheet_name)), strings)
    tables = [_profile("sheet1", rows[0], rows[1:])] if len(rows) > 1 else []
    preview = _profile_text(tables[0]) if tables else ""
    return _document(path, "xlsx", preview, [], tables, [], False)


def _csv(path: Path) -> SourceDocument:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.reader(handle))
    tables = [_profile(path.stem, rows[0], rows[1:])] if len(rows) > 1 else []
    text = _profile_text(tables[0]) if tables else ""
    return _document(path, "csv", text, [], tables, [], False)


def _text(path: Path, text: str, kind: str = "") -> SourceDocument:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    anchors = [Anchor(id=f"p{index}", text=part[:240]) for index, part in enumerate(paragraphs, start=1)]
    body = "\n\n".join(f"[{anchor.id}]\n{paragraphs[index]}" for index, anchor in enumerate(anchors))
    return _document(path, kind or path.suffix.lstrip(".") or "text", body, anchors, [], [], False)


def _image(path: Path) -> SourceDocument:
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
        mode = image.mode
        exif = image.getexif()
        taken = ""
        if exif:
            taken = str(exif.get(36867) or exif.get(306) or "")
    text = f"{path.name}: {width} by {height} pixels, mode {mode}."
    if taken:
        text += f" Taken {taken}."
    warnings = ["no OCR engine is installed, so text inside the image was not read"]
    return _document(path, "image", text, [Anchor(id="image", text=text)], [], warnings, False)


def _audio(path: Path) -> SourceDocument:
    detail = path.name
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as handle:
            seconds = handle.getnframes() / float(handle.getframerate() or 1)
        detail = f"{path.name}: {seconds:.1f} seconds of audio."
    warnings = ["audio was not transcribed; a transcript file was not beside it"]
    sidecar = path.with_suffix(".txt")
    if sidecar.is_file() and sidecar != path:
        text = sidecar.read_text(encoding="utf-8", errors="replace")
        warnings = ["transcript taken from the sidecar text file"]
        return _text(path, text, kind="audio")
    return _document(path, "audio", detail, [Anchor(id="audio", text=detail)], [], warnings, False)


def _video(path: Path) -> SourceDocument:
    warnings = ["video was not transcribed; ffmpeg keyframes are not extracted in this step"]
    text = f"{path.name}: video file, {path.stat().st_size} bytes."
    return _document(path, "video", text, [Anchor(id="video", text=text)], [], warnings, False)


def _html_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</p>", "\n\n", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return unescape(re.sub(r"[ \t]+", " ", cleaned)).strip()


def _guard_office(path: Path) -> list[str]:
    if not zipfile.is_zipfile(path):
        raise IngestError(f"{path.name} is not a valid office file")
    with zipfile.ZipFile(path) as package:
        names = package.namelist()
        compressed = sum(info.compress_size for info in package.infolist()) or 1
        expanded = sum(info.file_size for info in package.infolist())
        if expanded > MAX_BYTES * 4 or expanded / compressed > MAX_ZIP_RATIO:
            raise IngestError(f"{path.name} expands too far and was refused")
        if any(name.lower().endswith("vbaproject.bin") for name in names):
            raise IngestError(f"{path.name} contains macros and was refused")
        return names


def _shared_strings(package: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in package.namelist():
        return []
    root = ElementTree.fromstring(package.read(name))
    strings = []
    for item in root:
        strings.append("".join(node.text or "" for node in item.iter() if node.text))
    return strings


def _sheet_rows(root: ElementTree.Element, strings: list[str]) -> list[list[str]]:
    rows = []
    for row in root.iter():
        if not row.tag.endswith("row"):
            continue
        values = []
        for cell in row:
            if not cell.tag.endswith("c"):
                continue
            value = next((child.text or "" for child in cell if child.tag.endswith("v")), "")
            if cell.attrib.get("t") == "s" and value.isdigit():
                value = strings[int(value)] if int(value) < len(strings) else value
            values.append(value)
        if values:
            rows.append(values)
    return rows


def _profile(name: str, header: list, body: list[list]) -> TableProfile:
    columns = [str(cell or f"column_{index}") for index, cell in enumerate(header, start=1)]
    body = body[:MAX_TABLE_ROWS]
    preview = []
    numeric: dict[str, list[float]] = {column: [] for column in columns}
    for raw in body:
        record = {}
        for index, column in enumerate(columns):
            value = str(raw[index]).strip() if index < len(raw) else ""
            record[column] = value
            number = _number(value)
            if number is not None:
                numeric[column].append(number)
        if len(preview) < 5:
            preview.append(record)
    stats = {}
    for column, values in numeric.items():
        if values:
            stats[column] = {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}
    return TableProfile(name=name, columns=columns, row_count=len(body), numeric=stats, preview=preview)


def _profile_text(table: TableProfile) -> str:
    stats = ", ".join(f"{name} mean {info['mean']:.2f}" for name, info in table.numeric.items())
    lines = [f"Table {table.name}: {table.row_count} rows, columns {', '.join(table.columns)}."]
    if stats:
        lines.append(stats + ".")
    return " ".join(lines)


def _number(value: str) -> float | None:
    cleaned = value.replace(",", "").replace("%", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _document(path: Path, kind: str, text: str, anchors: list[Anchor], tables: list[TableProfile], warnings: list[str], hidden: bool) -> SourceDocument:
    return SourceDocument(
        id=_slug(path.stem),
        path=str(path),
        kind=kind,
        title=path.stem.replace("_", " ") or path.name,
        text=text.strip(),
        anchors=anchors,
        tables=tables,
        warnings=warnings,
        hidden_text=hidden,
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "source"
