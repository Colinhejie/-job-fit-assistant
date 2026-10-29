# -*- coding: utf-8 -*-
"""Typed errors for the job-fit-assistant local core.

Every error carries a stable machine code and a process exit code so the host
agent can branch on failure class instead of parsing prose.
"""

from __future__ import annotations


class JfaError(Exception):
    """Base class for all expected failures."""

    code = "E_GENERIC"
    exit_code = 1

    def __init__(self, message: str, *, hint: str = "", detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "detail": self.detail,
            },
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


class UsageError(JfaError):
    """The caller passed arguments the CLI cannot honour."""

    code = "E_USAGE"
    exit_code = 2


class NotFoundError(JfaError):
    """A referenced workspace artifact or source file does not exist."""

    code = "E_NOT_FOUND"
    exit_code = 4


class DataError(JfaError):
    """A stored artifact is corrupt, wrong schema, or internally inconsistent."""

    code = "E_DATA"
    exit_code = 4


class ValidationError(JfaError):
    """Content violates a schema or a rewriting/fact safety rule."""

    code = "E_VALIDATION"
    exit_code = 4


class DependencyMissing(JfaError):
    """An optional or required third-party dependency is not installed."""

    code = "E_DEPENDENCY"
    exit_code = 3


class OcrUnavailable(DependencyMissing):
    """No usable OCR backend. Never downgrade this to a silent empty result."""

    code = "E_OCR_UNAVAILABLE"
    exit_code = 3


class ExportError(JfaError):
    """Document generation or conversion failed."""

    code = "E_EXPORT"
    exit_code = 5


class SafetyViolation(JfaError):
    """A privacy/authorization rule would be broken by this operation."""

    code = "E_SAFETY"
    exit_code = 6


class PausedError(JfaError):
    """The run was intentionally paused (login lost, captcha, rate limit)."""

    code = "E_PAUSED"
    exit_code = 7
