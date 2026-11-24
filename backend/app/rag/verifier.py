# File: backend/app/rag/verifier.py
import re
import unicodedata

from pydantic import BaseModel, Field

from app.core.config import settings
from app.rag.providers.base import (
    RawCitationCandidate,
    RawLLMGeneration,
    RetrievedChunk,
)
from app.rag.providers.mock_provider import STOPWORDS_VI_EN
from app.schemas.chat import CitationItem
from app.schemas.common import ABSTENTION_MESSAGE_VI, VerificationStatus

BRACKET_CITATION_PATTERN = re.compile(
    r"\[([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[a-zA-Z0-9_-]*chunk[a-zA-Z0-9_-]*|[0-9a-fA-F-]{16,64})\]"
)


def _normalize_text(text: str) -> str:
    """Unicode NFC, lowercase, collapse whitespace and strip edge punctuation."""
    norm = unicodedata.normalize("NFC", (text or "").lower())
    norm = re.sub(r"[^\w\s\u00C0-\u1EF9.,/%-]", " ", norm)
    return re.sub(r"\s+", " ", norm).strip()


def _extract_content_tokens(text: str) -> list[str]:
    """Extract informative lowercase tokens excluding conversational stopwords."""
    norm = unicodedata.normalize("NFC", (text or "").lower())
    raw_tokens = re.findall(r"[0-9a-zA-Z\u00C0-\u1EF9]+(?:[.,/_-][0-9a-zA-Z\u00C0-\u1EF9]+)*", norm)
    filtered = [t for t in raw_tokens if t not in STOPWORDS_VI_EN and len(t) > 1]
    return filtered if filtered else raw_tokens


def _extract_numbers(text: str) -> set[str]:
    """Extract all numeric literals from text to catch fabricated quantities/dates."""
    return set(re.findall(r"\b\d+(?:[.,]\d+)*\b", text or ""))


class VerificationResult(BaseModel):
    found: bool
    answer: str
    citations: list[CitationItem] = Field(default_factory=list)
    rejected_chunk_ids: list[str] = Field(default_factory=list)


