# File: backend/app/ingestion/parsers/docx_parser.py
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from app.ingestion.chunker import ParsedSection

logger = logging.getLogger(__name__)

try:
    import docx  # type: ignore[import-not-found]
    from docx.oxml.table import CT_Tbl  # type: ignore[import-not-found]
    from docx.oxml.text.paragraph import CT_P  # type: ignore[import-not-found]
    from docx.table import Table  # type: ignore[import-not-found]
    from docx.text.paragraph import Paragraph  # type: ignore[import-not-found]

    _HAS_PYTHON_DOCX = True
except Exception:  # pragma: no cover
    docx = None  # type: ignore[assignment]
    CT_Tbl = None  # type: ignore[assignment,misc]
    CT_P = None  # type: ignore[assignment,misc]
    Table = None  # type: ignore[assignment,misc]
    Paragraph = None  # type: ignore[assignment,misc]
    _HAS_PYTHON_DOCX = False

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_RE = re.compile(r"^(?:heading|tiêu\s*đề)\s*(\d+)?", re.IGNORECASE)


def _clean_cell_text(text: str) -> str:
    """Collapse internal newlines and excessive spaces inside a single table cell."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return cleaned.replace("|", "\\|")


def _format_table_as_markdown(rows: list[list[str]]) -> str:
    """Convert a 2D list of table cells into pipe-delimited Markdown table rows (`| Col1 | Col2 |`)."""
    non_empty_rows: list[list[str]] = []
    max_cols = 0
    for row in rows:
        cleaned_row = [_clean_cell_text(cell) for cell in row]
        if any(cleaned_row):
            non_empty_rows.append(cleaned_row)
            max_cols = max(max_cols, len(cleaned_row))

    if not non_empty_rows or max_cols == 0:
        return ""

    md_lines: list[str] = []
    for idx, row in enumerate(non_empty_rows):
        padded = row + [""] * (max_cols - len(row))
        md_lines.append(f"| {' | '.join(padded)} |")
        if idx == 0 and len(non_empty_rows) > 1:
            md_lines.append(f"| {' | '.join(['---'] * max_cols)} |")

    return "\n".join(md_lines)


def _detect_heading_level(style_name: str | None) -> int | None:
    """Return heading level (1..9) if style_name represents a Word heading or title, else None."""
    if not style_name:
        return None
    normalized = style_name.strip()
    if normalized.lower() in ("title", "subtitle"):
        return 1
    match = _HEADING_RE.match(normalized)
    if match:
        lvl_str = match.group(1)
        return int(lvl_str) if lvl_str and lvl_str.isdigit() else 1
    return None


def _extract_blocks_with_python_docx(path: Path) -> list[tuple[str, str, int | None]]:
    """
    Extract ordered blocks from a DOCX file using `python-docx`.
    Returns list of tuples: (block_type, text_content, heading_level)
    where block_type is 'heading' | 'paragraph' | 'table'.
    """
    document = docx.Document(str(path))
    blocks: list[tuple[str, str, int | None]] = []

    body_elm = document.element.body
    for child in body_elm.iterchildren():
        if CT_P is not None and isinstance(child, CT_P):
            para = Paragraph(child, document)
            text = re.sub(r"[ \t]+", " ", (para.text or "").strip())
            if not text:
                continue
            style_name = para.style.name if para.style is not None else None
            heading_level = _detect_heading_level(style_name)
            if heading_level is not None:
                blocks.append(("heading", text, heading_level))
            else:
                blocks.append(("paragraph", text, None))
        elif CT_Tbl is not None and isinstance(child, CT_Tbl):
            table = Table(child, document)
            table_rows: list[list[str]] = []
            for row in table.rows:
                table_rows.append([cell.text or "" for cell in row.cells])
            md_table = _format_table_as_markdown(table_rows)
            if md_table:
                blocks.append(("table", md_table, None))

    return blocks


def _extract_blocks_from_ooxml_zip(path: Path) -> list[tuple[str, str, int | None]]:
    """
    Pure-Python stdlib OOXML (`word/document.xml`) extractor used when `python-docx` is unavailable.
    Extracts paragraphs, heading styles, and tables in exact document order.
    """
    if not zipfile.is_zipfile(path):
        raise ValueError(f"File is not a valid DOCX/ZIP archive: {path}")

    styles_map: dict[str, str] = {}
    with zipfile.ZipFile(path, "r") as zf:
        if "word/styles.xml" in zf.namelist():
            try:
                styles_root = ET.fromstring(zf.read("word/styles.xml"))
                for style_el in styles_root.findall(f".//{_W_NS}style"):
                    style_id = style_el.attrib.get(f"{_W_NS}styleId", "")
                    name_el = style_el.find(f"{_W_NS}name")
                    name_val = (
                        name_el.attrib.get(f"{_W_NS}val", "")
                        if name_el is not None
                        else style_id
                    )
                    if style_id:
                        styles_map[style_id] = name_val or style_id
            except Exception:
                pass

        if "word/document.xml" not in zf.namelist():
            raise ValueError(f"DOCX archive missing word/document.xml: {path}")
        doc_xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(doc_xml_bytes)
    body = root.find(f"{_W_NS}body")
    if body is None:
        return []

    blocks: list[tuple[str, str, int | None]] = []

    for child in body:
        if child.tag == f"{_W_NS}p":
            texts = [t.text for t in child.findall(f".//{_W_NS}t") if t.text]
            para_text = re.sub(r"[ \t]+", " ", "".join(texts).strip())
            if not para_text:
                continue

            heading_level: int | None = None
            ppr = child.find(f"{_W_NS}pPr")
            if ppr is not None:
                pstyle = ppr.find(f"{_W_NS}pStyle")
                if pstyle is not None:
                    raw_style = pstyle.attrib.get(f"{_W_NS}val", "")
                    resolved_style = styles_map.get(raw_style, raw_style)
                    heading_level = _detect_heading_level(resolved_style) or _detect_heading_level(raw_style)
                if heading_level is None:
                    outline_el = ppr.find(f"{_W_NS}outlineLvl")
                    if outline_el is not None:
                        val = outline_el.attrib.get(f"{_W_NS}val", "")
                        if val.isdigit():
                            heading_level = int(val) + 1

            if heading_level is not None:
                blocks.append(("heading", para_text, heading_level))
            else:
                blocks.append(("paragraph", para_text, None))

        elif child.tag == f"{_W_NS}tbl":
            table_rows: list[list[str]] = []
            for tr in child.findall(f"{_W_NS}tr"):
                row_cells: list[str] = []
                for tc in tr.findall(f"{_W_NS}tc"):
                    cell_paragraphs: list[str] = []
                    for p in tc.findall(f".//{_W_NS}p"):
                        p_texts = [t.text for t in p.findall(f".//{_W_NS}t") if t.text]
                        joined = "".join(p_texts).strip()
                        if joined:
                            cell_paragraphs.append(joined)
                    row_cells.append(" ".join(cell_paragraphs))
                table_rows.append(row_cells)
            md_table = _format_table_as_markdown(table_rows)
            if md_table:
                blocks.append(("table", md_table, None))

    return blocks


def parse_docx(file_path: str | Path) -> list[ParsedSection]:
    """
    Extract paragraphs and tables in document order from a DOCX file.
    Tracks heading hierarchy (`Heading 1`, `Heading 2`, etc.) so each section has
    `page_or_sheet = f"Section {section_idx}: {heading_title}"` (or `"Section 1"` if no heading precedes it).
    Converts DOCX tables into pipe-delimited Markdown rows (`| Col1 | Col2 |`).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"DOCX file not found: {path}")

    blocks: list[tuple[str, str, int | None]] = []
    if _HAS_PYTHON_DOCX and docx is not None:
        try:
            blocks = _extract_blocks_with_python_docx(path)
        except Exception as exc:
            logger.warning("python-docx failed for '%s', falling back to OOXML parser: %s", path, exc)
            blocks = []

    if not blocks:
        blocks = _extract_blocks_from_ooxml_zip(path)

    if not blocks:
        return []

    sections: list[ParsedSection] = []
    section_idx = 1
    current_heading: str | None = None
    current_heading_level: int | None = None
    heading_hierarchy: dict[int, str] = {}
    current_lines: list[str] = []

    def _flush_section() -> None:
        nonlocal section_idx, current_lines
        body_text = "\n\n".join(line for line in current_lines if line.strip()).strip()
        if not body_text:
            current_lines = []
            return

        if current_heading:
            page_or_sheet = f"Section {section_idx}: {current_heading}"
        else:
            page_or_sheet = f"Section {section_idx}"

        active_hierarchy = [
            heading_hierarchy[lvl]
            for lvl in sorted(heading_hierarchy.keys())
            if heading_hierarchy.get(lvl)
        ]

        meta: dict[str, Any] = {
            "section_index": section_idx,
            "source_format": "docx",
        }
        if current_heading:
            meta["heading"] = current_heading
        if current_heading_level is not None:
            meta["heading_level"] = current_heading_level
        if active_hierarchy:
            meta["heading_hierarchy"] = active_hierarchy

        sections.append(
            ParsedSection(
                text=body_text,
                page_or_sheet=page_or_sheet,
                metadata=meta,
            )
        )
        section_idx += 1
        current_lines = []

    for block_type, content, level in blocks:
        if block_type == "heading":
            if current_lines:
                _flush_section()
            lvl = level or 1
            # Prune deeper heading levels from hierarchy
            for existing_lvl in list(heading_hierarchy.keys()):
                if existing_lvl >= lvl:
                    del heading_hierarchy[existing_lvl]
            heading_hierarchy[lvl] = content
            current_heading = content
            current_heading_level = lvl
            current_lines.append(content)
        else:
            current_lines.append(content)

    if current_lines:
        _flush_section()

    return sections
