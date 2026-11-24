# File: backend/app/core/exceptions.py
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppException(Exception):
    """Base application exception with structured status_code and error_code."""

    def __init__(
        self,
        detail: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        error_code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code


class UnauthorizedException(AppException):
    def __init__(
        self,
        detail: str = "Invalid or expired authentication credentials",
        error_code: str = "AUTH_UNAUTHORIZED",
    ) -> None:
        super().__init__(
            detail=detail,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=error_code,
        )


class ForbiddenException(AppException):
    def __init__(
        self,
        detail: str = "Insufficient permissions to perform this action",
        error_code: str = "AUTH_FORBIDDEN",
    ) -> None:
        super().__init__(
            detail=detail,
            status_code=status.HTTP_403_FORBIDDEN,
            error_code=error_code,
        )


class NotFoundException(AppException):
    def __init__(
        self,
        detail: str = "Requested resource not found",
        error_code: str = "RESOURCE_NOT_FOUND",
    ) -> None:
        super().__init__(
            detail=detail,
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=error_code,
        )


class ConflictException(AppException):
    def __init__(
        self,
        detail: str = "Resource conflict",
        error_code: str = "RESOURCE_CONFLICT",
    ) -> None:
        super().__init__(
            detail=detail,
            status_code=status.HTTP_409_CONFLICT,
            error_code=error_code,
        )


class PayloadTooLargeException(AppException):
    def __init__(
        self,
        detail: str = "Uploaded file exceeds maximum allowed size (50MB)",
        error_code: str = "UPLOAD_FILE_TOO_LARGE",
    ) -> None:
        super().__init__(
            detail=detail,
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            error_code=error_code,
        )


def register_exception_handlers(app: FastAPI) -> None:
    """Register structured JSON exception handlers matching ErrorResponse in openapi.yaml."""

    @app.exception_handler(AppException)
    async def handle_app_exception(_request: Request, exc: AppException) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "error_code": exc.error_code},
            headers=headers,
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            detail_msg = str(exc.detail.get("detail", "HTTP error"))
            error_code = exc.detail.get("error_code")
        else:
            detail_msg = str(exc.detail)
            error_code = None
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail_msg, "error_code": error_code},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Request validation failed",
                "error_code": "VALIDATION_ERROR",
                "errors": exc.errors(),
            },
        )
