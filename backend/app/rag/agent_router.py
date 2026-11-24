# File: backend/app/rag/agent_router.py
import re
import unicodedata
from typing import Final

from app.rag.prompts import is_prompt_injection, sanitize_user_query


def _strip_diacritics_lower(text: str) -> str:
    """Normalize Vietnamese text into lowercase ASCII without diacritics."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped).lower()


# Any enterprise, factual, policy, security, or out-of-domain knowledge cue forces RAG routing (None)
KNOWLEDGE_OR_FACTUAL_BLOCKLIST: Final[tuple[str, ...]] = (
    # Enterprise / policy / HR / Finance / Legal / Engineering keywords
    "nghi phep",
    "phep nam",
    "ngay phep",
    "xin nghi",
    "xin off",
    "nghi om",
    "nghi co luong",
    "ngay co luong",
    "luong",
    "thuong",
    "dai ngo",
    "thu nhap",
    "hop dong",
    "phap che",
    "phap ly",
    "nda",
    "thanh toan",
    "phe duyet",
    "duyet chi",
    "hoa don",
    "tam ung",
    "ngan sach",
    "chi phi",
    "ke toan",
    "cong tac",
    "bao hiem",
    "quy trinh",
    "chinh sach",
    "quy dinh",
    "quy che",
    "tai lieu",
    "ho so",
    "bao cao",
    "thoi gian lam viec",
    "gio lam viec",
    "tan lam",
    "may gio",
    "lam viec tu xa",
    "lam tu xa",
    "hybrid",
    "wfh",
    "remote",
    "su co",
    "loi he thong",
    "sap web",
    "web sap",
    "web chet",
    "server loi",
    "rollback",
    "on-call",
    "oncall",
    "production",
    "ci/cd",
    "cicd",
    "pytest",
    "fastapi",
    "postgresql",
    "pgvector",
    "trien khai",
    "ky thuat",
    "giam doc",
    "director",
    "nhan su",
    "tai chinh",
    "engineering",
    "legal",
    "finance",
    "tai lieu mat",
    "bi mat",
    "mat khau",
    "phan quyen",
    "rbac",
    "abac",
    "restricted",
    "kpi",
    "okr",
    "tuyen dung",
    "thu viec",
    "laptop",
    "vpn",
    "file",
    "pdf",
    "docx",
    "xlsx",
    "salary",
    "leave",
    "vacation",
    "contract",
    "payment",
    "budget",
    "policy",
    "document",
    "database",
    # Out-of-domain factual / trivia / follow-up cues that MUST hit RAG (or Scenario 1 abstention)
    "cong thuc",
    "pho bo",
    "nau pho",
    "sao hoa",
    "bitcoin",
    "co phieu",
    "gia vang",
    "ten lua",
    "hat nhan",
    "world cup",
    "cau thu",
    "bong da",
    "thoi tiet",
    "du bao",
    "lich su",
    "bao nhieu",
    "may ngay",
    "chi tiet",
    "ap dung",
    "thu tuc",
    "dieu kien",
    "han muc",
    "muc toi da",
    "select ",
    "union ",
    "drop table",
    "or 1=1",
)

# Exact / anchored conversational patterns checked ONLY after safety & knowledge guards pass
INTENT_PATTERNS: Final[tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]] = (
    (
        "capabilities",
        tuple(
            re.compile(p, re.IGNORECASE)
            for p in (
                r"\bban\s+(co\s+the\s+)?giup\s+(duoc\s+)?gi\b",
                r"\bban\s+lam\s+duoc\s+gi\b",
                r"\bban\s+ho\s+tro\s+(duoc\s+)?gi\b",
                r"\bco\s+the\s+(lam|giup|ho\s+tro)\s+(duoc\s+)?gi\b",
                r"\bgiup\s+(toi|minh|em)\s+voi\b",
                r"\bhuong\s+dan\s+su\s+dung\b",
                r"\bwhat\s+can\s+you\s+do\b",
                r"\bhow\s+can\s+you\s+help\b",
                r"^(can\s+you\s+)?help(\s+me)?$",
            )
        ),
    ),
    (
        "identity",
        tuple(
            re.compile(p, re.IGNORECASE)
            for p in (
                r"\b(ban|cau|em|anh|bot|ai)\s+la\s+ai\b",
                r"\bten\s+(cua\s+)?ban\s+la\s+gi\b",
                r"\bban\s+ten\s+(la\s+)?gi\b",
                r"\bgioi\s+thieu\s+(ve\s+)?(ban|ban\s+than)\b",
                r"\bwho\s+are\s+you\b",
                r"\bwhat\s+is\s+your\s+name\b",
                r"\bwhat\s+are\s+you\b",
            )
        ),
    ),
    (
        "farewell",
        tuple(
            re.compile(p, re.IGNORECASE)
            for p in (
                r"\btam\s+biet\b",
                r"\bhen\s+gap\s+lai\b",
                r"\bchuc\s+ngu\s+ngon\b",
                r"\bngu\s+ngon\b",
                r"^(chao\s+)?(bye|goodbye|bye\s+bye|see\s+you(\s+later)?)$",
                r"\b(bye|goodbye|see\s+you)\b",
            )
        ),
    ),
    (
        "thanks",
        tuple(
            re.compile(p, re.IGNORECASE)
            for p in (
                r"\bcam\s+on\b",
                r"\bthank(s|\s+you)?\b",
                r"^(tks|thx|ty)$",
                r"^(ok\s+)?(cam\s+on|rat\s+huu\s+ich|tuyet\s+voi)(\s+nhe|\s+nhieu|\s+ban)?$",
            )
        ),
    ),
    (
        "smalltalk",
        tuple(
            re.compile(p, re.IGNORECASE)
            for p in (
                r"\b(hom\s+nay\s+)?(ban\s+)?(co\s+)?khoe\s+khong\b",
                r"\bhom\s+nay\s+(ban\s+)?the\s+nao\b",
                r"\bdao\s+nay\s+sao\s+roi\b",
                r"\bban\s+dang\s+lam\s+gi\b",
                r"\bhow\s+are\s+you\b",
                r"\bhow\s+is\s+it\s+going\b",
                r"\bwhat'?s\s+up\b",
                r"^(ok|oke|okay|duoc\s+roi|hieu\s+roi|ro\s+roi|vang|da\s+vang|nice|cool|great)$",
            )
        ),
    ),
    (
        "greeting",
        tuple(
            re.compile(p, re.IGNORECASE)
            for p in (
                r"^(xin\s+chao|chao(\s+ban|\s+ai|\s+bot|\s+tro\s+ly|\s+ad|\s+nhe|\s+moi\s+nguoi|\s+buoi\s+sang|\s+buoi\s+chieu|\s+buoi\s+toi)?)$",
                r"^(hello|helo|hi|hey|alo|a\s+lo|good\s+morning|good\s+afternoon|good\s+evening)(\s+(there|bot|ai|ban|everyone|all))?$",
                r"^(xin\s+chao|chao\s+ban|hello|hi|hey|alo)\s+(hom\s+nay\s+the\s+nao|khoe\s+khong|nhe|nha|oi)$",
            )
        ),
    ),
)


def classify_conversational_intent(query: str) -> str | None:
    """
    Classify whether `query` is a pure social/conversational utterance
    ("greeting", "identity", "capabilities", "thanks", "farewell", "smalltalk")
    or a knowledge/RAG query (`None`).

    CRITICAL SAFETY INVARIANTS:
    1. Prompt injection / jailbreak attempts ALWAYS return `None` so they are blocked by RAG security gates.
    2. Queries containing numbers, SQLi characters, or ANY enterprise/policy/factual/out-of-domain keywords
       ALWAYS return `None` so Scenarios 1, 2, and 3 are strictly enforced.
    """
    cleaned = sanitize_user_query(query)
    if not cleaned:
        return None

    # Rule 1: Never treat prompt injection / jailbreak as conversational smalltalk
    if is_prompt_injection(cleaned):
        return None

    # Rule 2: SQLi tokens or numeric digits indicate a substantive/adversarial query -> route to RAG
    if any(ch in cleaned for ch in ("'", '"', ";", "--", "=")) or re.search(r"\d", cleaned):
        return None

    ascii_lower = _strip_diacritics_lower(cleaned)
    # Strip edge punctuation for matching
    plain = re.sub(r"[^\w\s/-]", " ", ascii_lower)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return None

    # Rule 3: Long queries (> 12 words) are substantive questions, not short social utterances
    words = plain.split()
    if len(words) > 12:
        return None

    # Rule 4: Any enterprise, policy, or factual/out-of-domain keyword -> MUST route to RAG
    for blocked_kw in KNOWLEDGE_OR_FACTUAL_BLOCKLIST:
        if blocked_kw in plain:
            return None

    # Rule 5: Match genuine conversational intents in priority order
    for intent_name, patterns in INTENT_PATTERNS:
        for pattern in patterns:
            if pattern.search(plain):
                return intent_name

    return None
