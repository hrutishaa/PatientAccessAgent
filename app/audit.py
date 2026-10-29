"""Append-only audit trail of every PHI-bearing data access (HIPAA 164.312(b) style)."""
from __future__ import annotations

import hashlib
import logging
import time
from collections import deque

from app.observability import current_trace_id

log = logging.getLogger("audit")


def pseudonym(patient_id: str) -> str:
    return "p_" + hashlib.sha256(patient_id.encode()).hexdigest()[:12]


class AuditLog:
    def __init__(self, maxlen: int = 10_000) -> None:
        self.events: deque[dict] = deque(maxlen=maxlen)

    def record(self, actor: str, action: str, resource: str, resource_id: str | None,
               patient_ids: list[str]) -> dict:
        event = {
            "ts": time.time(), "trace_id": current_trace_id(), "actor": actor,
            "action": action, "resource": resource, "resource_id": pseudonym(resource_id) if resource == "Patient" and resource_id else resource_id,
            "patients": [pseudonym(p) for p in patient_ids],
        }
        self.events.append(event)
        log.info("audit", extra={"audit": event})
        return event

    def clear(self) -> None:
        self.events.clear()


audit = AuditLog()