class CitationVerifier:
    """
    Scenario 2 Post-Generation Citation Verifier (ADR-004).
    Validates every cited chunk_id against the retrieved authorized_chunks whitelist,
    verifies lexical/token containment and numeric consistency of quotes against chunk.content,
    binds authoritative metadata from the DB chunk, and falls back to Scenario 1 if ungrounded.
    """

    def __init__(self, overlap_threshold: float | None = None) -> None:
        self.overlap_threshold = (
            overlap_threshold
            if overlap_threshold is not None
            else settings.QUOTE_OVERLAP_THRESHOLD
        )

    def compute_quote_overlap(self, quote: str, chunk_content: str) -> float:
        """
        Compute lexical/token containment ratio in [0.0, 1.0] of `quote` inside `chunk_content`.
        Returns 0.0 if `quote` is empty or contains fabricated numeric literals absent from `chunk_content`.
        """
        norm_quote = _normalize_text(quote)
        norm_content = _normalize_text(chunk_content)
        if not norm_quote or not norm_content:
            return 0.0

        # Numeric hallucination check: any number in the quote must exist in the authoritative chunk
        quote_numbers = _extract_numbers(norm_quote)
        content_numbers = _extract_numbers(norm_content)
        if quote_numbers and not quote_numbers.issubset(content_numbers):
            return 0.0

        if norm_quote in norm_content:
            return 1.0

        quote_tokens = _extract_content_tokens(quote)
        content_token_set = set(_extract_content_tokens(chunk_content))
        if not quote_tokens:
            return 0.0

        matched = sum(1 for tok in quote_tokens if tok in content_token_set)
        return float(matched) / float(len(quote_tokens))

    def _extract_best_verbatim_span(self, reference_text: str, chunk_content: str) -> str:
        """
        Find the verbatim sentence/span inside `chunk_content` that best matches `reference_text`.
        """
        cleaned_content = (chunk_content or "").strip()
        if not cleaned_content:
            return ""

        ref_trimmed = (reference_text or "").strip()
        if ref_trimmed and ref_trimmed in cleaned_content:
            return ref_trimmed

        ref_tokens = set(_extract_content_tokens(reference_text))
        spans = [
            m.group(0).strip()
            for m in re.finditer(r"[^.!?\n]+[.!?]?", cleaned_content)
            if len(m.group(0).strip()) >= 10
        ]
        if not spans:
            return cleaned_content

        best_span = spans[0]
        best_hits = -1
        for span in spans:
            span_tokens = set(_extract_content_tokens(span))
            hits = len(ref_tokens & span_tokens)
            if hits > best_hits:
                best_hits = hits
                best_span = span

        return best_span if len(cleaned_content) > 320 else cleaned_content

    @staticmethod
    def _extract_claim_for_chunk_id(answer: str, chunk_id_str: str) -> str:
        """Extract the sentence(s) in `answer` referencing `[chunk_id_str]`."""
        tag = f"[{chunk_id_str}]"
        sentences = re.split(r"(?<=[.!?])\s+|\n+", answer or "")
        matching: list[str] = []
        for sent in sentences:
            if tag.lower() in sent.lower():
                cleaned_sent = re.sub(BRACKET_CITATION_PATTERN, "", sent).strip()
                if cleaned_sent:
                    matching.append(cleaned_sent)
        if matching:
            return " ".join(matching)
        return re.sub(BRACKET_CITATION_PATTERN, "", answer or "").strip()

    @staticmethod
    def _strip_rejected_claims_from_answer(
        answer: str,
        verified_ids_lower: set[str],
        rejected_ids_lower: set[str],
    ) -> str:
        """
        Remove inline [rejected_id] tags and strip sentences that only cite rejected chunk IDs.
        """
        if not rejected_ids_lower:
            return (answer or "").strip()

        paragraphs = (answer or "").split("\n")
        kept_paragraphs: list[str] = []

        for para in paragraphs:
            if not para.strip():
                continue
            sentences = re.split(r"(?<=[.!?])\s+", para.strip())
            kept_sentences: list[str] = []
            for sent in sentences:
                cited_in_sent = [
                    m.group(1).strip().lower()
                    for m in BRACKET_CITATION_PATTERN.finditer(sent)
                ]
                if not cited_in_sent:
                    # Keep introductory/connecting sentence only if it doesn't contain rejected IDs
                    kept_sentences.append(sent)
                    continue

                has_verified = any(cid in verified_ids_lower for cid in cited_in_sent)
                has_rejected = any(cid in rejected_ids_lower for cid in cited_in_sent)

                if has_rejected and not has_verified:
                    # Sentence relies solely on fabricated/unverified citation -> strip entire claim
                    continue

                if has_rejected and has_verified:
                    # Strip only the rejected bracket tags from this sentence
                    def _replace_tag(match: re.Match[str]) -> str:
                        cid_low = match.group(1).strip().lower()
                        return "" if cid_low in rejected_ids_lower else match.group(0)

                    cleaned_sent = BRACKET_CITATION_PATTERN.sub(_replace_tag, sent)
                    cleaned_sent = re.sub(r"\s+([.,;!?])", r"\1", cleaned_sent)
                    cleaned_sent = re.sub(r"\s{2,}", " ", cleaned_sent).strip()
                    if cleaned_sent:
                        kept_sentences.append(cleaned_sent)
                else:
                    kept_sentences.append(sent)

            if kept_sentences:
                kept_paragraphs.append(" ".join(kept_sentences))

        return "\n\n".join(kept_paragraphs).strip()

    def verify(
        self,
        generation: RawLLMGeneration,
        authorized_chunks: list[RetrievedChunk],
    ) -> VerificationResult:
        """
        1. Builds whitelist map: {str(chunk.chunk_id).lower(): chunk for chunk in authorized_chunks}.
        2. Collects candidate citations from generation.citations AND regex matches inside generation.answer.
        3. Validates each candidate against whitelist and overlap_threshold against chunk.content.
        4. Populates CitationItem strictly from the authoritative DB chunk.
        5. Falls back to Scenario 1 (found=False, ABSTENTION_MESSAGE_VI) if abstained or 0 verified citations.
        """
        whitelist_map: dict[str, RetrievedChunk] = {
            str(chunk.chunk_id).strip().lower(): chunk for chunk in authorized_chunks
        }

        rejected_chunk_ids: list[str] = []
        rejected_seen_lower: set[str] = set()

        def _record_rejection(raw_id: str) -> None:
            low = raw_id.strip().lower()
            if low and low not in rejected_seen_lower:
                rejected_seen_lower.add(low)
                rejected_chunk_ids.append(raw_id.strip())

        # Collect candidates preserving order: structured citations first, then inline [chunk_id]
        candidates: list[RawCitationCandidate] = list(generation.citations or [])
        structured_ids_lower = {
            str(c.chunk_id).strip().lower() for c in candidates if str(c.chunk_id).strip()
        }

        for match in BRACKET_CITATION_PATTERN.finditer(generation.answer or ""):
            inline_id = match.group(1).strip()
            if inline_id.lower() not in structured_ids_lower:
                candidates.append(RawCitationCandidate(chunk_id=inline_id, quote=""))
                structured_ids_lower.add(inline_id.lower())

        if generation.abstained or not authorized_chunks or not (generation.answer or "").strip():
            for cand in candidates:
                cid_str = str(cand.chunk_id).strip()
                if cid_str.lower() not in whitelist_map:
                    _record_rejection(cid_str)
            return VerificationResult(
                found=False,
                answer=ABSTENTION_MESSAGE_VI,
                citations=[],
                rejected_chunk_ids=rejected_chunk_ids,
            )

        verified_citations: list[CitationItem] = []
        verified_ids_lower: set[str] = set()

        for cand in candidates:
            raw_id_str = str(cand.chunk_id).strip()
            norm_id = raw_id_str.lower()
            if not norm_id:
                continue

            # Check 1: Whitelist lookup
            if norm_id not in whitelist_map:
                _record_rejection(raw_id_str)
                continue

            auth_chunk = whitelist_map[norm_id]
            candidate_quote = (cand.quote or "").strip()

            # Check 2: Lexical/token containment & numeric consistency verification
            if candidate_quote:
                overlap_score = self.compute_quote_overlap(
                    quote=candidate_quote,
                    chunk_content=auth_chunk.content,
                )
                if overlap_score < self.overlap_threshold:
                    _record_rejection(raw_id_str)
                    continue
                verified_quote = self._extract_best_verbatim_span(
                    reference_text=candidate_quote,
                    chunk_content=auth_chunk.content,
                )
            else:
                # Candidate came from inline [chunk_id] without an explicit quote field
                claim_text = self._extract_claim_for_chunk_id(
                    answer=generation.answer,
                    chunk_id_str=raw_id_str,
                )
                overlap_score = self.compute_quote_overlap(
                    quote=claim_text,
                    chunk_content=auth_chunk.content,
                )
                if overlap_score < self.overlap_threshold:
                    _record_rejection(raw_id_str)
                    continue
                verified_quote = self._extract_best_verbatim_span(
                    reference_text=claim_text,
                    chunk_content=auth_chunk.content,
                )

            # Check 3: Also verify that the inline claim in generation.answer doesn't contradict numbers in chunk
            inline_claim = self._extract_claim_for_chunk_id(
                answer=generation.answer,
                chunk_id_str=raw_id_str,
            )
            if inline_claim:
                claim_overlap = self.compute_quote_overlap(
                    quote=inline_claim,
                    chunk_content=auth_chunk.content,
                )
                # If the sentence citing ONLY this chunk has 0 overlap or fabricated numbers, reject it
                cited_in_claim = [
                    m.group(1).strip().lower()
                    for m in BRACKET_CITATION_PATTERN.finditer(generation.answer or "")
                ]
                if len(set(cited_in_claim)) <= 1 and claim_overlap < self.overlap_threshold:
                    _record_rejection(raw_id_str)
                    continue

            if norm_id not in verified_ids_lower:
                verified_ids_lower.add(norm_id)
                verified_citations.append(
                    CitationItem(
                        document_id=auth_chunk.document_id,
                        file_name=auth_chunk.file_name,
                        page_or_sheet=auth_chunk.page_or_sheet,
                        chunk_id=auth_chunk.chunk_id,
                        quote=verified_quote,
                        score=round(float(auth_chunk.score), 6),
                        verification_status=VerificationStatus.VERIFIED,
                    )
                )

        if not verified_citations:
            return VerificationResult(
                found=False,
                answer=ABSTENTION_MESSAGE_VI,
                citations=[],
                rejected_chunk_ids=rejected_chunk_ids,
            )

        cleaned_answer = self._strip_rejected_claims_from_answer(
            answer=generation.answer,
            verified_ids_lower=verified_ids_lower,
            rejected_ids_lower=rejected_seen_lower,
        )

        if not cleaned_answer:
            return VerificationResult(
                found=False,
                answer=ABSTENTION_MESSAGE_VI,
                citations=[],
                rejected_chunk_ids=rejected_chunk_ids,
            )

        # Ensure every verified citation appears inline as [chunk_id] in cleaned_answer
        for citation in verified_citations:
            tag = f"[{citation.chunk_id}]"
            if tag.lower() not in cleaned_answer.lower():
                cleaned_answer = f"{cleaned_answer} {tag}"

        return VerificationResult(
            found=True,
            answer=cleaned_answer,
            citations=verified_citations,
            rejected_chunk_ids=rejected_chunk_ids,
        )
