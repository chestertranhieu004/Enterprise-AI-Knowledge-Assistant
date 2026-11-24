# File: backend/app/schemas/auth.py
from pydantic import BaseModel
from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400
    user: UserRead
