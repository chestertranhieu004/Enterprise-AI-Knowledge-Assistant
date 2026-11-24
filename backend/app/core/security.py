# File: backend/app/core/security.py
import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings

try:
    import bcrypt as _bcrypt
except ImportError:  # pragma: no cover
    _bcrypt = None

try:
    import jwt as _pyjwt
except ImportError:  # pragma: no cover
    _pyjwt = None

try:
    from jose import jwt as _jose_jwt
except ImportError:  # pragma: no cover
    _jose_jwt = None


def get_password_hash(password: str) -> str:
    """Hash a plaintext password using bcrypt (with PBKDF2 fallback if bcrypt unavailable)."""
    if _bcrypt is not None:
        salt = _bcrypt.gensalt(rounds=12)
        hashed = _bcrypt.hashpw(password.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    # Deterministic stdlib PBKDF2 fallback
    salt_bytes = uuid.uuid4().hex.encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 100_000)
    return f"pbkdf2_sha256${salt_bytes.decode('utf-8')}${dk.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored bcrypt or PBKDF2 hash."""
    if not plain_password or not hashed_password:
        return False

    if hashed_password.startswith("pbkdf2_sha256$"):
        parts = hashed_password.split("$")
        if len(parts) != 3:
            return False
        _, salt_str, stored_hex = parts
        dk = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode("utf-8"), salt_str.encode("utf-8"), 100_000
        )
        return hmac.compare_digest(dk.hex(), stored_hex)

    if _bcrypt is not None:
        try:
            return _bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed_password.encode("utf-8"),
            )
        except ValueError:
            return False

    return False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def create_access_token(
    subject: str | uuid.UUID,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token containing `sub`, `exp`, `iat`, and optional RBAC claims."""
    now = datetime.now(timezone.utc)
    if expires_delta is not None:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra_claims:
        to_encode.update(extra_claims)

    if _pyjwt is not None:
        token = _pyjwt.encode(
            to_encode,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        return token if isinstance(token, str) else token.decode("utf-8")

    if _jose_jwt is not None:
        return _jose_jwt.encode(
            to_encode,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

    # Pure-Python HS256 fallback
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(to_encode, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token. Raises ValueError on invalid or expired token."""
    if not token:
        raise ValueError("Missing authentication token")

    if _pyjwt is not None:
        try:
            payload = _pyjwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
            return dict(payload)
        except Exception as exc:
            raise ValueError(f"Invalid or expired token: {exc}") from exc

    if _jose_jwt is not None:
        try:
            payload = _jose_jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
            return dict(payload)
        except Exception as exc:
            raise ValueError(f"Invalid or expired token: {exc}") from exc

    # Pure-Python HS256 verification fallback
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT token")
    header_b64, payload_b64, signature_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    actual_sig = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid JWT signature")
    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    exp = payload.get("exp")
    if exp is not None and int(datetime.now(timezone.utc).timestamp()) >= int(exp):
        raise ValueError("Token has expired")
    return dict(payload)
