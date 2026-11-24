# File: backend/app/rag/providers/mock_provider.py
import hashlib
import math
import re
import unicodedata
from typing import Final

from app.rag.prompts import is_prompt_injection, sanitize_user_query
from app.rag.providers.base import (
    BaseEmbeddingProvider,
    BaseLLMProvider,
    RawCitationCandidate,
    RawLLMGeneration,
    RetrievedChunk,
    l2_normalize,
)
from app.schemas.common import ABSTENTION_MESSAGE_VI

# Conversational & functional stopwords stripped before lexical feature hashing
STOPWORDS_VI_EN: Final[frozenset[str]] = frozenset(
    {
        "của",
        "cua",
        "như",
        "nhu",
        "thế",
        "the",
        "nào",
        "nao",
        "là",
        "la",
        "gì",
        "gi",
        "bao",
        "nhiêu",
        "nhieu",
        "ở",
        "o",
        "đâu",
        "dau",
        "khi",
        "sao",
        "vậy",
        "vay",
        "có",
        "co",
        "được",
        "duoc",
        "không",
        "khong",
        "cho",
        "hỏi",
        "hoi",
        "về",
        "ve",
        "các",
        "cac",
        "những",
        "nhung",
        "một",
        "mot",
        "và",
        "va",
        "hoặc",
        "hoac",
        "trong",
        "tại",
        "tai",
        "với",
        "voi",
        "đến",
        "den",
        "từ",
        "tu",
        "theo",
        "để",
        "de",
        "này",
        "nay",
        "đó",
        "do",
        "thì",
        "thi",
        "mà",
        "ma",
        "bị",
        "bi",
        "bởi",
        "boi",
        "vào",
        "vao",
        "ra",
        "lên",
        "len",
        "xuống",
        "xuong",
        "rằng",
        "rang",
        "nếu",
        "neu",
        "vì",
        "vi",
        "nhưng",
        "nhung",
        "cũng",
        "cung",
        "đã",
        "da",
        "đang",
        "dang",
        "sẽ",
        "se",
        "vẫn",
        "van",
        "còn",
        "con",
        "rất",
        "rat",
        "quá",
        "qua",
        "lắm",
        "lam",
        "nhất",
        "nhat",
        "hơn",
        "hon",
        "mỗi",
        "moi",
        "mọi",
        "tất",
        "tat",
        "cả",
        "ca",
        "ai",
        "ạ",
        "a",
        "nhé",
        "nhe",
        "nhỉ",
        "nhi",
        "ơi",
        "oi",
        "xin",
        "vui",
        "lòng",
        "long",
        "tôi",
        "toi",
        "bạn",
        "ban",
        "chúng",
        "chung",
        "ta",
        "what",
        "how",
        "when",
        "where",
        "who",
        "why",
        "which",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "an",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "about",
        "as",
        "into",
        "can",
        "could",
        "would",
        "should",
        "do",
        "does",
        "did",
        "please",
        "tell",
        "me",
    }
)

# High-frequency generic enterprise terms down-weighted so unrelated policies stay orthogonal
LOW_SPECIFICITY_WORDS: Final[frozenset[str]] = frozenset(
    {
        "công",
        "cong",
        "ty",
        "doanh",
        "nghiệp",
        "nghiep",
        "nhân",
        "nhan",
        "viên",
        "vien",
        "quy",
        "định",
        "dinh",
        "chính",
        "chinh",
        "sách",
        "sach",
        "hướng",
        "huong",
        "dẫn",
        "dan",
        "tài",
        "tai",
        "liệu",
        "lieu",
        "năm",
        "nam",
        "ngày",
        "ngay",
        "tháng",
        "thang",
        "phòng",
        "phong",
        "ban",
        "khối",
        "khoi",
        "hệ",
        "he",
        "thống",
        "thong",
        "bảng",
        "bang",
        "mức",
        "muc",
        "cấp",
        "cap",
        "chung",
        "nội",
        "noi",
        "bộ",
        "bo",
        "company",
        "enterprise",
        "employee",
        "policy",
        "document",
        "system",
        "general",
    }
)

