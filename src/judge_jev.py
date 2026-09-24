"""Alias pointing to judge.py for backward compatibility."""

from generate import http_post_json
from judge import (
    DEFAULT_BASE,
    DEFAULT_MODEL,
    TIMEOUT,
    Judge,
    JevJudge,
    FallbackJudge,
    jev_configured,
)

__all__ = [
    "DEFAULT_BASE",
    "DEFAULT_MODEL",
    "TIMEOUT",
    "Judge",
    "JevJudge",
    "FallbackJudge",
    "jev_configured",
    "http_post_json",
]
