# File: tests/__init__.py
"""
Enterprise Knowledge Assistant — Phase 5 QA, Mandatory Scenario Proofs & Red-Team Security Suite.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import re
import sys
import types
from pathlib import Path

_PURE_PACKAGES: frozenset[str] = frozenset(
    {
        "sqlalchemy",
        "pydantic_settings",
        "dotenv",
        "openpyxl",
        "et_xmlfile",
        "fastapi",
        "starlette",
        "anyio",
        "httpx",
        "httpcore",
        "h11",
        "multipart",
        "python_multipart",
        "annotated_doc",
        "pytest",
        "_pytest",
        "py",
        "pluggy",
        "iniconfig",
        "sniffio",
    }
)

_SITE_PACKAGE_DIRS: list[str] = [
    p
    for p in (
        r"C:\Users\hieun\AppData\Local\Programs\Python\Python314\Lib\site-packages",
        r"C:\Users\hieun\AppData\Local\Programs\Python\Python312\Lib\site-packages",
        r"C:\Users\hieun\AppData\Local\Programs\Python\Python39\Lib\site-packages",
    )
    if Path(p).exists()
]


class _CrossEnvPurePackageFinder(importlib.abc.MetaPathFinder):
    """Resolves pure-Python dependencies across local Python installations without C-extension conflicts."""

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> importlib.machinery.ModuleSpec | None:
        top_pkg = fullname.split(".")[0]
        if top_pkg not in _PURE_PACKAGES or "cyextension" in fullname:
            return None
        if path is not None:
            return importlib.machinery.PathFinder.find_spec(fullname, path, target)  # type: ignore[arg-type]
        return importlib.machinery.PathFinder.find_spec(fullname, _SITE_PACKAGE_DIRS, target)  # type: ignore[arg-type]


def bootstrap_test_environment() -> None:
    """Ensure backend path, pure-Python packages, SQLite test URL, and email_validator shim are active."""
    import os

    os.environ.setdefault("DATABASE_URL", "sqlite://")
    os.environ.setdefault("SEED_ON_STARTUP", "false")

    if not any(
        type(f).__name__ == "_CrossEnvPurePackageFinder" for f in sys.meta_path
    ):
        sys.meta_path.append(_CrossEnvPurePackageFinder())

    if "email_validator" not in sys.modules:
        ev_mod = types.ModuleType("email_validator")

        class EmailNotValidError(ValueError):
            pass

        class ValidatedEmail:
            def __init__(self, local_part: str, normalized: str) -> None:
                self.local_part = local_part
                self.normalized = normalized
                self.email = normalized

        def validate_email(
            email: str,
            check_deliverability: bool = False,
        ) -> ValidatedEmail:
            cleaned = (email or "").strip()
            match = re.match(r"^([^@\s]+)@([^@\s]+\.[^@\s]+)$", cleaned)
            if not match:
                raise EmailNotValidError(f"Invalid email address: {email}")
            local, domain = match.group(1), match.group(2).lower()
            return ValidatedEmail(local, f"{local}@{domain}")

        ev_mod.EmailNotValidError = EmailNotValidError  # type: ignore[attr-defined]
        ev_mod.ValidatedEmail = ValidatedEmail  # type: ignore[attr-defined]
        ev_mod.validate_email = validate_email  # type: ignore[attr-defined]
        sys.modules["email_validator"] = ev_mod

        try:
            import pydantic.networks

            pydantic.networks.email_validator = ev_mod  # type: ignore[attr-defined]
            pydantic.networks.import_email_validator = lambda: None  # type: ignore[assignment]
        except Exception:
            pass

    repo_root = Path(__file__).resolve().parent.parent
    candidate_backends = [
        repo_root / "backend",
        Path(r"C:\Users\hieun\AppData\Local\Temp\enterprise-knowledge-assistant\backend"),
        Path(r"d:\enterprise-knowledge-assistant\backend"),
    ]
    for backend_dir in candidate_backends:
        try:
            if backend_dir.exists() and str(backend_dir) not in sys.path:
                sys.path.insert(0, str(backend_dir))
        except PermissionError:
            continue


bootstrap_test_environment()
