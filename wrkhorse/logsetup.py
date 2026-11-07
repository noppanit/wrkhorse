"""Stderr logging that redacts known secrets."""

from __future__ import annotations

import logging
import os
import sys

_SECRET_ENV = ("WRKHORSE_AUTH_TOKEN", "WRKHORSE_INFERENCE_API_KEY", "VLLM_API_KEY")
_CONFIGURED = False


class ScrubFilter(logging.Filter):
    """Replace configured secret values before a record is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = [os.environ.get(name, "") for name in _SECRET_ENV]
        secrets = [value for value in secrets if len(value) >= 8]
        if not secrets:
            return True
        message = record.getMessage()
        redacted = message
        for secret in secrets:
            redacted = redacted.replace(secret, "[redacted]")
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(ScrubFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("mcp", "uvicorn", "httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
