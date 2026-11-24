# File: backend/app/ingestion/chunker.py
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.core.schema import Document as LlamaDocument
    from llama_index.core.schema import MetadataMode, TextNode

    _HAS_LLAMA_INDEX = True
except Exception:  # pragma: no cover
    SentenceSplitter = None  # type: ignore[assignment,misc]
    LlamaDocument = None  # type: ignore[assignment,misc]
    MetadataMode = None  # type: ignore[assignment,misc]
    TextNode = None  # type: ignore[assignment,misc]
    _HAS_LLAMA_INDEX = False


_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?।。！？\n])\s+")


def _offline_tokenize(text: str) -> list[str]:
    """Deterministic, zero-network Unicode word/punctuation tokenizer."""
    if not text:
        return []
    return _TOKEN_PATTERN.findall(text)


def count_tokens(text: str) -> int:
    """Count tokens using the deterministic Unicode tokenizer."""
    if not text or not text.strip():
        return 0
    tokens = _offline_tokenize(text)
    return max(1, len(tokens))


class ParsedSection(BaseModel):
    """Output of format-specific parsers (PDF page, DOCX section, XLSX sheet row-block)."""

    text: str
    page_or_sheet: str  # e.g. "Page 1", "Section 2: Nghỉ phép", "Sheet: Budget_2026 (Rows 1-20)"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkData(BaseModel):
    """Output of LlamaIndex SentenceSplitter node chunking ready for embedding."""

    chunk_index: int
    page_or_sheet: str
    content: str
    token_count: int
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class LlamaIndexChunker:
    """Uses llama_index.core.schema.Document and SentenceSplitter(chunk_size, chunk_overlap)."""

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be non-negative")
        if chunk_overlap >= chunk_size:
            chunk_overlap = max(0, chunk_size // 4)

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._splitter: Any = None

        if _HAS_LLAMA_INDEX and SentenceSplitter is not None:
            try:
                self._splitter = SentenceSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    tokenizer=_offline_tokenize,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Falling back to internal sentence splitter because SentenceSplitter init failed: %s",
                    exc,
                )
                self._splitter = None

    def _fallback_split_text(self, text: str) -> list[str]:
        """Resilient offline sentence-aware sliding window splitter if LlamaIndex is unavailable."""
        cleaned = text.strip()
        if not cleaned:
            return []

        tokens = _offline_tokenize(cleaned)
        if len(tokens) <= self.chunk_size:
            return [cleaned]

        sentences = [s.strip() for s in _SENTENCE_SPLIT_PATTERN.split(cleaned) if s.strip()]
        if not sentences:
            sentences = [cleaned]

        chunks: list[str] = []
        current_sentences: list[str] = []
        current_token_count = 0

        for sentence in sentences:
            sent_tokens = _offline_tokenize(sentence)
            sent_len = len(sent_tokens)

            if sent_len > self.chunk_size:
                if current_sentences:
                    chunks.append(" ".join(current_sentences).strip())
                    current_sentences = []
                    current_token_count = 0
                words = sentence.split()
                step = max(1, self.chunk_size - self.chunk_overlap)
                for start_idx in range(0, len(words), step):
                    window = " ".join(words[start_idx : start_idx + self.chunk_size]).strip()
                    if window:
                        chunks.append(window)
                    if start_idx + self.chunk_size >= len(words):
                        break
                continue

            if current_token_count + sent_len > self.chunk_size and current_sentences:
                chunks.append(" ".join(current_sentences).strip())
                # Retain overlap sentences from the tail of current_sentences
                overlap_sentences: list[str] = []
                overlap_tokens = 0
                for prev_sent in reversed(current_sentences):
                    prev_len = len(_offline_tokenize(prev_sent))
                    if overlap_tokens + prev_len <= self.chunk_overlap:
                        overlap_sentences.insert(0, prev_sent)
                        overlap_tokens += prev_len
                    else:
                        break
                current_sentences = overlap_sentences
                current_token_count = overlap_tokens

            current_sentences.append(sentence)
            current_token_count += sent_len

        if current_sentences:
            final_chunk = " ".join(current_sentences).strip()
            if final_chunk:
                chunks.append(final_chunk)

        return chunks or [cleaned]

    def split_sections(
        self,
        sections: list[ParsedSection],
        file_name: str,
    ) -> list[ChunkData]:
        """Splits ParsedSections into ChunkData objects preserving page_or_sheet coordinates."""
        chunks: list[ChunkData] = []
        chunk_index = 0

        for section in sections:
            cleaned_text = (section.text or "").strip()
            if not cleaned_text:
                continue

            base_metadata: dict[str, Any] = {
                **section.metadata,
                "page_or_sheet": section.page_or_sheet,
                "file_name": file_name,
            }

            node_entries: list[tuple[str, dict[str, Any], str]] = []

            if self._splitter is not None and LlamaDocument is not None:
                try:
                    excluded_keys = list(base_metadata.keys())
                    llama_doc = LlamaDocument(
                        text=cleaned_text,
                        metadata=dict(base_metadata),
                        excluded_embed_metadata_keys=excluded_keys,
                        excluded_llm_metadata_keys=excluded_keys,
                    )
                    nodes = self._splitter.get_nodes_from_documents([llama_doc])
                    for node in nodes:
                        if MetadataMode is not None and hasattr(node, "get_content"):
                            raw_content = node.get_content(metadata_mode=MetadataMode.NONE)
                        else:
                            raw_content = getattr(node, "text", "")
                        content_str = (raw_content or "").strip()
                        if not content_str:
                            continue
                        node_meta = dict(base_metadata)
                        if hasattr(node, "metadata") and isinstance(node.metadata, dict):
                            node_meta.update(node.metadata)
                        node_id = str(getattr(node, "node_id", None) or uuid.uuid4())
                        node_entries.append((content_str, node_meta, node_id))
                except Exception as exc:
                    logger.warning(
                        "LlamaIndex SentenceSplitter failed on section '%s'; using fallback splitter: %s",
                        section.page_or_sheet,
                        exc,
                    )
                    node_entries = []

            if not node_entries:
                for piece in self._fallback_split_text(cleaned_text):
                    if not piece:
                        continue
                    node_entries.append((piece, dict(base_metadata), str(uuid.uuid4())))

            for content_str, node_meta, node_id in node_entries:
                page_or_sheet = str(node_meta.get("page_or_sheet") or section.page_or_sheet)
                token_cnt = count_tokens(content_str)
                final_meta: dict[str, Any] = {
                    **node_meta,
                    "file_name": file_name,
                    "page_or_sheet": page_or_sheet,
                    "chunk_index": chunk_index,
                    "node_id": node_id,
                }
                chunks.append(
                    ChunkData(
                        chunk_index=chunk_index,
                        page_or_sheet=page_or_sheet,
                        content=content_str,
                        token_count=token_cnt,
                        metadata_json=final_meta,
                    )
                )
                chunk_index += 1

        return chunks


