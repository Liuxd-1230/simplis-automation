"""Typed failures used by the CLI and JSON diagnostic reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class V2Error(Exception):
    message: str
    code: str = "v2_error"
    details: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 2

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "details": self.details}}


class ValidationError(V2Error):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, "validation_error", details, 2)


class CatalogError(V2Error):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, "catalog_error", details, 3)


class VerificationError(V2Error):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, "verification_error", details, 4)


class ImportBlockedError(V2Error):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, "import_blocked", details, 5)
