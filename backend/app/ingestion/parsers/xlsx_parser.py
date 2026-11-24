# File: backend/app/ingestion/parsers/xlsx_parser.py
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
    from openpyxl import load_workbook  # type: ignore[import-not-found]

    _HAS_OPENPYXL = True
except Exception:  # pragma: no cover
    load_workbook = None  # type: ignore[assignment,misc]
    _HAS_OPENPYXL = False

_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REF_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def _col_letters_to_index(col_letters: str) -> int:
    """Convert Excel column letters ('A' -> 0, 'B' -> 1, 'AA' -> 26) to 0-based index."""
    idx = 0
    for ch in col_letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def _format_cell_value(val: Any) -> str:
    """Convert a cell value into a clean single-line string."""
    if val is None:
        return ""
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    text = str(val).strip()
    return re.sub(r"\s+", " ", text)


def _extract_sheets_with_openpyxl(path: Path) -> list[tuple[str, list[list[str]]]]:
    """Extract (sheet_title, rows_matrix) from an XLSX file using `openpyxl`."""
    wb = load_workbook(filename=str(path), data_only=True, read_only=True)
    sheets_data: list[tuple[str, list[list[str]]]] = []
    try:
        for sheet in wb.worksheets:
            rows_matrix: list[list[str]] = []
            for row in sheet.iter_rows(values_only=True):
                formatted_row = [_format_cell_value(cell) for cell in row]
                # Trim trailing empty cells
                while formatted_row and not formatted_row[-1]:
                    formatted_row.pop()
                if any(formatted_row):
                    rows_matrix.append(formatted_row)
            sheets_data.append((sheet.title, rows_matrix))
    finally:
        wb.close()
    return sheets_data


def _extract_sheets_from_ooxml_zip(path: Path) -> list[tuple[str, list[list[str]]]]:
    """
    Pure-Python stdlib OOXML (`.xlsx` ZIP + XML) extractor used when `openpyxl` is unavailable.
    Reads `xl/workbook.xml`, `xl/_rels/workbook.xml.rels`, `xl/sharedStrings.xml`, and worksheet XMLs.
    """
    if not zipfile.is_zipfile(path):
        raise ValueError(f"File is not a valid XLSX/ZIP archive: {path}")

    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError(f"XLSX archive missing xl/workbook.xml: {path}")

        # 1. Load shared strings table if present
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.findall(f"{_MAIN_NS}si"):
                t_parts = [t.text or "" for t in si.findall(f".//{_MAIN_NS}t")]
                shared_strings.append("".join(t_parts))

        # 2. Load workbook relationships (rId -> worksheet target path)
        rel_targets: dict[str, str] = {}
        rels_path = "xl/_rels/workbook.xml.rels"
        if rels_path in names:
            rels_root = ET.fromstring(zf.read(rels_path))
            for rel in rels_root.findall(f"{_PKG_REL_NS}Relationship"):
                r_id = rel.attrib.get("Id", "")
                target = rel.attrib.get("Target", "").lstrip("/")
                if target and not target.startswith("xl/"):
                    target = f"xl/{target}"
                if r_id and target:
                    rel_targets[r_id] = target

        # 3. Parse sheet names and targets from xl/workbook.xml
        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        sheet_specs: list[tuple[str, str]] = []
        for idx, sheet_el in enumerate(wb_root.findall(f".//{_MAIN_NS}sheet"), start=1):
            title = sheet_el.attrib.get("name", f"Sheet{idx}")
            r_id = sheet_el.attrib.get(f"{_REL_NS}id", "")
            target = rel_targets.get(r_id, f"xl/worksheets/sheet{idx}.xml")
            if target not in names:
                alt_target = f"xl/worksheets/sheet{idx}.xml"
                if alt_target in names:
                    target = alt_target
            if target in names:
                sheet_specs.append((title, target))

        # 4. Extract rows from each worksheet XML
        sheets_data: list[tuple[str, list[list[str]]]] = []
        for sheet_title, target in sheet_specs:
            ws_root = ET.fromstring(zf.read(target))
            rows_matrix: list[list[str]] = []

            for row_el in ws_root.findall(f".//{_MAIN_NS}sheetData/{_MAIN_NS}row"):
                cells_by_col: dict[int, str] = {}
                next_col_idx = 0

                for cell_el in row_el.findall(f"{_MAIN_NS}c"):
                    cell_ref = cell_el.attrib.get("r", "")
                    m = _CELL_REF_RE.match(cell_ref)
                    if m:
                        col_idx = _col_letters_to_index(m.group(1))
                    else:
                        col_idx = next_col_idx
                    next_col_idx = col_idx + 1

                    cell_type = cell_el.attrib.get("t", "")
                    val_str = ""
                    if cell_type == "inlineStr":
                        t_parts = [t.text or "" for t in cell_el.findall(f".//{_MAIN_NS}t")]
                        val_str = "".join(t_parts)
                    else:
                        v_el = cell_el.find(f"{_MAIN_NS}v")
                        raw_v = (v_el.text or "") if v_el is not None else ""
                        if cell_type == "s":
                            if raw_v.isdigit() and int(raw_v) < len(shared_strings):
                                val_str = shared_strings[int(raw_v)]
                            else:
                                val_str = raw_v
                        elif cell_type == "b":
                            val_str = "TRUE" if raw_v in ("1", "true", "TRUE") else "FALSE"
                        else:
                            val_str = raw_v

                    cleaned_val = _format_cell_value(val_str)
                    if cleaned_val:
                        cells_by_col[col_idx] = cleaned_val

                if cells_by_col:
                    max_col = max(cells_by_col.keys())
                    formatted_row = [cells_by_col.get(i, "") for i in range(max_col + 1)]
                    while formatted_row and not formatted_row[-1]:
                        formatted_row.pop()
                    if any(formatted_row):
                        rows_matrix.append(formatted_row)

            sheets_data.append((sheet_title, rows_matrix))

    return sheets_data