def parse_text_or_markdown(file_path: str | Path) -> list[ParsedSection]:
    """
    Parse plain text (.txt) or Markdown (.md) files into heading-aware ParsedSection objects.
    Preserves Markdown headings as `Section N: <Heading>` when present, or `Section 1` for plain text.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    raw_text = path.read_text(encoding="utf-8", errors="replace")
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    ext = path.suffix.lower().lstrip(".") or "txt"
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_pattern.finditer(normalized))

    if not matches:
        return [
            ParsedSection(
                text=normalized,
                page_or_sheet="Section 1",
                metadata={"section_index": 1, "source_format": ext},
            )
        ]

    sections: list[ParsedSection] = []
    section_idx = 1

    # Capture any preamble before the first Markdown heading
    first_start = matches[0].start()
    if first_start > 0:
        preamble = normalized[:first_start].strip()
        if preamble:
            sections.append(
                ParsedSection(
                    text=preamble,
                    page_or_sheet=f"Section {section_idx}",
                    metadata={"section_index": section_idx, "source_format": ext},
                )
            )
            section_idx += 1

    for idx, match in enumerate(matches):
        heading_title = match.group(2).strip()
        start_pos = match.start()
        end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
        section_body = normalized[start_pos:end_pos].strip()
        if not section_body:
            continue
        page_label = (
            f"Section {section_idx}: {heading_title}"
            if heading_title
            else f"Section {section_idx}"
        )
        sections.append(
            ParsedSection(
                text=section_body,
                page_or_sheet=page_label,
                metadata={
                    "section_index": section_idx,
                    "heading": heading_title,
                    "source_format": ext,
                },
            )
        )
        section_idx += 1

    return sections
