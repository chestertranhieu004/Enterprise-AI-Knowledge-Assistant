# File: backend/app/ingestion/parsers/pdf_parser.py
from __future__ import annotations

import logging
import re
import zlib
from pathlib import Path

from app.ingestion.chunker import ParsedSection

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    _HAS_PYPDF = True
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment,misc]
    _HAS_PYPDF = False

try:
    import pdfplumber  # type: ignore[import-not-found]

    _HAS_PDFPLUMBER = True
except Exception:  # pragma: no cover
    pdfplumber = None  # type: ignore[assignment]
    _HAS_PDFPLUMBER = False


def _normalize_pdf_text(raw_text: str) -> str:
    """Normalize horizontal whitespace while preserving intentional paragraph breaks."""
    if not raw_text:
        return ""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    # Collapse horizontal spaces/tabs on each line
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    normalized_lines: list[str] = []
    blank_streak = 0
    for line in lines:
        if not line:
            blank_streak += 1
            if blank_streak == 1 and normalized_lines:
                normalized_lines.append("")
        else:
            blank_streak = 0
            normalized_lines.append(line)
    return "\n".join(normalized_lines).strip()


def _decode_pdf_literal_string(raw: bytes) -> str:
    """Decode a PDF literal string content (without outer parentheses) into Unicode text."""
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == 0x5C:  # backslash '\'
            i += 1
            if i >= n:
                break
            esc = raw[i]
            if esc in (0x6E,):  # \n
                out.append(0x0A)
            elif esc in (0x72,):  # \r
                out.append(0x0D)
            elif esc in (0x74,):  # \t
                out.append(0x09)
            elif esc in (0x62,):  # \b
                out.append(0x08)
            elif esc in (0x66,):  # \f
                out.append(0x0C)
            elif esc in (0x28, 0x29, 0x5C):  # \( \) \\
                out.append(esc)
            elif 0x30 <= esc <= 0x37:  # octal \ddd
                oct_digits = bytes([esc])
                for _ in range(2):
                    if i + 1 < n and 0x30 <= raw[i + 1] <= 0x37:
                        i += 1
                        oct_digits += bytes([raw[i]])
                    else:
                        break
                out.append(int(oct_digits, 8) & 0xFF)
            elif esc in (0x0A, 0x0D):
                # Escaped line continuation
                if esc == 0x0D and i + 1 < n and raw[i + 1] == 0x0A:
                    i += 1
            else:
                out.append(esc)
        else:
            out.append(ch)
        i += 1

    data = bytes(out)
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _decode_pdf_hex_string(hex_bytes: bytes) -> str:
    """Decode a PDF hex string `<...>` into Unicode text."""
    cleaned = re.sub(rb"\s+", b"", hex_bytes)
    if len(cleaned) % 2 == 1:
        cleaned += b"0"
    try:
        data = bytes.fromhex(cleaned.decode("ascii"))
    except Exception:
        return ""
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    if len(data) >= 2 and len(data) % 2 == 0 and b"\x00" in data:
        try:
            return data.decode("utf-16-be")
        except Exception:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _extract_text_from_content_stream(stream_bytes: bytes) -> str:
    """Extract readable text from a decompressed PDF page content stream (`BT ... ET`)."""
    paragraphs: list[str] = []
    current_line_parts: list[str] = []

    token_re = re.compile(
        rb"(\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>|\[(?:\\.|[^\[\]])*\]|T\*|Td|TD|Tj|TJ|'|\"|ET)"
    )
    sub_token_re = re.compile(rb"(\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>)")

    for match in token_re.finditer(stream_bytes):
        tok = match.group(0)
        if tok.startswith(b"(") and tok.endswith(b")"):
            current_line_parts.append(_decode_pdf_literal_string(tok[1:-1]))
        elif tok.startswith(b"<") and tok.endswith(b">"):
            current_line_parts.append(_decode_pdf_hex_string(tok[1:-1]))
        elif tok.startswith(b"[") and tok.endswith(b"]"):
            pieces: list[str] = []
            for sub in sub_token_re.finditer(tok[1:-1]):
                s_tok = sub.group(0)
                if s_tok.startswith(b"("):
                    pieces.append(_decode_pdf_literal_string(s_tok[1:-1]))
                elif s_tok.startswith(b"<"):
                    pieces.append(_decode_pdf_hex_string(s_tok[1:-1]))
            if pieces:
                current_line_parts.append("".join(pieces))
        elif tok in (b"T*", b"Td", b"TD", b"'", b'"', b"ET"):
            if current_line_parts:
                line = "".join(current_line_parts).strip()
                if line:
                    paragraphs.append(line)
                current_line_parts = []

    if current_line_parts:
        line = "".join(current_line_parts).strip()
        if line:
            paragraphs.append(line)

    return "\n".join(paragraphs)