def parse_xlsx(
    file_path: str | Path,
    rows_per_section: int = 25,
) -> list[ParsedSection]:
    """
    Extract worksheets from an XLSX file using `openpyxl` (`load_workbook(file_path, data_only=True)`).
    Detects the header row (first non-empty row), groups data rows in batches of `rows_per_section` (default 25),
    prepends column headers to every batch, formats each row as `Header1: Val1 | Header2: Val2`,
    and sets `page_or_sheet = f"Sheet: {sheet.title} (Rows {start_row}-{end_row})"`.
    """
    if rows_per_section <= 0:
        raise ValueError("rows_per_section must be a positive integer")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"XLSX file not found: {path}")

    sheets_data: list[tuple[str, list[list[str]]]] = []
    if _HAS_OPENPYXL and load_workbook is not None:
        try:
            sheets_data = _extract_sheets_with_openpyxl(path)
        except Exception as exc:
            logger.warning("openpyxl failed for '%s', falling back to OOXML parser: %s", path, exc)
            sheets_data = []

    if not sheets_data:
        sheets_data = _extract_sheets_from_ooxml_zip(path)

    sections: list[ParsedSection] = []

    for sheet_title, non_empty_rows in sheets_data:
        if not non_empty_rows:
            continue

        raw_headers = non_empty_rows[0]
        max_cols = max(len(r) for r in non_empty_rows)
        headers: list[str] = []
        for col_idx in range(max_cols):
            raw_h = raw_headers[col_idx].strip() if col_idx < len(raw_headers) else ""
            headers.append(raw_h if raw_h else f"Column_{col_idx + 1}")

        data_rows = non_empty_rows[1:]
        header_banner = f"Columns: {' | '.join(headers)}"

        # Handle worksheet that only contains a single header/summary row
        if not data_rows:
            single_line = " | ".join(
                f"{headers[i]}: {val}"
                for i, val in enumerate(raw_headers)
                if val
            )
            section_text = f"Sheet: {sheet_title}\n{header_banner}\n{single_line}".strip()
            sections.append(
                ParsedSection(
                    text=section_text,
                    page_or_sheet=f"Sheet: {sheet_title} (Rows 1-1)",
                    metadata={
                        "sheet_name": sheet_title,
                        "start_row": 1,
                        "end_row": 1,
                        "headers": headers,
                        "source_format": "xlsx",
                    },
                )
            )
            continue

        for batch_start in range(0, len(data_rows), rows_per_section):
            batch = data_rows[batch_start : batch_start + rows_per_section]
            start_row = batch_start + 1
            end_row = batch_start + len(batch)

            formatted_lines: list[str] = [
                f"Sheet: {sheet_title}",
                header_banner,
            ]
            for row in batch:
                kv_pairs: list[str] = []
                for col_idx, header_name in enumerate(headers):
                    cell_val = row[col_idx] if col_idx < len(row) else ""
                    if cell_val != "":
                        kv_pairs.append(f"{header_name}: {cell_val}")
                if kv_pairs:
                    formatted_lines.append(" | ".join(kv_pairs))

            section_text = "\n".join(formatted_lines).strip()
            page_or_sheet = f"Sheet: {sheet_title} (Rows {start_row}-{end_row})"

            sections.append(
                ParsedSection(
                    text=section_text,
                    page_or_sheet=page_or_sheet,
                    metadata={
                        "sheet_name": sheet_title,
                        "start_row": start_row,
                        "end_row": end_row,
                        "row_count": len(batch),
                        "headers": headers,
                        "source_format": "xlsx",
                    },
                )
            )

    return sections