# 20 Orthogonal Semantic Concept Clusters (each occupies 16 dimensions in [0..320))
SEMANTIC_CONCEPT_CLUSTERS: Final[list[tuple[str, tuple[str, ...]]]] = [
    (
        "leave_policy",
        (
            "nghỉ phép",
            "nghi phep",
            "phép năm",
            "phep nam",
            "ngày phép",
            "ngay phep",
            "phép thâm niên",
            "phep tham nien",
            "hưởng nguyên lương",
            "huong nguyen luong",
            "xin nghỉ",
            "xin nghi",
            "nghỉ ốm",
            "nghi om",
            "annual leave",
            "paid leave",
            "vacation",
            "time off",
        ),
    ),
    (
        "working_hours_hybrid",
        (
            "thời gian làm việc",
            "thoi gian lam viec",
            "giờ làm việc",
            "gio lam viec",
            "làm việc tiêu chuẩn",
            "lam viec tieu chuan",
            "làm việc linh hoạt",
            "lam viec linh hoat",
            "hybrid",
            "remote",
            "08:30",
            "17:30",
            "thứ hai đến thứ sáu",
            "thu hai den thu sau",
            "working hours",
            "work from home",
        ),
    ),
    (
        "executive_compensation_salary",
        (
            "lương thưởng",
            "luong thuong",
            "khung lương",
            "khung luong",
            "quỹ thưởng",
            "quy thuong",
            "đãi ngộ cấp cao",
            "dai ngo cap cao",
            "giám đốc khối",
            "giam doc khoi",
            "director",
            "executive bonus",
            "120.000.000",
            "180.000.000",
            "lương cơ bản",
            "luong co ban",
            "bang_luong",
            "bảng lương",
            "mức lương",
            "muc luong",
            "salary",
            "compensation",
            "bonus pool",
        ),
    ),
    (
        "engineering_cicd_stack",
        (
            "triển khai hệ thống",
            "trien khai he thong",
            "ci/cd",
            "cicd",
            "pipeline ci",
            "microservices",
            "fastapi",
            "postgresql",
            "pgvector",
            "pytest",
            "độ phủ",
            "do phu",
            "80%",
            "merge",
            "nhánh main",
            "nhanh main",
            "python 3.11",
        ),
    ),
    (
        "incident_response_oncall",
        (
            "ứng cứu sự cố",
            "ung cuu su co",
            "xử lý sự cố",
            "xu ly su co",
            "sự cố mức độ p1",
            "su co muc do p1",
            "sự cố production",
            "su co production",
            "on-call",
            "oncall",
            "rollback",
            "container",
            "lỗi 5xx",
            "loi 5xx",
            "15 phút",
            "15 phut",
            "incident",
            "outage",
        ),
    ),
    (
        "finance_budget_accounting",
        (
            "ngân sách",
            "ngan sach",
            "báo cáo tài chính",
            "bao cao tai chinh",
            "chi phí",
            "chi phi",
            "kế toán",
            "ke toan",
            "thanh toán",
            "thanh toan",
            "hóa đơn",
            "hoa don",
            "kiểm toán",
            "kiem toan",
            "hoàn ứng",
            "hoan ung",
            "công tác phí",
            "cong tac phi",
            "budget",
            "financial report",
            "expense",
        ),
    ),
    (
        "legal_contract_compliance",
        (
            "pháp chế",
            "phap che",
            "hợp đồng",
            "hop dong",
            "tuân thủ",
            "tuan thu",
            "pháp lý",
            "phap ly",
            "nda",
            "cam kết bảo mật",
            "cam ket bao mat",
            "sở hữu trí tuệ",
            "so huu tri tue",
            "tranh chấp",
            "tranh chap",
            "legal",
            "compliance",
            "contract",
        ),
    ),
    (
        "security_infosec_rbac",
        (
            "bảo mật thông tin",
            "bao mat thong tin",
            "phân quyền",
            "phan quyen",
            "quyền truy cập",
            "quyen truy cap",
            "xác thực",
            "xac thuc",
            "mật khẩu",
            "mat khau",
            "an toàn thông tin",
            "an toan thong tin",
            "access control",
            "Zero Trust",
        ),
    ),
    (
        "recruitment_onboarding",
        (
            "tuyển dụng",
            "tuyen dung",
            "thử việc",
            "thu viec",
            "hội nhập",
            "hoi nhap",
            "onboarding",
            "ứng viên",
            "ung vien",
            "phỏng vấn",
            "phong van",
            "đào tạo nhân viên",
            "dao tao nhan vien",
        ),
    ),
    (
        "insurance_healthcare_benefits",
        (
            "bảo hiểm",
            "bao hiem",
            "bhxh",
            "bhyt",
            "khám sức khỏe",
            "kham suc khoe",
            "thai sản",
            "thai san",
            "phúc lợi",
            "phuc loi",
            "health insurance",
        ),
    ),
    (
        "it_equipment_assets",
        (
            "cấp phát thiết bị",
            "cap phat thiet bi",
            "laptop",
            "máy tính",
            "may tinh",
            "tài sản công ty",
            "tai san cong ty",
            "hỗ trợ it",
            "ho tro it",
            "vpn",
        ),
    ),
    (
        "performance_review_kpi",
        (
            "đánh giá hiệu quả",
            "danh gia hieu qua",
            "kpi",
            "okr",
            "thăng tiến",
            "thang tien",
            "performance review",
            "đánh giá định kỳ",
            "danh gia dinh ky",
        ),
    ),
]