def _fallback_parse_pdf_bytes(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """
    Pure-Python stdlib PDF page extractor used when neither pypdf nor pdfplumber is installed.
    Supports uncompressed and FlateDecode-compressed object streams and multi-page trees.
    """
    obj_pattern = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)endobj", re.DOTALL)
    stream_pattern = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)

    objects: dict[int, bytes] = {}
    streams: dict[int, bytes] = {}

    for match in obj_pattern.finditer(pdf_bytes):
        obj_num = int(match.group(1))
        obj_body = match.group(3)
        objects[obj_num] = obj_body
        s_match = stream_pattern.search(obj_body)
        if s_match:
            raw_stream = s_match.group(1)
            if b"/FlateDecode" in obj_body:
                try:
                    raw_stream = zlib.decompress(raw_stream)
                except Exception:
                    try:
                        raw_stream = zlib.decompress(raw_stream.strip(b"\r\n"))
                    except Exception:
                        pass
            streams[obj_num] = raw_stream

    # Find Page objects (/Type /Page, excluding /Type /Pages)
    page_obj_nums: list[int] = []
    for obj_num, body in objects.items():
        # Strip any embedded stream before inspecting the object dictionary
        dict_part = body.split(b"stream", 1)[0]
        if re.search(rb"/Type\s*/Page\b(?!s)", dict_part):
            page_obj_nums.append(obj_num)

    results: list[tuple[int, str]] = []
    if page_obj_nums:
        for idx, page_obj_num in enumerate(page_obj_nums, start=1):
            dict_part = objects[page_obj_num].split(b"stream", 1)[0]
            content_refs: list[int] = []
            arr_match = re.search(rb"/Contents\s*\[(.*?)\]", dict_part, re.DOTALL)
            if arr_match:
                content_refs = [
                    int(m.group(1))
                    for m in re.finditer(rb"(\d+)\s+\d+\s+R", arr_match.group(1))
                ]
            else:
                single_match = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", dict_part)
                if single_match:
                    content_refs = [int(single_match.group(1))]
                elif page_obj_num in streams:
                    content_refs = [page_obj_num]

            page_texts: list[str] = []
            for ref in content_refs:
                if ref in streams:
                    extracted = _extract_text_from_content_stream(streams[ref])
                    if extracted:
                        page_texts.append(extracted)
            combined = _normalize_pdf_text("\n".join(page_texts))
            if combined:
                results.append((idx, combined))

    if not results and streams:
        page_idx = 1
        for obj_num in sorted(streams.keys()):
            extracted = _normalize_pdf_text(_extract_text_from_content_stream(streams[obj_num]))
            if extracted:
                results.append((page_idx, extracted))
                page_idx += 1

    return results


def parse_pdf(file_path: str | Path) -> list[ParsedSection]:
    """
    Extract text page by page from a PDF file using pypdf (with pdfplumber and stdlib fallbacks).
    Preserves 1-indexed page coordinates (`page_or_sheet = f"Page {page_number}"`).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {path}")

    page_entries: dict[int, str] = {}
    total_pages = 0

    # 1. Primary extractor: pypdf
    if _HAS_PYPDF and PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            total_pages = len(reader.pages)
            for page_number, page in enumerate(reader.pages, start=1):
                raw_text = page.extract_text() or ""
                cleaned = _normalize_pdf_text(raw_text)
                if cleaned:
                    page_entries[page_number] = cleaned
        except Exception as exc:
            logger.warning("pypdf extraction failed for '%s': %s", path, exc)

    # 2. Secondary fallback: pdfplumber (fills any pages missing or failed in pypdf)
    if _HAS_PDFPLUMBER and pdfplumber is not None and (not page_entries or len(page_entries) < total_pages):
        try:
            with pdfplumber.open(str(path)) as pdf:
                total_pages = max(total_pages, len(pdf.pages))
                for page_number, page in enumerate(pdf.pages, start=1):
                    if page_number not in page_entries:
                        raw_text = page.extract_text() or ""
                        cleaned = _normalize_pdf_text(raw_text)
                        if cleaned:
                            page_entries[page_number] = cleaned
        except Exception as exc:
            logger.warning("pdfplumber fallback failed for '%s': %s", path, exc)

    # 3. Resilient stdlib fallback when neither pypdf nor pdfplumber extracted pages
    if not page_entries:
        pdf_bytes = path.read_bytes()
        for page_number, extracted_text in _fallback_parse_pdf_bytes(pdf_bytes):
            if extracted_text:
                page_entries[page_number] = extracted_text

    sections: list[ParsedSection] = []
    for page_number in sorted(page_entries.keys()):
        text_content = page_entries[page_number]
        if not text_content:
            continue
        sections.append(
            ParsedSection(
                text=text_content,
                page_or_sheet=f"Page {page_number}",
                metadata={
                    "page_number": page_number,
                    "source_format": "pdf",
                },
            )
        )

    return sections
