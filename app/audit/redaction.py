from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|authorization|credential|api[_-]?key", re.I
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+")
_CARD = re.compile(r"(?<!\d)(\d{4})[ -]?\d{4}[ -]?\d{4}[ -]?(\d{4})(?!\d)")


def redact(value: Any) -> Any:
    """Recursively remove credentials and mask payment-card numbers."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _CARD.sub(r"\1-****-****-\2", _BEARER.sub("Bearer [REDACTED]", value))
    return value