def _strip_diacritics(text: str) -> str:
    """Convert Vietnamese text with diacritics into plain ASCII lowercase."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped).lower()


def _tokenize_words(text: str) -> list[str]:
    """Extract lowercase alphanumeric/Vietnamese word tokens."""
    normalized = unicodedata.normalize("NFC", text.lower())
    return re.findall(r"[0-9a-zA-Z\u00C0-\u1EF9]+(?:[./_-][0-9a-zA-Z\u00C0-\u1EF9]+)*", normalized)


def _hash_feature_to_bucket(feature: str, start_dim: int, span_dim: int, slot: int = 0) -> tuple[int, float]:
    """Deterministically map a string feature into (dimension_index, sign) within [start_dim, start_dim + span_dim)."""
    digest = hashlib.blake2b(f"{slot}:{feature}".encode("utf-8"), digest_size=8).digest()
    raw_idx = int.from_bytes(digest[:4], "big")
    sign = 1.0 if (digest[4] & 1) == 0 else -1.0
    return start_dim + (raw_idx % span_dim), sign


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """
    Deterministic Vietnamese-aware semantic + lexical feature-hashed 768-dim Embedding Provider.

    Vector layout (768 dimensions total):
    - Dimensions [0..384): Orthogonal semantic concept subspace (16 dims per concept cluster).
    - Dimensions [384..768): Lexical content token, non-diacritic token, bigram, and character
      n-gram feature hashing with opening-topic boost and stopword elimination.
    """

    provider_name: str = "mock"
    model_name: str = "mock-768"
    dimension: int = 768

    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension

    def _compute_single_embedding(self, text: str) -> list[float]:
        dim = self.dimension
        cleaned = sanitize_user_query(text)
        if not cleaned:
            empty_vec = [0.0] * dim
            empty_vec[0] = 1.0
            return empty_vec

        lower_text = cleaned.lower()
        ascii_text = _strip_diacritics(lower_text)

        concept_span = min(384, dim // 2)
        lexical_start = concept_span
        lexical_span = dim - lexical_start

        concept_vec = [0.0] * concept_span
        lexical_vec = [0.0] * lexical_span

        # 1. Semantic Concept Subspace Projection ([0..concept_span))
        matched_concepts = 0
        for cluster_idx, (cluster_name, triggers) in enumerate(SEMANTIC_CONCEPT_CLUSTERS):
            hit_score = 0.0
            for phrase in triggers:
                if phrase in lower_text or phrase in ascii_text:
                    # Multi-word phrases carry stronger specificity than single keywords
                    word_count = len(phrase.split())
                    hit_score += 2.2 if word_count >= 2 else 1.2
            if hit_score > 0.0:
                matched_concepts += 1
                block_size = 16
                block_start = (cluster_idx * block_size) % concept_span
                # Project concept activation into its dedicated orthogonal 16-dim block
                saturated_score = 1.0 + math.log1p(hit_score)
                for offset in range(block_size):
                    idx = (block_start + offset) % concept_span
                    _, sign = _hash_feature_to_bucket(
                        f"concept:{cluster_name}:{offset}", 0, concept_span, offset
                    )
                    concept_vec[idx] += sign * saturated_score

        # 2. Content Token & Bigram Lexical Subspace Projection ([lexical_start..dim))
        raw_tokens = _tokenize_words(lower_text)
        content_tokens: list[tuple[str, str, float]] = []
        content_position = 0

        for tok in raw_tokens:
            ascii_tok = _strip_diacritics(tok)
            if tok in STOPWORDS_VI_EN or ascii_tok in STOPWORDS_VI_EN:
                continue
            if len(tok) <= 1 and not tok.isdigit():
                continue

            if tok in LOW_SPECIFICITY_WORDS or ascii_tok in LOW_SPECIFICITY_WORDS:
                base_weight = 0.12
            else:
                # Opening topic tokens (first 8 specific content tokens) get higher salience weight
                # so long paragraphs remain tightly aligned with short queries on their main topic.
                if content_position < 8:
                    base_weight = 3.2
                elif content_position < 16:
                    base_weight = 1.8
                else:
                    base_weight = 0.75
                content_position += 1

            content_tokens.append((tok, ascii_tok, base_weight))

        # Aggregate sublinear TF weights per unique content token
        token_weights: dict[str, float] = {}
        ascii_weights: dict[str, float] = {}
        for tok, ascii_tok, w in content_tokens:
            token_weights[tok] = token_weights.get(tok, 0.0) + w
            if ascii_tok:
                ascii_weights[ascii_tok] = ascii_weights.get(ascii_tok, 0.0) + (w * 0.85)

        for tok, total_w in token_weights.items():
            eff_w = 1.0 + math.log1p(total_w) if total_w >= 1.0 else total_w
            for slot in range(3):
                idx, sign = _hash_feature_to_bucket(f"tok:{tok}", 0, lexical_span, slot)
                lexical_vec[idx] += sign * eff_w

            # Character 4-gram projection for morphological / compound resilience
            if len(tok) >= 4 and total_w >= 0.5:
                for i in range(len(tok) - 3):
                    ngram = tok[i : i + 4]
                    idx, sign = _hash_feature_to_bucket(f"chr:{ngram}", 0, lexical_span, 0)
                    lexical_vec[idx] += sign * (eff_w * 0.25)

        for ascii_tok, total_w in ascii_weights.items():
            eff_w = 1.0 + math.log1p(total_w) if total_w >= 1.0 else total_w
            for slot in range(2):
                idx, sign = _hash_feature_to_bucket(f"asc:{ascii_tok}", 0, lexical_span, slot)
                lexical_vec[idx] += sign * (eff_w * 0.85)

        # Bigrams of specific content tokens (excluding low-specificity generic terms)
        specific_tokens = [
            (tok, ascii_tok, w)
            for tok, ascii_tok, w in content_tokens
            if w >= 0.5
        ]
        for i in range(len(specific_tokens) - 1):
            t1, a1, w1 = specific_tokens[i]
            t2, a2, w2 = specific_tokens[i + 1]
            bigram_w = math.sqrt(w1 * w2) * 1.15
            for feat in (f"bi:{t1}_{t2}", f"abi:{a1}_{a2}"):
                idx, sign = _hash_feature_to_bucket(feat, 0, lexical_span, 0)
                lexical_vec[idx] += sign * bigram_w

        # 3. Subspace Normalization & Blending
        concept_norm = math.sqrt(sum(v * v for v in concept_vec))
        lexical_norm = math.sqrt(sum(v * v for v in lexical_vec))

        if concept_norm > 1e-9 and lexical_norm > 1e-9:
            # Blend 76% concept energy + 24% lexical energy (sqrt(0.76) ≈ 0.8718, sqrt(0.24) ≈ 0.4899)
            c_scale = math.sqrt(0.76) / concept_norm
            l_scale = math.sqrt(0.24) / lexical_norm
            combined = [v * c_scale for v in concept_vec] + [v * l_scale for v in lexical_vec]
        elif concept_norm > 1e-9:
            c_scale = 1.0 / concept_norm
            combined = [v * c_scale for v in concept_vec] + [0.0] * lexical_span
        elif lexical_norm > 1e-9:
            l_scale = 1.0 / lexical_norm
            combined = [0.0] * concept_span + [v * l_scale for v in lexical_vec]
        else:
            # Fallback for purely generic/stopword strings
            idx, sign = _hash_feature_to_bucket(lower_text, 0, lexical_span, 0)
            lexical_vec[idx] = sign
            combined = [0.0] * concept_span + lexical_vec

        return l2_normalize(combined, target_dim=dim)

    def embed_query(self, text: str) -> list[float]:
        """Returns a 768-dimensional L2-normalized float vector for a query string."""
        return self._compute_single_embedding(text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Returns a list of 768-dimensional L2-normalized float vectors for batch document chunks."""
        return [self._compute_single_embedding(t) for t in texts]


