"""Structured JSON logging with PHI redaction, trace ids, and an optional LangSmith hook."""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid

_trace: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

REDACTIONS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}(?!T)\b"), "[DATE]"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "[DATE]"),
    (re.compile(r"\b(?:MRN|SYN)\s*:?\s*\d{5,}\b", re.IGNORECASE), "[MRN]"),
    (re.compile(r"\b(my name is|i am|i'm|this is)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", re.IGNORECASE), r"\1 [NAME]"),
]


def redact(text: str) -> str:
    for pattern, repl in REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:16]
    _trace.set(tid)
    return tid


def set_trace_id(tid: str) -> None:
    _trace.set(tid)


def current_trace_id() -> str:
    return _trace.get()


def _redact_obj(o):
    if isinstance(o, str):
        return redact(o)
    if isinstance(o, dict):
        return {k: _redact_obj(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_redact_obj(v) for v in o]
    return o


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": round(time.time(), 3), "level": record.levelname, "logger": record.name,
                   "trace_id": current_trace_id(), "msg": redact(record.getMessage())}
        for key in ("audit", "turn", "http"):
            if hasattr(record, key):
                payload[key] = _redact_obj(getattr(record, key))
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", level))


def traceable(name: str):
    """Wrap with LangSmith tracing if LANGSMITH_API_KEY is set and langsmith is installed."""
    def deco(fn):
        if not os.getenv("LANGSMITH_API_KEY"):
            return fn
        try:
            from langsmith import traceable as ls_traceable  # type: ignore
        except ImportError:
            return fn
        return ls_traceable(name=name)(fn)
    return deco
