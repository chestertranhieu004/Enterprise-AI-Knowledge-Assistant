# File: backend/app/ingestion/parsers/__init__.py
from app.ingestion.chunker import parse_text_or_markdown
from app.ingestion.parsers.docx_parser import parse_docx
from app.ingestion.parsers.pdf_parser import parse_pdf
from app.ingestion.parsers.xlsx_parser import parse_xlsx

__all__ = [
    "parse_pdf",
    "parse_docx",
    "parse_xlsx",
    "parse_text_or_markdown",
]