class MockLLMProvider(BaseLLMProvider):
    """
    Deterministic Grounded Vietnamese LLM Provider for offline development and CI testing.
    Strictly cites only retrieved authorized chunk IDs and extracts verbatim quotes from chunk.content.
    """

    provider_name: str = "mock"
    model_name: str = "mock-grounded-vi"

    @staticmethod
    def _extract_best_verbatim_quote(query: str, content: str) -> str:
        """
        Select the sentence or clause from `content` with highest lexical overlap with `query`.
        Always returns an exact verbatim substring of `content`.
        """
        cleaned_content = (content or "").strip()
        if not cleaned_content:
            return ""

        query_tokens = {
            _strip_diacritics(t)
            for t in _tokenize_words(query)
            if t not in STOPWORDS_VI_EN and _strip_diacritics(t) not in STOPWORDS_VI_EN
        }

        # Split into candidate sentences while keeping exact substring slices
        sentence_spans: list[str] = []
        for match in re.finditer(r"[^.!?\n]+[.!?]?", cleaned_content):
            span = match.group(0).strip()
            if len(span) >= 15:
                sentence_spans.append(span)

        if not sentence_spans:
            return cleaned_content

        best_span = sentence_spans[0]
        best_score = -1.0
        for span in sentence_spans:
            span_tokens = {_strip_diacritics(t) for t in _tokenize_words(span)}
            overlap = len(query_tokens & span_tokens)
            # Prefer informative sentences containing query terms
            score = overlap * 2.0 + min(len(span), 200) / 200.0
            if score > best_score:
                best_score = score
                best_span = span

        # If content is short enough (< 320 chars), returning full content or top 2 sentences gives rich context
        if len(cleaned_content) <= 320:
            return cleaned_content
        return best_span

    def generate_grounded_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> RawLLMGeneration:
        """
        Synthesize a grounded Vietnamese answer citing [chunk_id] with verbatim quotes
        from authorized chunks, or abstain if prompt-injection / empty chunks are detected.
        """
        if is_prompt_injection(query) or not chunks:
            return RawLLMGeneration(
                answer=ABSTENTION_MESSAGE_VI,
                abstained=True,
                citations=[],
                suggested_queries=self.suggest_rephrased_queries(query),
            )

        sorted_chunks = sorted(chunks, key=lambda c: c.score, reverse=True)
        top_chunk = sorted_chunks[0]

        selected_chunks: list[RetrievedChunk] = [top_chunk]
        if len(sorted_chunks) > 1 and (top_chunk.score - sorted_chunks[1].score) <= 0.10:
            selected_chunks.append(sorted_chunks[1])

        answer_parts: list[str] = []
        citations: list[RawCitationCandidate] = []

        for chunk in selected_chunks:
            quote = self._extract_best_verbatim_quote(query, chunk.content)
            if not quote:
                continue
            citations.append(
                RawCitationCandidate(
                    chunk_id=chunk.chunk_id,
                    quote=quote,
                )
            )
            answer_parts.append(
                f"Theo tài liệu **{chunk.title}** ({chunk.page_or_sheet}): {quote} [{chunk.chunk_id}]."
            )

        if not citations:
            return RawLLMGeneration(
                answer=ABSTENTION_MESSAGE_VI,
                abstained=True,
                citations=[],
                suggested_queries=self.suggest_rephrased_queries(query),
            )

        full_answer = "\n\n".join(answer_parts)
        return RawLLMGeneration(
            answer=full_answer,
            abstained=False,
            citations=citations,
            suggested_queries=self.suggest_rephrased_queries(query),
        )

    def suggest_rephrased_queries(
        self,
        query: str,
    ) -> list[str]:
        """
        Return 3 safe Vietnamese suggested query rephrasings for Scenario 1 (or follow-ups).
        Guarantees zero leakage of restricted document titles or confidential content.
        """
        cleaned = sanitize_user_query(query)
        ascii_q = _strip_diacritics(cleaned.lower())

        if is_prompt_injection(cleaned):
            return [
                "Quy định thời gian làm việc tiêu chuẩn và làm việc linh hoạt (hybrid) của công ty là gì?",
                "Chính sách nghỉ phép năm của nhân viên chính thức được quy định như thế nào?",
                "Tiêu chuẩn triển khai kỹ thuật hoặc quy trình làm việc của phòng ban bạn là gì?",
            ]

        if any(k in ascii_q for k in ("nghi phep", "phep nam", "ngay phep", "nghi om", "leave")):
            return [
                "Nhân viên chính thức được nghỉ bao nhiêu ngày phép năm hưởng nguyên lương?",
                "Quy định cộng thêm ngày phép theo thâm niên làm việc tại công ty như thế nào?",
                "Thủ tục đăng ký đơn xin nghỉ phép trên hệ thống HR cần thực hiện trước bao nhiêu ngày?",
            ]

        if any(k in ascii_q for k in ("thoi gian", "gio lam", "hybrid", "lam viec")):
            return [
                "Khung giờ làm việc tiêu chuẩn từ thứ Hai đến thứ Sáu của công ty là như thế nào?",
                "Chính sách làm việc linh hoạt (hybrid) cho phép tối đa bao nhiêu ngày mỗi tuần?",
                "Quy chế nghỉ phép năm và chế độ làm việc chung của công ty quy định ra sao?",
            ]

        if any(k in ascii_q for k in ("ky thuat", "ci/cd", "fastapi", "pgvector", "su co", "p1", "rollback")):
            return [
                "Tiêu chuẩn triển khai hệ thống backend và độ phủ kiểm thử pytest tối thiểu là bao nhiêu?",
                "Quy trình ứng cứu sự cố mức độ P1 và thời gian phản hồi on-call của khối Engineering là gì?",
                "Khi nào kỹ sư trực on-call cần thực hiện rollback container trên môi trường production?",
            ]

        # General safe rephrasings without echoing adversarial or sensitive substrings
        topic_words = [
            tok
            for tok in _tokenize_words(cleaned)
            if tok not in STOPWORDS_VI_EN and len(tok) > 1
        ][:5]
        topic_phrase = " ".join(topic_words) if topic_words else "quy chế nội bộ"

        return [
            f"Quy định chung của công ty liên quan đến {topic_phrase} được áp dụng như thế nào?",
            "Chính sách thời gian làm việc và nghỉ phép năm tiêu chuẩn dành cho nhân viên là gì?",
            "Hướng dẫn quy trình nghiệp vụ dành cho phòng ban của tôi bao gồm những nội dung nào?",
        ]
